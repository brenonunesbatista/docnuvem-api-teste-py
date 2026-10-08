"""Servidor local: páginas, proxy, segurança, status da API e histórico."""

from __future__ import annotations

import json
from typing import Any

import pytest

from docnuvem_tester import web
from docnuvem_tester.web import ROTAS_PERMITIDAS, Historico, mascarar_token

from .conftest import TOKEN_OK, App, Upstream


def test_mascarar_token() -> None:
    assert mascarar_token("abcdef1234") == "Bearer ***...1234"
    assert mascarar_token("ab") == "Bearer ***...ab"
    assert mascarar_token("") == "Bearer ***"


# ---------------------------------------------------------------- páginas ---


def test_perfis_nunca_expoe_o_token(app: App) -> None:
    r = app.http.get("/_perfis")
    assert r.status_code == 200
    assert TOKEN_OK not in r.text
    dados = r.json()
    assert dados["padrao"] == "cliente1"
    assert dados["historico"] is True
    assert dados["arquivoHistorico"].endswith("historico.jsonl")
    cliente1 = next(p for p in dados["perfis"] if p["id"] == "cliente1")
    assert cliente1["tokenMascarado"] == "Bearer ***...1234"


def test_pagina_e_arquivos_estaticos(app: App) -> None:
    pagina = app.http.get("/")
    assert pagina.status_code == 200
    assert pagina.headers["content-type"].startswith("text/html")
    assert "Docnuvem" in pagina.text
    js = app.http.get("/dc-runtime.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    logo = app.http.get("/logo.png")
    assert logo.status_code == 200
    assert logo.content.startswith(b"\x89PNG")
    assert app.http.get("/nao-existe").status_code == 404


# ------------------------------------------------------------------ proxy ---


def test_proxy_injeta_token_e_instancia_minuscula(app: App, upstream: Upstream) -> None:
    r = app.http.get(
        "/_proxy/cliente1/api/documentos", params={"pagina": "0", "status": "pendente"}
    )
    env = r.json()
    assert env["status"] == 200
    chamada = upstream.recebidas[-1]
    assert chamada["auth"] == f"Bearer {TOKEN_OK}"
    assert "instancia=cliente1" in chamada["path"]  # a instância do perfil era CLIENTE1
    assert "pagina=0" in chamada["path"]
    assert "status=pendente" in chamada["path"]
    assert env["url"].startswith(upstream.base)
    assert TOKEN_OK not in r.text


def test_proxy_ignora_instancia_vinda_do_navegador(app: App, upstream: Upstream) -> None:
    app.http.get("/_proxy/cliente1/api/documentos", params={"instancia": "outra"})
    caminho = upstream.recebidas[-1]["path"]
    assert caminho.count("instancia=") == 1
    assert "instancia=cliente1" in caminho


def test_proxy_mantem_parametro_vazio(app: App, upstream: Upstream) -> None:
    app.http.get("/_proxy/cliente1/api/documentos", params={"status": ""})
    assert "status=" in upstream.recebidas[-1]["path"]


def test_proxy_repassa_json(app: App, upstream: Upstream) -> None:
    corpo = {"documentoId": 10, "signatarios": []}
    app.http.post("/_proxy/cliente1/api/assinatura", json=corpo)
    chamada = upstream.recebidas[-1]
    assert chamada["method"] == "POST"
    assert chamada["ct"] == "application/json"
    assert json.loads(chamada["corpo"]) == corpo


def test_proxy_repassa_multipart_intacto(app: App, upstream: Upstream) -> None:
    conteudo = b"%PDF-1.4\x00\xff binario"
    app.http.post(
        "/_proxy/cliente1/importar",
        params={"nomeArquivo": "a.pdf", "nomePasta": "Contratos"},
        files={"file": ("a.pdf", conteudo, "application/pdf")},
    )
    chamada = upstream.recebidas[-1]
    assert chamada["ct"].startswith("multipart/form-data")
    assert conteudo in chamada["corpo"]
    assert "nomePasta=Contratos" in chamada["path"]


def test_proxy_devolve_erro_da_api_sem_alterar(app: App, upstream: Upstream) -> None:
    upstream.respostas[("GET", "/api/documentos")] = (400, '{"retorno":"status inválido"}')
    env = app.http.get("/_proxy/cliente1/api/documentos").json()
    assert env["status"] == 400
    assert json.loads(env["body"])["retorno"] == "status inválido"


def test_proxy_502_quando_a_api_nao_responde(app: App) -> None:
    env = app.http.get("/_proxy/fora/api/modelos").json()
    assert env["status"] == 502
    assert "Falha de conexão" in json.loads(env["body"])["retorno"]


def test_proxy_recusa_rota_fora_da_lista(app: App, upstream: Upstream) -> None:
    antes = len(upstream.recebidas)
    assert app.http.get("/_proxy/cliente1/admin/usuarios").status_code == 403
    assert app.http.post("/_proxy/cliente1/api/modelos").status_code == 403  # método errado
    assert app.http.get("/_proxy/cliente1/api/sync/tree").status_code == 403
    assert len(upstream.recebidas) == antes


def test_proxy_perfil_desconhecido(app: App) -> None:
    assert app.http.get("/_proxy/ninguem/api/modelos").status_code == 404


def test_proxy_limita_o_tamanho_do_corpo(
    app: App, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web, "MAX_CORPO", 10)
    r = app.http.post("/_proxy/cliente1/api/assinatura", content=b"x" * 100)
    assert r.status_code == 413
    assert not [c for c in upstream.recebidas if c["rota"] == "/api/assinatura"]


@pytest.mark.parametrize(
    "caminho",
    [
        "/api/documento/../status",
        "/api/documento/./download",
        "/api/documento//status",
        "/api/assinatura/..",
        "/api/assinatura/",
        "/api/documento/1/2/status",
    ],
)
def test_rotas_com_id_nao_aceitam_passeio_de_diretorio(caminho: str) -> None:
    assert not any(rx.match(caminho) for _, rx in ROTAS_PERMITIDAS)


@pytest.mark.parametrize(
    ("metodo", "caminho"),
    [
        ("GET", "/api/documento/48201/status"),
        ("GET", "/api/documento/48201/download"),
        ("DELETE", "/api/assinatura/7001"),
        ("DELETE", "/api/assinatura/abc-9"),
    ],
)
def test_rotas_com_id_normais_passam(metodo: str, caminho: str) -> None:
    assert any(m == metodo and rx.match(caminho) for m, rx in ROTAS_PERMITIDAS)


# -------------------------------------------------------------- segurança ---


def test_recusa_host_falso(app: App) -> None:
    assert app.http.get("/_perfis", headers={"Host": "evil.example"}).status_code == 403


def test_recusa_origem_de_outro_site(app: App) -> None:
    assert app.http.get("/_perfis", headers={"Origin": "http://evil.example"}).status_code == 403
    assert (
        app.http.post(
            "/_proxy/cliente1/api/assinatura", json={}, headers={"Origin": "http://evil.example"}
        ).status_code
        == 403
    )


def test_recusa_requisicao_cross_site_do_navegador(app: App) -> None:
    r = app.http.get("/_perfis", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_aceita_a_propria_pagina(app: App) -> None:
    r = app.http.get("/_perfis", headers={"Origin": app.base, "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200


def test_historico_tambem_exige_origem_valida(app: App) -> None:
    ruim = {"Origin": "http://evil.example"}
    entrada = {"method": "GET", "url": "http://x", "status": 200}
    assert app.http.post("/_historico", json=entrada, headers=ruim).status_code == 403
    assert app.http.delete("/_historico", headers=ruim).status_code == 403


# ----------------------------------------------------------- status da API ---


def test_status_ok(app: App) -> None:
    dados = app.http.get("/_status/cliente1").json()
    assert dados["nivel"] == "ok"
    assert dados["api"]["ok"] is True
    assert dados["token"]["ok"] is True
    assert isinstance(dados["api"]["ms"], int)


def test_status_token_recusado(app: App) -> None:
    dados = app.http.get("/_status/ruim").json()
    assert dados["nivel"] == "token"
    assert dados["token"]["status"] == 401
    assert dados["api"]["ok"] is True


def test_status_api_fora_do_ar(app: App) -> None:
    dados = app.http.get("/_status/fora").json()
    assert dados["nivel"] == "offline"
    assert dados["api"]["ok"] is False
    assert dados["api"]["erro"]


def test_status_api_com_erro_5xx(app: App, upstream: Upstream) -> None:
    upstream.respostas[("GET", "/v3/api-docs/swagger-config")] = (503, "")
    assert app.http.get("/_status/cliente1?forcar=1").json()["nivel"] == "erro"


def test_status_so_confere_com_leituras(app: App, upstream: Upstream) -> None:
    app.http.get("/_status/cliente1?forcar=1")
    assert {c["method"] for c in upstream.recebidas} == {"GET"}


def test_status_usa_cache_e_forcar_ignora(app: App, upstream: Upstream) -> None:
    app.http.get("/_status/cliente1?forcar=1")
    n = len(upstream.recebidas)
    app.http.get("/_status/cliente1")
    assert len(upstream.recebidas) == n  # veio do cache
    app.http.get("/_status/cliente1?forcar=1")
    assert len(upstream.recebidas) > n


def test_status_perfil_desconhecido(app: App) -> None:
    assert app.http.get("/_status/ninguem").status_code == 404


def test_status_lento(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "LIMITE_API_LENTA_MS", -1)  # qualquer tempo passa a ser "lento"
    dados = web.verificar_status(app.servidor.cliente, app.servidor.cfg.perfis["cliente1"])
    assert dados["nivel"] == "lento"


# ---------------------------------------------------------------- histórico ---


def entrada(n: int = 1, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"c{n}",
        "ts": 1_760_000_000_000 + n,
        "method": "GET",
        "perfil": "cliente1",
        "url": f"http://api/x/{n}",
        "status": 200,
        "ms": 12,
        "auth": "Bearer ***...1234",
        "screen": "documentos",
        **extra,
    }


def test_historico_grava_e_lista_do_mais_novo_para_o_mais_antigo(app: App) -> None:
    for n in (1, 2, 3):
        assert app.http.post("/_historico", json=entrada(n)).json() == {"ok": True}
    itens = app.http.get("/_historico").json()["itens"]
    assert [i["id"] for i in itens] == ["c3", "c2", "c1"]
    assert all("salvoEm" in i for i in itens)
    assert [i["id"] for i in app.http.get("/_historico?limite=2").json()["itens"]] == ["c3", "c2"]


def test_historico_sobrevive_a_novo_servidor(app: App) -> None:
    app.http.post("/_historico", json=entrada(1))
    assert app.historico is not None
    outro = Historico(app.historico.arquivo)  # como se o programa tivesse sido reiniciado
    assert [i["id"] for i in outro.recentes(10)] == ["c1"]


def test_historico_descarta_chaves_desconhecidas(app: App) -> None:
    app.http.post("/_historico", json=entrada(1, token="SEGREDO", authorization="Bearer x"))
    item = app.http.get("/_historico").json()["itens"][0]
    assert "token" not in item
    assert "authorization" not in item
    assert "SEGREDO" not in (
        app.historico.arquivo.read_text(encoding="utf-8") if app.historico else ""
    )


def test_historico_recusa_entrada_invalida(app: App) -> None:
    assert app.http.post("/_historico", content=b"nao json").status_code == 400
    assert app.http.post("/_historico", json={"method": "GET"}).status_code == 400
    assert app.http.post("/_historico", json=[1, 2]).status_code == 400


def test_historico_recusa_entrada_enorme(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "MAX_ENTRADA", 100)
    assert app.http.post("/_historico", json=entrada(1, body="x" * 500)).status_code == 413


def test_historico_ignora_linha_corrompida(app: App) -> None:
    app.http.post("/_historico", json=entrada(1))
    assert app.historico is not None
    with app.historico.arquivo.open("a", encoding="utf-8") as f:
        f.write("{linha quebrada\n")
    app.http.post("/_historico", json=entrada(2))
    assert [i["id"] for i in app.http.get("/_historico").json()["itens"]] == ["c2", "c1"]


def test_historico_exporta_json(app: App) -> None:
    app.http.post("/_historico", json=entrada(1))
    app.http.post("/_historico", json=entrada(2))
    r = app.http.get("/_historico/exportar?formato=json")
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.json"')
    assert [i["id"] for i in r.json()] == ["c1", "c2"]  # ordem cronológica


def test_historico_exporta_csv(app: App) -> None:
    app.http.post("/_historico", json=entrada(1))
    r = app.http.get("/_historico/exportar?formato=csv")
    assert r.headers["content-disposition"].endswith('.csv"')
    assert r.content.startswith(b"\xef\xbb\xbf")  # BOM para o Excel
    linhas = r.content.decode("utf-8-sig").splitlines()
    assert linhas[0] == "data_hora;perfil;metodo;url;status;duracao_ms;tela;favorita"
    assert "cliente1;GET;http://api/x/1;200;12;documentos" in linhas[1]


def test_historico_limpar(app: App) -> None:
    app.http.post("/_historico", json=entrada(1))
    assert app.http.delete("/_historico").json() == {"ok": True}
    assert app.http.get("/_historico").json()["itens"] == []


def test_historico_poda_o_excesso(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "MAX_HISTORICO", 5)
    monkeypatch.setattr(web, "FOLGA_HISTORICO", 0)
    h = Historico(tmp_path / "h.jsonl")
    for n in range(12):
        h.adicionar(entrada(n))
    ids = [i["id"] for i in h.todas()]
    assert len(ids) <= 6
    assert ids[-1] == "c11"  # a mais recente sempre fica


def test_historico_desligado(app_sem_historico: App) -> None:
    app = app_sem_historico
    assert app.http.get("/_perfis").json()["historico"] is False
    assert app.http.post("/_historico", json=entrada(1)).json()["ok"] is False
    assert app.http.get("/_historico").json()["itens"] == []
    assert app.http.get("/_historico/exportar?formato=json").json() == []


def test_conexao_abandonada_pelo_navegador_nao_imprime_erro(
    app: App, capsys: pytest.CaptureFixture[str]
) -> None:
    try:
        raise ConnectionAbortedError("o navegador fechou a aba")
    except ConnectionAbortedError:
        app.servidor.handle_error(None, ("127.0.0.1", 1234))
    assert capsys.readouterr().err == ""


def test_erro_de_verdade_continua_aparecendo(app: App, capsys: pytest.CaptureFixture[str]) -> None:
    try:
        raise ValueError("bug de verdade")
    except ValueError:
        app.servidor.handle_error(None, ("127.0.0.1", 1234))
    assert "bug de verdade" in capsys.readouterr().err
