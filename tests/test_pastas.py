"""Estrutura de pastas da plataforma (seletor de pasta)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from .conftest import App, Upstream

OFICIAL = ("GET", "/api/diretorios")
SINC = ("POST", "/api/sync/listarDiretoriosFilhos")


def _oficial(*pastas: tuple[int, str, str], mais: bool = False) -> tuple[int, str]:
    itens = [{"diretorioId": i, "nome": n, "caminho": c} for i, n, c in pastas]
    return 200, json.dumps({"diretorios": itens, "temMais": mais})


def _sinc(
    *pastas: tuple[int, str, str], mais: bool = False, lixeira: set[int] | None = None
) -> Any:
    itens = [
        {"id": i, "nome": n, "caminhoNomesPais": c, "lixeira": i in (lixeira or set())}
        for i, n, c in pastas
    ]
    return 200, json.dumps({"diretorios": itens, "temMais": mais})


def _filhos(app: App, pai: int = 0, perfil: str = "cliente1") -> Any:
    return app.http.get(f"/_pastas/filhos?perfil={perfil}&pai={pai}")


def test_usa_o_endpoint_oficial_quando_ele_responde(app: App, upstream: Upstream) -> None:
    upstream.respostas[OFICIAL] = _oficial(
        (2, "Contratos", "/Meus documentos/Contratos"), (1, "Atas", "/Meus documentos/Atas")
    )
    r = _filhos(app).json()
    assert r["ok"] is True
    assert r["fonte"] == "api"
    assert [(p["id"], p["nome"]) for p in r["pastas"]] == [
        (1, "Atas"),
        (2, "Contratos"),
    ]  # por nome
    assert r["pastas"][0]["caminho"] == "/Meus documentos/Atas"
    consulta = next(c for c in upstream.recebidas if c["rota"] == "/api/diretorios")["path"]
    assert "diretorioPaiId=0" in consulta and "instancia=cliente1" in consulta


def test_cai_para_o_sincronizador_quando_a_api_recusa(app: App, upstream: Upstream) -> None:
    upstream.respostas[OFICIAL] = (401, "")  # o que a API real faz hoje
    upstream.respostas[SINC] = _sinc((7, "Docs", "/Meus documentos/Docs"))
    r = _filhos(app, pai=0).json()
    assert r["fonte"] == "sincronizador"
    assert r["pastas"] == [{"id": 7, "nome": "Docs", "caminho": "/Meus documentos/Docs"}]
    pedido = next(c for c in upstream.recebidas if c["rota"] == SINC[1])
    assert "diretorioPaiId=0" in pedido["path"]
    assert "size=200" in pedido["path"]


def test_lembra_qual_fonte_funciona(app: App, upstream: Upstream) -> None:
    upstream.respostas[OFICIAL] = (401, "")
    upstream.respostas[SINC] = _sinc((7, "Docs", "/Meus documentos/Docs"))
    _filhos(app)
    antes = len([c for c in upstream.recebidas if c["rota"] == "/api/diretorios"])
    _filhos(app, pai=7)
    _filhos(app, pai=8)
    depois = len([c for c in upstream.recebidas if c["rota"] == "/api/diretorios"])
    assert depois == antes  # não insiste no endpoint que já recusou


def test_voltar_a_funcionar_o_oficial_e_percebido_depois_do_prazo(
    app: App, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docnuvem_tester import pastas

    upstream.respostas[OFICIAL] = (401, "")
    upstream.respostas[SINC] = _sinc((7, "Docs", "/Meus documentos/Docs"))
    _filhos(app)
    monkeypatch.setattr(pastas, "TTL_FONTE", -1)  # a lembrança venceu
    upstream.respostas[OFICIAL] = _oficial((3, "Nova", "/Meus documentos/Nova"))
    assert _filhos(app).json()["fonte"] == "api"


def test_lixeira_nao_aparece(app: App, upstream: Upstream) -> None:
    upstream.respostas[OFICIAL] = (401, "")
    upstream.respostas[SINC] = _sinc((1, "A", "/Meus documentos/A"), (2, "B", "/x"), lixeira={2})
    assert [p["id"] for p in _filhos(app).json()["pastas"]] == [1]


def test_junta_todas_as_paginas(app: App, upstream: Upstream) -> None:
    def pagina(q: dict[str, list[str]]) -> tuple[int, str]:
        n = int(q["page"][0])
        lote = [(n * 2 + i, f"p{n * 2 + i}", f"/Meus documentos/p{n * 2 + i}") for i in (1, 2)]
        return _sinc(*lote, mais=n < 2)

    upstream.respostas[OFICIAL] = (401, "")
    upstream.dinamicas[SINC] = pagina
    assert [p["id"] for p in _filhos(app).json()["pastas"]] == [1, 2, 3, 4, 5, 6]


def test_pasta_vazia(app: App, upstream: Upstream) -> None:
    upstream.respostas[OFICIAL] = _oficial()
    assert _filhos(app, pai=9).json()["pastas"] == []


def test_erro_quando_nenhuma_fonte_funciona(app: App, upstream: Upstream) -> None:
    upstream.respostas[OFICIAL] = (401, "")
    upstream.respostas[SINC] = (401, '{"retorno": "exige login no sincronizador"}')
    r = _filhos(app)
    assert r.status_code == 502
    erro = r.json()["erro"]
    assert "HTTP 401" in erro and "exige login no sincronizador" in erro
    assert "Entre com o seu usuário" in erro  # a API pediu login: a saída é entrar
    assert r.json()["precisaLogin"] is True


def test_erro_sem_pedido_de_login_manda_digitar_o_caminho(app: App, upstream: Upstream) -> None:
    upstream.respostas[OFICIAL] = (500, "")
    upstream.respostas[SINC] = (500, "")
    r = _filhos(app)
    assert "Digite o caminho" in r.json()["erro"]
    assert r.json()["precisaLogin"] is False


def test_api_fora_do_ar(app: App) -> None:
    r = _filhos(app, perfil="fora")
    assert r.status_code == 502
    assert "conexão" in r.json()["erro"]


def test_perfil_somente_leitura_tambem_navega(app: App, upstream: Upstream) -> None:
    upstream.respostas[OFICIAL] = _oficial((1, "A", "/Meus documentos/A"))
    assert _filhos(app, perfil="leitura").json()["ok"] is True


@pytest.mark.parametrize("pai", ["abc", "-1", "1.5"])
def test_pai_invalido(app: App, pai: str) -> None:
    assert app.http.get(f"/_pastas/filhos?perfil=cliente1&pai={pai}").status_code == 400


def test_perfil_desconhecido(app: App) -> None:
    assert _filhos(app, perfil="ninguem").status_code == 404


def test_exige_origem_da_pagina(app: App) -> None:
    r = app.http.get("/_pastas/filhos?perfil=cliente1&pai=0", headers={"Host": "evil.example"})
    assert r.status_code == 403
