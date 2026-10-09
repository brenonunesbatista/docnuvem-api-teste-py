"""Etapa 2: perfil protegido, diagnóstico da instância e favoritos persistentes."""

from __future__ import annotations

import json
from typing import Any

import pytest

from docnuvem_tester import web
from docnuvem_tester.config import ConfigError, parse_config
from docnuvem_tester.web import CABECALHO_CONFIRMACAO, Historico

from .conftest import TOKEN_OK, App, Upstream

# ------------------------------------------------------------ config.json ---


def _cfg(protegido: Any) -> dict[str, Any]:
    perfil: dict[str, Any] = {"instancia": "a", "baseUrl": "http://x", "token": "t"}
    if protegido is not None:
        perfil["protegido"] = protegido
    return {"perfis": {"a": perfil}, "perfilPadrao": "a"}


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (None, ""),
        (False, ""),
        (True, "confirmar"),
        ("confirmar", "confirmar"),
        ("bloquear", "bloquear"),
    ],
)
def test_config_protegido(valor: Any, esperado: str) -> None:
    assert parse_config(_cfg(valor)).perfis["a"].protecao == esperado


@pytest.mark.parametrize("valor", ["sim", 1, ["confirmar"]])
def test_config_protegido_invalido(valor: Any) -> None:
    with pytest.raises(ConfigError, match="protegido"):
        parse_config(_cfg(valor))


def test_perfis_informa_a_protecao(app: App) -> None:
    por_id = {p["id"]: p for p in app.http.get("/_perfis").json()["perfis"]}
    assert por_id["cliente1"]["protegido"] == ""
    assert por_id["prod"]["protegido"] == "confirmar"
    assert por_id["leitura"]["protegido"] == "bloquear"


# --------------------------------------------------------- perfil protegido ---


