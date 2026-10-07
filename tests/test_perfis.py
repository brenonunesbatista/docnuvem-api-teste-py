"""Gerenciador de perfis: edições vão para o config.json e nunca para o git."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .conftest import TOKEN_OK, App, Upstream


def _arquivo(app: App) -> Path:
    assert app.servidor.arquivo_config is not None
    return app.servidor.arquivo_config


def _gravado(app: App) -> dict[str, Any]:
    return dict(json.loads(_arquivo(app).read_text(encoding="utf-8")))


def _salvar(app: App, **campos: str) -> Any:
    corpo = {"nome": "novo", "instancia": "Novo", "baseUrl": "http://api:8083", "token": "tk-1234"}
    return app.http.post("/_config/perfil", json={**corpo, **campos})


def test_perfis_informa_que_e_editavel(app_cfg: App, app: App) -> None:
    dados = app_cfg.http.get("/_perfis").json()
    assert dados["editavel"] is True
    assert dados["arquivoConfig"].endswith("config.json")
    assert app.http.get("/_perfis").json()["editavel"] is False  # sem arquivo: só leitura


def test_sem_arquivo_de_config_nao_edita(app: App) -> None:
    r = _salvar(app)
    assert r.status_code == 400
    assert "sem arquivo de configuração" in r.json()["erro"]


def test_criar_perfil_grava_no_config_json(app_cfg: App) -> None:
    r = _salvar(app_cfg, protecao="bloquear")
    assert r.json() == {"ok": True}
    perfil = _gravado(app_cfg)["perfis"]["novo"]
    assert perfil == {
        "instancia": "Novo",
        "baseUrl": "http://api:8083",
        "token": "tk-1234",
        "protegido": "bloquear",
    }
    por_id = {p["id"]: p for p in app_cfg.http.get("/_perfis").json()["perfis"]}
    assert por_id["novo"]["protegido"] == "bloquear"  # já vale sem reiniciar


def test_token_nunca_volta_para_o_navegador(app_cfg: App) -> None:
    resposta = _salvar(app_cfg, token="SEGREDO-MUITO-LONGO-123")
    assert "SEGREDO-MUITO-LONGO-123" not in resposta.text
    assert "SEGREDO-MUITO-LONGO-123" not in app_cfg.http.get("/_perfis").text


def test_protecao_confirmar_vira_true(app_cfg: App) -> None:
    _salvar(app_cfg, protecao="confirmar")
    assert _gravado(app_cfg)["perfis"]["novo"]["protegido"] is True


def test_editar_sem_token_mantem_o_token_atual(app_cfg: App) -> None:
    r = _salvar(app_cfg, original="b", nome="b", instancia="B2", token="")
    assert r.json() == {"ok": True}
    perfil = _gravado(app_cfg)["perfis"]["b"]
    assert perfil["token"] == "token-b-9999"
    assert perfil["instancia"] == "B2"


def test_editar_com_token_novo_troca_o_token(app_cfg: App) -> None:
    _salvar(app_cfg, original="b", nome="b", token="  novo-token  ")
    assert _gravado(app_cfg)["perfis"]["b"]["token"] == "novo-token"


def test_editar_remove_a_protecao(app_cfg: App) -> None:
    _salvar(app_cfg, protecao="bloquear")
    _salvar(app_cfg, original="novo", token="", protecao="")
    assert "protegido" not in _gravado(app_cfg)["perfis"]["novo"]


def test_renomear_mantem_ordem_e_acompanha_o_padrao(app_cfg: App) -> None:
    _salvar(app_cfg, original="a", nome="principal", instancia="A", token="")
    dados = _gravado(app_cfg)
    assert list(dados["perfis"]) == ["principal", "b"]
    assert dados["perfilPadrao"] == "principal"


def test_editar_preserva_o_resto_do_arquivo(app_cfg: App) -> None:
    _salvar(app_cfg)
    assert _gravado(app_cfg)["extra"] == {"mantido": True}


def test_nome_repetido_e_recusado(app_cfg: App) -> None:
    assert _salvar(app_cfg, nome="b").status_code == 409
    assert _salvar(app_cfg, original="a", nome="b", token="").status_code == 409


def test_editar_perfil_inexistente(app_cfg: App) -> None:
    assert _salvar(app_cfg, original="zzz").status_code == 404


def test_validacoes(app_cfg: App) -> None:
    ruins = [
        {"nome": ""},
        {"nome": "a/b"},
        {"nome": "x" * 41},
        {"nome": "..%2f"},
        {"instancia": "  "},
        {"baseUrl": "ftp://x"},
        {"baseUrl": "api:8083"},
        {"token": ""},
        {"protecao": "talvez"},
    ]
    for campos in ruins:
        r = _salvar(app_cfg, **campos)
        assert r.status_code == 400, campos
        assert r.json()["ok"] is False
    assert list(_gravado(app_cfg)["perfis"]) == ["a", "b"]  # nada foi gravado


def test_campos_que_nao_sao_texto(app_cfg: App) -> None:
    r = app_cfg.http.post("/_config/perfil", json={"nome": 1, "token": ["x"]})
    assert r.status_code == 400


def test_corpo_invalido(app_cfg: App) -> None:
    assert app_cfg.http.post("/_config/perfil", content=b"nao json").status_code == 400
    assert app_cfg.http.post("/_config/perfil", json=[1]).status_code == 400


def test_gravacao_faz_copia_de_seguranca(app_cfg: App) -> None:
    antes = _arquivo(app_cfg).read_text(encoding="utf-8")
    _salvar(app_cfg)
    assert _arquivo(app_cfg).with_name("config.json.bak").read_text(encoding="utf-8") == antes
    assert not _arquivo(app_cfg).with_name("config.json.tmp").exists()


def test_remover_perfil(app_cfg: App) -> None:
    assert app_cfg.http.delete("/_config/perfil/b").json() == {"ok": True}
    assert list(_gravado(app_cfg)["perfis"]) == ["a"]
    assert [p["id"] for p in app_cfg.http.get("/_perfis").json()["perfis"]] == ["a"]


def test_remover_o_padrao_escolhe_outro(app_cfg: App) -> None:
    app_cfg.http.delete("/_config/perfil/a")
    assert _gravado(app_cfg)["perfilPadrao"] == "b"


def test_remover_o_ultimo_perfil_e_recusado(app_cfg: App) -> None:
    app_cfg.http.delete("/_config/perfil/b")
    r = app_cfg.http.delete("/_config/perfil/a")
    assert r.status_code == 400
    assert list(_gravado(app_cfg)["perfis"]) == ["a"]


def test_remover_perfil_inexistente(app_cfg: App) -> None:
    assert app_cfg.http.delete("/_config/perfil/zzz").status_code == 404


def test_definir_padrao(app_cfg: App) -> None:
    assert app_cfg.http.post("/_config/padrao", json={"nome": "b"}).json() == {"ok": True}
    assert _gravado(app_cfg)["perfilPadrao"] == "b"
    assert app_cfg.http.get("/_perfis").json()["padrao"] == "b"
    assert app_cfg.http.post("/_config/padrao", json={"nome": "zzz"}).status_code == 404


def test_perfil_criado_ja_serve_chamadas_com_o_token_novo(app_cfg: App, upstream: Upstream) -> None:
    _salvar(app_cfg, nome="live", instancia="LIVE", baseUrl=upstream.base, token=TOKEN_OK)
    env = app_cfg.http.get("/_proxy/live/api/modelos").json()
    assert env["status"] == 200
    assert upstream.recebidas[-1]["auth"] == f"Bearer {TOKEN_OK}"
    assert "instancia=live" in upstream.recebidas[-1]["path"]


def test_trocar_o_token_limpa_o_cache_de_status(app_cfg: App) -> None:
    assert app_cfg.http.get("/_status/b").json()["nivel"] == "token"  # o token de b não vale
    base = app_cfg.servidor.cfg.perfis["b"].baseUrl
    _salvar(app_cfg, original="b", nome="b", token=TOKEN_OK, baseUrl=base)
    assert app_cfg.http.get("/_status/b").json()["nivel"] == "ok"  # sem esperar o cache


def test_edicao_exige_origem_da_pagina(app_cfg: App) -> None:
    ruim = {"Origin": "http://evil.example"}
    corpo = {"nome": "x", "instancia": "x", "baseUrl": "http://x", "token": "t"}
    assert app_cfg.http.post("/_config/perfil", json=corpo, headers=ruim).status_code == 403
    assert app_cfg.http.post("/_config/padrao", json={"nome": "b"}, headers=ruim).status_code == 403
    assert app_cfg.http.delete("/_config/perfil/b", headers=ruim).status_code == 403
    host = {"Host": "evil.example"}
    assert app_cfg.http.delete("/_config/perfil/b", headers=host).status_code == 403
    assert list(_gravado(app_cfg)["perfis"]) == ["a", "b"]


def test_config_e_copias_ficam_fora_do_git() -> None:
    raiz = Path(__file__).parent.parent
    linhas = (raiz / ".gitignore").read_text(encoding="utf-8").splitlines()
    for arquivo in ("config.json", "config.json.bak", "config.json.tmp"):
        assert arquivo in linhas, f"{arquivo} precisa estar no .gitignore"
