"""Login de usuário por instância: o que é guardado, o que nunca pode vazar."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from docnuvem_tester.config import ConfigError, parse_config
from docnuvem_tester.login_usuario import dispositivo_padrao, extrair_token, sem_segredos

from .conftest import TOKEN_OK, App, Upstream

LOGIN = ("POST", "/api/sync/login")
SENHA = "S3nha-MUITO-secreta!&=?"
TOKEN_USUARIO = "tok-USUARIO-0123456789"
DIRETORIOS = ("GET", "/api/diretorios")
FILHOS = ("POST", "/api/sync/listarDiretoriosFilhos")


def _login_ok(upstream: Upstream, token: str = TOKEN_USUARIO) -> None:
    """A API só aceita a senha certa; devolve o token do dispositivo."""

    def responder(q: dict[str, list[str]]) -> tuple[int, str]:
        if q["senha"][0] != SENHA:
            return 401, '{"retorno": "Usuário ou senha inválidos"}'
        return 200, json.dumps({"token": token, "usuario": q["login"][0]})

    upstream.dinamicas[LOGIN] = responder


def _entrar(app: App, perfil: str = "a", **extra: Any) -> Any:
    corpo = {"perfil": perfil, "login": "ana@cliente.com.br", "senha": SENHA, **extra}
    return app.http.post("/_config/login", json=corpo)


def _config(app: App) -> dict[str, Any]:
    assert app.servidor.arquivo_config is not None
    return dict(json.loads(app.servidor.arquivo_config.read_text(encoding="utf-8")))


def _todo_texto(app: App) -> str:
    """Tudo que o programa grava em disco: config, cópia de segurança e histórico."""
    arq = app.servidor.arquivo_config
    assert arq is not None
    partes = [p.read_text(encoding="utf-8") for p in arq.parent.iterdir() if p.is_file()]
    return "\n".join(partes)


# ------------------------------------------------------------- utilitários ---


@pytest.mark.parametrize(
    ("dado", "esperado"),
    [
        ({"token": "abc"}, "abc"),
        ({"token": "Bearer abc"}, "abc"),
        ({"accessToken": " xyz "}, "xyz"),
        ({"dados": {"tokenDispositivo": "ddd"}}, "ddd"),  # também em campo aninhado
        (
            {"x": "aaaaaaaa.bbbbbbbb.cccccccc"},
            "aaaaaaaa.bbbbbbbb.cccccccc",
        ),  # JWT em qualquer campo
        ({"a": {"b": "aaaaaaaa.bbbbbbbb.cccccccc"}}, "aaaaaaaa.bbbbbbbb.cccccccc"),
        ("Bearer aaaaaaaa.bbbbbbbb.cccccccc", "aaaaaaaa.bbbbbbbb.cccccccc"),
        ("texto qualquer", None),
        ({"mensagem": "ok"}, None),
        (None, None),
    ],
)
def test_extrair_token(dado: Any, esperado: str | None) -> None:
    assert extrair_token(dado) == esperado


def test_sem_segredos_tira_a_senha_da_url_e_do_texto() -> None:
    texto = "POST http://x/api/sync/login?login=ana&senha=abc%21def&instancia=x falhou: abc!def"
    limpo = sem_segredos(texto, "abc!def")
    assert "abc" not in limpo
    assert "senha=***" in limpo and "login=ana" in limpo


def test_dispositivo_e_estavel() -> None:
    assert dispositivo_padrao() == dispositivo_padrao()
    assert dispositivo_padrao().startswith("docnuvem-api-tester-")


# ------------------------------------------------------------------- login ---


def test_login_guarda_so_o_token_e_o_usuario(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    r = _entrar(app_cfg)
    assert r.json() == {"ok": True, "usuario": "ana@cliente.com.br"}
    perfil = _config(app_cfg)["perfis"]["a"]
    assert perfil["tokenUsuario"] == TOKEN_USUARIO
    assert perfil["usuario"] == "ana@cliente.com.br"
    assert perfil["dispositivo"] == dispositivo_padrao()
    assert perfil["token"] == TOKEN_OK  # o token da instância continua como estava
    assert "senha" not in perfil


def test_login_envia_o_que_a_api_pede(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    _entrar(app_cfg)
    (pedido,) = [c for c in upstream.recebidas if c["rota"] == "/api/sync/login"]
    assert pedido["method"] == "POST"
    assert pedido["auth"] is None  # o login não manda nenhum token
    assert "instancia=a" in pedido["path"]  # sempre em minúsculo
    assert "login=ana%40cliente.com.br" in pedido["path"]
    assert f"identificadorDispositivo={dispositivo_padrao()}" in pedido["path"]
    assert "nomeMaquina=" in pedido["path"]


def test_o_mesmo_dispositivo_em_todo_login(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    _entrar(app_cfg, "a")
    _entrar(app_cfg, "b")
    ids = {
        next(p for p in c["path"].split("&") if p.startswith("identificadorDispositivo="))
        for c in upstream.recebidas
        if c["rota"] == "/api/sync/login"
    }
    assert len(ids) == 1  # nunca cria um dispositivo novo a cada login


def test_cada_perfil_tem_o_seu_proprio_usuario(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream, token="tok-do-perfil-a-111111")
    _entrar(app_cfg, "a", login="ana@a.com")
    _login_ok(upstream, token="tok-do-perfil-b-222222")
    _entrar(app_cfg, "b", login="beto@b.com")
    perfis = _config(app_cfg)["perfis"]
    assert perfis["a"]["usuario"] == "ana@a.com"
    assert perfis["a"]["tokenUsuario"] == "tok-do-perfil-a-111111"
    assert perfis["b"]["usuario"] == "beto@b.com"
    assert perfis["b"]["tokenUsuario"] == "tok-do-perfil-b-222222"


def test_login_vale_sem_reiniciar(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    _entrar(app_cfg)
    assert app_cfg.servidor.cfg.perfis["a"].tokenUsuario == TOKEN_USUARIO
    por_id = {p["id"]: p for p in app_cfg.http.get("/_perfis").json()["perfis"]}
    assert por_id["a"]["usuario"] == "ana@cliente.com.br"
    assert por_id["b"]["usuario"] == ""


def test_senha_errada(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    r = _entrar(app_cfg, senha="errada")
    assert r.status_code == 401
    assert "Usuário ou senha inválidos" in r.json()["erro"]
    assert "tokenUsuario" not in _config(app_cfg)["perfis"]["a"]


def test_duas_etapas_nao_e_suportado(app_cfg: App, upstream: Upstream) -> None:
    upstream.respostas[LOGIN] = (403, '{"retorno": "exige verificação em duas etapas"}')
    r = _entrar(app_cfg)
    assert r.status_code == 403
    assert "duas etapas" in r.json()["erro"]


def test_outro_erro_da_api_mostra_a_mensagem(app_cfg: App, upstream: Upstream) -> None:
    upstream.respostas[LOGIN] = (400, '{"retorno": "Dispositivo já vinculado a outro usuário"}')
    r = _entrar(app_cfg)
    assert r.status_code == 400
    assert "Dispositivo já vinculado a outro usuário" in r.json()["erro"]


def test_resposta_sem_token_nao_grava_nada(app_cfg: App, upstream: Upstream) -> None:
    upstream.respostas[LOGIN] = (200, '{"mensagem": "ok", "versao": 3}')
    r = _entrar(app_cfg)
    assert r.status_code == 502
    assert "não reconheci o token" in r.json()["erro"]
    assert "mensagem, versao" in r.json()["erro"]  # só os nomes dos campos, nunca os valores
    assert "tokenUsuario" not in _config(app_cfg)["perfis"]["a"]


def test_api_fora_do_ar_nao_vaza_a_senha(app_cfg: App) -> None:
    app_cfg.http.post(
        "/_config/perfil",
        json={"nome": "morto", "instancia": "m", "baseUrl": "http://127.0.0.1:9", "token": "t"},
    )
    r = _entrar(app_cfg, "morto")
    assert r.status_code == 502
    assert "conexão" in r.json()["erro"]
    assert SENHA not in r.text


@pytest.mark.parametrize("campos", [{"login": ""}, {"login": "  "}, {"senha": ""}, {"login": 1}])
def test_pede_usuario_e_senha(app_cfg: App, campos: dict[str, Any]) -> None:
    r = _entrar(app_cfg, **campos)
    assert r.status_code == 400
    assert "usuário e a senha" in r.json()["erro"]


def test_perfil_desconhecido(app_cfg: App) -> None:
    assert _entrar(app_cfg, "ninguem").status_code == 404


def test_sem_arquivo_de_config_nao_loga(app: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    r = _entrar(app, "cliente1")
    assert r.status_code == 400
    assert not [c for c in upstream.recebidas if c["rota"] == "/api/sync/login"]  # nem chega à API


def test_login_respeita_a_protecao_do_perfil(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    app_cfg.http.post(
        "/_config/perfil",
        json={
            "original": "b",
            "nome": "b",
            "instancia": "b",
            "baseUrl": upstream.base,
            "token": "",
            "protecao": "bloquear",
        },
    )
    app_cfg.http.post(
        "/_config/perfil",
        json={
            "nome": "prod",
            "instancia": "p",
            "baseUrl": upstream.base,
            "token": "t",
            "protecao": "confirmar",
        },
    )
    assert _entrar(app_cfg, "b").status_code == 403  # só leitura: registrar dispositivo é escrita
    assert _entrar(app_cfg, "prod").status_code == 428
    assert [c for c in upstream.recebidas if c["rota"] == "/api/sync/login"] == []
    assert _entrar(app_cfg, "prod", confirmarProtecao=True).json()["ok"] is True


def test_login_exige_origem_da_pagina(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    ruim = {"Origin": "http://evil.example"}
    corpo = {"perfil": "a", "login": "x", "senha": SENHA}
    assert app_cfg.http.post("/_config/login", json=corpo, headers=ruim).status_code == 403
    assert app_cfg.http.delete("/_config/login/a", headers=ruim).status_code == 403
    assert not [c for c in upstream.recebidas if c["rota"] == "/api/sync/login"]


# -------------------------------------------------------------------- sair ---


def test_sair_apaga_o_token_e_o_usuario_mas_mantem_o_dispositivo(
    app_cfg: App, upstream: Upstream
) -> None:
    _login_ok(upstream)
    _entrar(app_cfg)
    assert app_cfg.http.delete("/_config/login/a").json() == {"ok": True}
    perfil = _config(app_cfg)["perfis"]["a"]
    assert "tokenUsuario" not in perfil and "usuario" not in perfil
    assert perfil["dispositivo"] == dispositivo_padrao()
    por_id = {p["id"]: p for p in app_cfg.http.get("/_perfis").json()["perfis"]}
    assert por_id["a"]["usuario"] == ""


def test_sair_de_perfil_desconhecido(app_cfg: App) -> None:
    assert app_cfg.http.delete("/_config/login/ninguem").status_code == 404


def test_trocar_a_instancia_do_perfil_derruba_o_login(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    _entrar(app_cfg)
    app_cfg.http.post(
        "/_config/perfil",
        json={
            "original": "a",
            "nome": "a",
            "instancia": "outra",
            "baseUrl": upstream.base,
            "token": "",
        },
    )
    assert (
        "tokenUsuario" not in _config(app_cfg)["perfis"]["a"]
    )  # o usuário era da instância antiga


def test_editar_outros_dados_mantem_o_login(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    _entrar(app_cfg)
    app_cfg.http.post(
        "/_config/perfil",
        json={
            "original": "a",
            "nome": "renomeado",
            "instancia": "A",
            "baseUrl": upstream.base,
            "token": "",
            "protecao": "",
        },
    )
    assert _config(app_cfg)["perfis"]["renomeado"]["tokenUsuario"] == TOKEN_USUARIO


# ------------------------------------------------------------- segurança ---


def test_a_senha_nunca_e_gravada_em_disco(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    _entrar(app_cfg)
    _entrar(app_cfg, senha="outra-senha-errada")
    app_cfg.http.delete("/_config/login/a")
    _entrar(app_cfg)
    disco = _todo_texto(app_cfg)
    assert SENHA not in disco
    assert "outra-senha-errada" not in disco
    assert '"senha"' not in disco  # nem como nome de campo


def test_nem_a_senha_nem_o_token_voltam_para_a_pagina(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    respostas = [
        _entrar(app_cfg).text,
        _entrar(app_cfg, senha="errada").text,
        app_cfg.http.get("/_perfis").text,
        app_cfg.http.get("/_painel?forcar=1").text,
        app_cfg.http.get("/_pastas/filhos?perfil=a&pai=0").text,
        app_cfg.http.get("/_diagnostico/a").text,
        app_cfg.http.get("/_status/a").text,
    ]
    for texto in respostas:
        assert SENHA not in texto
        assert TOKEN_USUARIO not in texto


def test_o_token_do_usuario_nao_vai_para_o_historico(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    _entrar(app_cfg)
    app_cfg.http.get("/_proxy/a/api/modelos")
    assert app_cfg.historico is not None
    historico = app_cfg.historico.arquivo
    texto = historico.read_text(encoding="utf-8") if historico.exists() else ""
    assert SENHA not in texto and TOKEN_USUARIO not in texto


def test_o_servidor_nao_imprime_a_senha(
    app_cfg: App, upstream: Upstream, capsys: pytest.CaptureFixture[str]
) -> None:
    _login_ok(upstream)
    _entrar(app_cfg)
    _entrar(app_cfg, senha="errada")
    saida = capsys.readouterr()
    assert SENHA not in saida.out + saida.err


def test_a_senha_nao_passa_pelo_proxy(app_cfg: App, upstream: Upstream) -> None:
    """O login não é uma rota do proxy: a página não tem como mandar a senha por lá."""
    r = app_cfg.http.post(f"/_proxy/a/api/sync/login?senha={SENHA}&login=x")
    assert r.status_code == 403
    assert [c for c in upstream.recebidas if c["rota"] == "/api/sync/login"] == []


def test_arquivos_secretos_ficam_fora_do_git() -> None:
    raiz = Path(__file__).parent.parent
    linhas = (raiz / ".gitignore").read_text(encoding="utf-8").splitlines()
    for arquivo in ("config.json", "config.json.bak", "config.json.tmp"):
        assert arquivo in linhas
    exemplo = (raiz / "config.example.json").read_text(encoding="utf-8")
    assert "tokenUsuario" not in exemplo and "senha" not in exemplo.lower()


def test_config_recusa_campos_de_login_que_nao_sao_texto() -> None:
    perfil = {"instancia": "a", "baseUrl": "http://x", "token": "t", "tokenUsuario": 123}
    with pytest.raises(ConfigError, match="tokenUsuario"):
        parse_config({"perfis": {"a": perfil}, "perfilPadrao": "a"})


# --------------------------------------------- o token do usuário nas pastas ---


def _filhos(app: App, perfil: str = "a") -> Any:
    return app.http.get(f"/_pastas/filhos?perfil={perfil}&pai=0")


def _pasta_oficial(id_: int, nome: str, caminho: str) -> tuple[int, str]:
    pasta = {"diretorioId": id_, "nome": nome, "caminho": caminho}
    return 200, json.dumps({"diretorios": [pasta]})


def test_pastas_usam_primeiro_o_token_do_usuario(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    _entrar(app_cfg)
    usuario = f"Bearer {TOKEN_USUARIO}"
    upstream.por_token[(*DIRETORIOS, usuario)] = _pasta_oficial(
        5, "Do usuário", "/Meus documentos/Do usuário"
    )
    r = _filhos(app_cfg).json()
    assert r["credencial"] == "usuario"
    assert [p["nome"] for p in r["pastas"]] == ["Do usuário"]
    primeira = next(c for c in upstream.recebidas if c["rota"] == "/api/diretorios")
    assert primeira["auth"] == usuario  # o token da instância nem é tentado antes


def test_pastas_voltam_ao_token_da_instancia_se_o_do_usuario_falhar(
    app_cfg: App, upstream: Upstream
) -> None:
    _login_ok(upstream)
    _entrar(app_cfg)
    upstream.por_token[(*DIRETORIOS, f"Bearer {TOKEN_USUARIO}")] = (401, "")
    upstream.por_token[(*FILHOS, f"Bearer {TOKEN_USUARIO}")] = (401, "")
    upstream.por_token[(*DIRETORIOS, f"Bearer {TOKEN_OK}")] = _pasta_oficial(
        7, "Da instância", "/x"
    )
    r = _filhos(app_cfg).json()
    assert (r["credencial"], r["fonte"]) == ("instancia", "api")


def test_sem_login_a_dica_manda_entrar(app_cfg: App, upstream: Upstream) -> None:
    upstream.respostas[DIRETORIOS] = (401, "")
    upstream.respostas[FILHOS] = (
        401,
        '{"retorno": "Esta instância exige login de usuário no sincronizador."}',
    )
    r = _filhos(app_cfg)
    assert r.status_code == 502
    corpo = r.json()
    assert corpo["precisaLogin"] is True
    assert "Entre com o seu usuário" in corpo["erro"]


def test_login_guardado_vencido_pede_novo_login(app_cfg: App, upstream: Upstream) -> None:
    _login_ok(upstream)
    _entrar(app_cfg)
    upstream.respostas[DIRETORIOS] = (401, "")
    upstream.respostas[FILHOS] = (401, "")  # o token do usuário e o da instância recusam
    assert _filhos(app_cfg).json()["precisaLogin"] is True


def test_erro_sem_relacao_com_login_nao_pede_login(app_cfg: App, upstream: Upstream) -> None:
    upstream.respostas[DIRETORIOS] = (500, "")
    upstream.respostas[FILHOS] = (500, "")
    assert _filhos(app_cfg).json()["precisaLogin"] is False


def test_baixar_pasta_le_a_arvore_com_o_token_do_usuario(
    app_cfg: App, upstream: Upstream, tmp_path: Path
) -> None:
    _login_ok(upstream)
    _entrar(app_cfg)
    upstream.por_token[(*DIRETORIOS, f"Bearer {TOKEN_OK}")] = (401, "")
    arvore = {
        "temMais": False,
        "diretorios": [
            {"diretorioId": 5, "caminho": "/Meus documentos/Raiz"},
            {"diretorioId": 6, "caminho": "/Meus documentos/Raiz/Sub"},
        ],
    }
    upstream.por_token[(*DIRETORIOS, f"Bearer {TOKEN_USUARIO}")] = (200, json.dumps(arvore))
    docs = [
        {"documentoId": i, "nomeArquivo": f"d{i}.pdf", "diretorioId": d}
        for i, d in ((1, 5), (2, 6))
    ]
    corpo = {"total": 2, "temMais": False, "documentos": docs}
    upstream.respostas[("GET", "/api/documentos")] = (200, json.dumps(corpo))
    for i in (1, 2):
        link = json.dumps({"urlDownload": f"{upstream.base}/arquivo/{i}"})
        upstream.respostas[("GET", f"/api/documento/{i}/download")] = (200, link)
        upstream.respostas[("GET", f"/arquivo/{i}")] = (200, "x")
    saida = tmp_path / "saida"
    pedido = {
        "perfil": "a",
        "diretorioId": 5,
        "destino": str(saida),
        "subpastas": True,
        "estrutura": True,
        "conflito": "renomear",
    }
    id_ = app_cfg.http.post("/_baixar/iniciar", json=pedido).json()["id"]
    estado: dict[str, Any] = {}
    for _ in range(300):
        estado = app_cfg.http.get(f"/_baixar/{id_}").json()
        if estado["estado"] not in ("listando", "baixando"):
            break
        time.sleep(0.03)
    assert estado["baixados"] == 2
    assert not estado["aviso"]
    assert (saida / "Sub" / "d2.pdf").is_file()  # a árvore foi refeita graças ao token do usuário