def _escrever(app: App, perfil: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    r = app.http.post(
        f"/_proxy/{perfil}/api/assinatura",
        json={"documentoId": 1},
        headers=headers or {},
    )
    assert r.status_code == 200
    return dict(r.json())


def test_perfil_confirmar_recusa_escrita_sem_confirmacao(app: App, upstream: Upstream) -> None:
    env = _escrever(app, "prod")
    assert env["status"] == 403
    assert env["protecao"] == "confirmar"
    assert "confirmação" in json.loads(env["body"])["retorno"]
    assert upstream.recebidas == []  # nada chegou à API


def test_perfil_confirmar_aceita_escrita_confirmada(app: App, upstream: Upstream) -> None:
    env = _escrever(app, "prod", {CABECALHO_CONFIRMACAO: "sim"})
    assert env["status"] == 200
    assert [c["method"] for c in upstream.recebidas] == ["POST"]


def test_perfil_confirmar_vale_para_delete(app: App, upstream: Upstream) -> None:
    r = app.http.delete("/_proxy/prod/api/assinatura/55").json()
    assert r["status"] == 403
    assert upstream.recebidas == []
    r = app.http.delete("/_proxy/prod/api/assinatura/55", headers={CABECALHO_CONFIRMACAO: "sim"})
    assert r.json()["status"] == 200


def test_perfil_confirmar_deixa_leituras_livres(app: App, upstream: Upstream) -> None:
    r = app.http.get("/_proxy/prod/api/documentos").json()
    assert r["status"] == 200
    assert [c["method"] for c in upstream.recebidas] == ["GET"]


def test_perfil_bloquear_recusa_mesmo_com_confirmacao(app: App, upstream: Upstream) -> None:
    env = _escrever(app, "leitura", {CABECALHO_CONFIRMACAO: "sim"})
    assert env["status"] == 403
    assert env["protecao"] == "bloquear"
    assert "só aceita leituras" in json.loads(env["body"])["retorno"]
    assert upstream.recebidas == []


def test_perfil_bloquear_deixa_leituras_livres(app: App) -> None:
    assert app.http.get("/_proxy/leitura/api/modelos").json()["status"] == 200


def test_perfil_livre_nao_pede_confirmacao(app: App, upstream: Upstream) -> None:
    assert _escrever(app, "cliente1")["status"] == 200
    assert len(upstream.recebidas) == 1


# -------------------------------------------------------------- diagnóstico ---


def _por_id(dados: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["id"]: i for i in dados["itens"]}


def _modelos(*itens: dict[str, Any]) -> tuple[int, str]:
    return 200, json.dumps({"modelos": list(itens)})


def test_diagnostico_tudo_certo(app: App, upstream: Upstream) -> None:
    upstream.respostas[("GET", "/api/modelos")] = _modelos(
        {"id": 1, "codigo": "CT", "nome": "Contrato", "geravelPorApi": True},
        {"id": 2, "codigo": "DC", "nome": "Declaração", "geravelPorApi": False},
    )
    upstream.respostas[("GET", "/api/diretorios")] = (200, '{"diretorios": [], "total": 7}')
    dados = app.http.get("/_diagnostico/cliente1").json()
    itens = _por_id(dados)
    assert dados["nivel"] == "ok"
    assert dados["instancia"] == "cliente1"
    assert [i["nivel"] for i in dados["itens"]] == ["ok", "ok", "ok", "ok", "info"]
    assert itens["modelos"]["total"] == 2
    assert itens["modelos"]["geraveis"] == 1
    assert itens["modelos"]["detalhe"] == "2 modelo(s), 1 gerável(is) por API."
    assert itens["modelos"]["lista"] == [
        {"codigo": "CT", "nome": "Contrato", "geravel": True},
        {"codigo": "DC", "nome": "Declaração", "geravel": False},
    ]
    assert "7 pasta(s)" in itens["pastas"]["detalhe"]
    assert "tipo 1 e 2" in itens["escola"]["causa"]


def test_diagnostico_so_faz_leituras(app: App, upstream: Upstream) -> None:
    app.http.get("/_diagnostico/cliente1")
    assert upstream.recebidas
    assert {c["method"] for c in upstream.recebidas} == {"GET"}


def test_diagnostico_usa_a_instancia_em_minusculo_e_o_token(app: App, upstream: Upstream) -> None:
    app.http.get("/_diagnostico/cliente1")
    autenticadas = [c for c in upstream.recebidas if c["rota"] != "/v3/api-docs/swagger-config"]
    assert autenticadas
    assert all(c["auth"] == f"Bearer {TOKEN_OK}" for c in autenticadas)
    assert all("instancia=cliente1" in c["path"] for c in autenticadas)


def test_diagnostico_token_recusado_pula_o_resto(app: App) -> None:
    dados = app.http.get("/_diagnostico/ruim").json()
    itens = _por_id(dados)
    assert dados["nivel"] == "erro"
    assert itens["api"]["nivel"] == "ok"
    assert itens["token"]["nivel"] == "erro"
    assert "401" in itens["token"]["detalhe"]
    assert "instancia" in itens["token"]["causa"]
    assert [itens[k]["nivel"] for k in ("modelos", "pastas", "escola")] == ["pulado"] * 3


def test_diagnostico_api_fora_do_ar(app: App) -> None:
    dados = app.http.get("/_diagnostico/fora").json()
    itens = _por_id(dados)
    assert dados["nivel"] == "erro"
    assert itens["api"]["nivel"] == "erro"
    assert "baseUrl" in itens["api"]["causa"]
    assert [i["nivel"] for i in dados["itens"]][1:] == ["pulado"] * 4


def test_diagnostico_api_com_erro_5xx(app: App, upstream: Upstream) -> None:
    upstream.respostas[("GET", "/v3/api-docs/swagger-config")] = (503, "")
    dados = app.http.get("/_diagnostico/cliente1").json()
    assert _por_id(dados)["api"]["nivel"] == "erro"


def test_diagnostico_sem_modelos_avisa(app: App, upstream: Upstream) -> None:
    upstream.respostas[("GET", "/api/modelos")] = _modelos()
    dados = app.http.get("/_diagnostico/cliente1").json()
    modelos = _por_id(dados)["modelos"]
    assert dados["nivel"] == "aviso"
    assert modelos["nivel"] == "aviso"
    assert "Nenhum modelo cadastrado" in modelos["detalhe"]


def test_diagnostico_modelos_sem_gerar_por_api_avisa(app: App, upstream: Upstream) -> None:
    upstream.respostas[("GET", "/api/modelos")] = _modelos({"id": 1, "nome": "X"})
    modelos = _por_id(app.http.get("/_diagnostico/cliente1").json())["modelos"]
    assert modelos["nivel"] == "aviso"
    assert "nenhum gerável por API" in modelos["detalhe"]


def test_diagnostico_sem_pastas_avisa(app: App, upstream: Upstream) -> None:
    upstream.respostas[("GET", "/api/modelos")] = _modelos(
        {"id": 1, "codigo": "A", "nome": "A", "geravelPorApi": True}
    )
    upstream.respostas[("GET", "/api/diretorios")] = (200, '{"diretorios": [], "total": 0}')
    pastas = _por_id(app.http.get("/_diagnostico/cliente1").json())["pastas"]
    assert pastas["nivel"] == "aviso"
    assert "já exista" in pastas["causa"]


def test_diagnostico_escola_sem_permissao_avisa(app: App, upstream: Upstream) -> None:
    upstream.respostas[("GET", "/api/solicitacaoAluno/consultarStatus")] = (
        403,
        '{"retorno": "Acesso negado"}',
    )
    escola = _por_id(app.http.get("/_diagnostico/cliente1").json())["escola"]
    assert escola["nivel"] == "aviso"
    assert "Acesso negado" in escola["detalhe"]


def test_diagnostico_escola_com_erro_5xx(app: App, upstream: Upstream) -> None:
    upstream.respostas[("GET", "/api/solicitacaoAluno/consultarStatus")] = (500, "")
    assert _por_id(app.http.get("/_diagnostico/cliente1").json())["escola"]["nivel"] == "erro"


def test_diagnostico_perfil_desconhecido(app: App) -> None:
    assert app.http.get("/_diagnostico/ninguem").status_code == 404


def test_diagnostico_exige_origem_da_pagina(app: App) -> None:
    r = app.http.get("/_diagnostico/cliente1", headers={"Host": "evil.example"})
    assert r.status_code == 403


# ------------------------------------------------------ favoritos persistentes ---


def entrada(n: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"c{n}",
        "ts": 1_760_000_000_000 + n,
        "method": "GET",
        "perfil": "cliente1",
        "url": f"http://api/x/{n}",
        "status": 200,
        "ms": 12,
        "screen": "documentos",
        **extra,
    }


def test_favoritar_persiste_no_disco(app: App) -> None:
    app.http.post("/_historico", json=entrada(1))
    app.http.post("/_historico", json=entrada(2))
    assert app.http.post("/_historico/favorito", json={"id": "c1", "fav": True}).json() == {
        "ok": True
    }
    assert app.historico is not None
    outro = Historico(app.historico.arquivo)  # como se o programa tivesse sido reiniciado
    favs = {e["id"]: e.get("fav") for e in outro.todas()}
    assert favs == {"c1": True, "c2": None}


def test_desfavoritar(app: App) -> None:
    app.http.post("/_historico", json=entrada(1, fav=True))
    app.http.post("/_historico/favorito", json={"id": "c1", "fav": False})
    assert app.http.get("/_historico").json()["itens"][0]["fav"] is False


def test_favoritar_id_inexistente(app: App) -> None:
    app.http.post("/_historico", json=entrada(1))
    r = app.http.post("/_historico/favorito", json={"id": "nao-existe", "fav": True})
    assert r.json() == {"ok": False}


def test_favoritar_recusa_pedido_invalido(app: App) -> None:
    assert app.http.post("/_historico/favorito", content=b"nao json").status_code == 400
    assert app.http.post("/_historico/favorito", json={"fav": True}).status_code == 400
    assert app.http.post("/_historico/favorito", json=[1]).status_code == 400


def test_favoritar_exige_origem_da_pagina(app: App) -> None:
    r = app.http.post(
        "/_historico/favorito", json={"id": "c1", "fav": True}, headers={"Origin": "http://x.test"}
    )
    assert r.status_code == 403


def test_limpar_mantem_as_favoritas(app: App) -> None:
    app.http.post("/_historico", json=entrada(1, fav=True))
    app.http.post("/_historico", json=entrada(2))
    app.http.delete("/_historico")
    assert [i["id"] for i in app.http.get("/_historico").json()["itens"]] == ["c1"]


def test_limpar_tudo_inclui_as_favoritas(app: App) -> None:
    app.http.post("/_historico", json=entrada(1, fav=True))
    app.http.delete("/_historico?tudo=1")
    assert app.http.get("/_historico").json()["itens"] == []


def test_poda_preserva_as_favoritas(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "MAX_HISTORICO", 5)
    monkeypatch.setattr(web, "FOLGA_HISTORICO", 0)
    h = Historico(tmp_path / "h.jsonl")
    h.adicionar(entrada(0, fav=True))
    for n in range(1, 15):
        h.adicionar(entrada(n))
    ids = [i["id"] for i in h.todas()]
    assert "c0" in ids  # a favorita mais antiga sobreviveu à poda
    assert ids[-1] == "c14"
    assert len(ids) <= 7


def test_listagem_inclui_favoritas_alem_do_limite(app: App) -> None:
    app.http.post("/_historico", json=entrada(1, fav=True))
    for n in (2, 3, 4):
        app.http.post("/_historico", json=entrada(n))
    ids = [i["id"] for i in app.http.get("/_historico?limite=2").json()["itens"]]
    assert ids == ["c4", "c3", "c1"]


def test_export_csv_marca_as_favoritas(app: App) -> None:
    app.http.post("/_historico", json=entrada(1, fav=True))
    app.http.post("/_historico", json=entrada(2))
    linhas = (
        app.http.get("/_historico/exportar?formato=csv").content.decode("utf-8-sig").splitlines()
    )
    assert linhas[1].endswith(";sim")
    assert linhas[2].endswith(";")


def test_diagnostico_pastas_recusadas_com_token_valido_aponta_para_a_api(
    app: App, upstream: Upstream
) -> None:
    upstream.respostas[("GET", "/api/diretorios")] = (401, "")
    upstream.respostas[("POST", "/api/sync/listarDiretoriosFilhos")] = (500, "")  # nem por aí
    pastas = _por_id(app.http.get("/_diagnostico/cliente1").json())["pastas"]
    assert pastas["nivel"] == "erro"
    assert "lado da API" in pastas["causa"]
