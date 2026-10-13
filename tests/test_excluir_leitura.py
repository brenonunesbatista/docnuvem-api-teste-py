"""Excluir em massa: leitura em paralelo, verificação em segundo plano e reaproveitamento."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from docnuvem_tester import excluir

from .conftest import App, Upstream
from .test_excluir import (
    LISTAGEM,
    _corpo,
    _destrutivas,
    _ensaio,
    _esperar,
    _iniciar,
    _paginado,
    _tres,
)


def _paginas_200(upstream: Upstream) -> int:
    """Quantas páginas cheias (tamanho=200) a API recebeu."""
    return len(
        [
            c
            for c in upstream.recebidas
            if c["rota"] == "/api/documentos" and "tamanho=200" in c["path"]
        ]
    )


def _aguardar_execucao(app: App, id_: str) -> None:
    for _ in range(300):
        if app.http.get(f"/_excluir/{id_}").json()["estado"] == "executando":
            return
        time.sleep(0.05)
    raise AssertionError("a exclusão não chegou a executar")


# ----------------------------------------------------------- leitura paralela ---


def test_a_leitura_usa_varias_consultas_ao_mesmo_tempo(app: App, upstream: Upstream) -> None:
    paginas = _paginado(4000)
    ativas = {"agora": 0, "pico": 0}
    trava = threading.Lock()

    def com_contagem(q: dict[str, list[str]]) -> tuple[int, str]:
        if q["tamanho"][0] == "1":
            return paginas(q)
        with trava:
            ativas["agora"] += 1
            ativas["pico"] = max(ativas["pico"], ativas["agora"])
        time.sleep(0.05)
        with trava:
            ativas["agora"] -= 1
        return paginas(q)

    upstream.dinamicas[LISTAGEM] = com_contagem
    r = _ensaio(app, confirmacao="").json()
    assert r["total"] == 4000
    assert 2 <= ativas["pico"] <= excluir.PARALELISMO  # em paralelo, mas dentro do limite


def test_leitura_em_paralelo_mantem_a_ordem_e_nao_repete(app: App, upstream: Upstream) -> None:
    paginas = _paginado(1234)

    def embaralhada(q: dict[str, list[str]]) -> tuple[int, str]:
        time.sleep(0.1 if int(q["pagina"][0]) % 2 == 0 else 0.01)  # páginas terminam fora de ordem
        return paginas(q)

    upstream.dinamicas[LISTAGEM] = embaralhada
    r = _ensaio(app, confirmacao="").json()
    assert r["total"] == 1234
    assert [a["documentoId"] for a in r["amostra"]] == list(range(1, 9))  # a ordem da API


def test_falha_numa_pagina_do_meio_aborta_a_leitura(app: App, upstream: Upstream) -> None:
    paginas = _paginado(5000)
    upstream.dinamicas[LISTAGEM] = lambda q: (500, "") if q["pagina"][0] == "4" else paginas(q)
    r = _ensaio(app, confirmacao="")
    assert r.status_code == 400
    assert "HTTP 500" in r.json()["erro"]


def test_mais_documentos_que_o_total_anunciado_continua_lendo(app: App, upstream: Upstream) -> None:
    """A API disse 300, mas tem 450: não pode deixar ninguém de fora."""
    paginas = _paginado(450)

    def total_errado(q: dict[str, list[str]]) -> tuple[int, str]:
        status, corpo = paginas(q)
        dado = json.loads(corpo)
        dado["total"] = 300
        return status, json.dumps(dado)

    upstream.dinamicas[LISTAGEM] = total_errado
    assert _ensaio(app, confirmacao="").json()["total"] == 450


# ------------------------------------------------------ verificação em segundo plano ---


def _verificar(app: App, **extra: Any) -> Any:
    return app.http.post("/_excluir/verificar", json=_corpo(**extra))


def _esperar_verificacao(app: App, id_: str, limite: float = 15.0) -> dict[str, Any]:
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        v = dict(app.http.get(f"/_excluir/verificacao/{id_}").json())
        if v["estado"] != "lendo":
            return v
        time.sleep(0.03)
    raise AssertionError("a verificação não terminou a tempo")


def test_verificacao_em_segundo_plano_traz_o_resultado(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    r = _verificar(app).json()
    assert r["ok"] is True
    v = _esperar_verificacao(app, r["id"])
    assert v["estado"] == "pronta"
    assert v["resultado"]["total"] == 3
    assert v["resultado"]["fluxosAtivos"] == 1
    assert v["resultado"]["confirmacao"] == "EXCLUIR 3"
    assert _destrutivas(upstream) == []  # só leituras


def test_verificacao_mostra_o_progresso_e_o_tempo_restante(app: App, upstream: Upstream) -> None:
    paginas = _paginado(2000)

    def devagar(q: dict[str, list[str]]) -> tuple[int, str]:
        if q["tamanho"][0] != "1":
            time.sleep(0.15)
        return paginas(q)

    upstream.dinamicas[LISTAGEM] = devagar
    r = _verificar(app).json()
    vistos: list[dict[str, Any]] = []
    for _ in range(300):
        v = dict(app.http.get(f"/_excluir/verificacao/{r['id']}").json())
        vistos.append(v)
        if v["estado"] != "lendo":
            break
        time.sleep(0.03)
    parciais = [x for x in vistos if x["estado"] == "lendo" and 0 < x["lidos"] < x["total"]]
    assert parciais, "a leitura nunca apareceu pela metade"
    assert all(x["total"] == 2000 for x in parciais)
    assert any(x["restante"] is not None for x in parciais)
    assert vistos[-1]["estado"] == "pronta" and vistos[-1]["lidos"] == 2000


def test_cancelar_a_verificacao(app: App, upstream: Upstream) -> None:
    paginas = _paginado(5000)

    def devagar(q: dict[str, list[str]]) -> tuple[int, str]:
        if q["tamanho"][0] != "1":
            time.sleep(0.2)
        return paginas(q)

    upstream.dinamicas[LISTAGEM] = devagar
    r = _verificar(app).json()
    time.sleep(0.4)
    assert app.http.post(f"/_excluir/verificacao/{r['id']}/cancelar").json() == {"ok": True}
    v = _esperar_verificacao(app, r["id"])
    assert v["estado"] == "cancelada" and v["resultado"] is None
    assert _destrutivas(upstream) == []


def test_verificacao_com_erro_na_api(app: App, upstream: Upstream) -> None:
    upstream.respostas[LISTAGEM] = (500, "")
    v = _esperar_verificacao(app, _verificar(app).json()["id"])
    assert v["estado"] == "erro"
    assert "HTTP 500" in v["erro"]


@pytest.mark.parametrize(
    "campos", [{"diretorioId": 0}, {"status": "apagado"}, {"diretorioId": "x"}]
)
def test_verificacao_recusa_pedido_invalido_na_hora(
    app: App, upstream: Upstream, campos: dict[str, Any]
) -> None:
    _tres(upstream)
    assert _verificar(app, **campos).status_code == 400
    assert upstream.recebidas == []


def test_verificacao_desconhecida_e_origem(app: App) -> None:
    assert app.http.get("/_excluir/verificacao/nada").status_code == 404
    assert app.http.post("/_excluir/verificacao/nada/cancelar").status_code == 404
    ruim = {"Origin": "http://evil.example"}
    assert app.http.post("/_excluir/verificar", json=_corpo(), headers=ruim).status_code == 403
    assert app.http.post("/_excluir/verificacao/x/cancelar", headers=ruim).status_code == 403


def test_verificacao_vale_em_perfil_somente_leitura(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    v = _esperar_verificacao(app, _verificar(app, perfil="leitura").json()["id"])
    assert v["estado"] == "pronta"


# --------------------------------------------------- reaproveitar a leitura ---


def test_a_lista_da_verificacao_e_reaproveitada_na_exclusao(app: App, upstream: Upstream) -> None:
    upstream.dinamicas[LISTAGEM] = _paginado(900)
    v = _esperar_verificacao(app, _verificar(app).json()["id"])
    lidas = _paginas_200(upstream)
    assert lidas == 5  # 900 / 200, arredondado para cima
    r = _iniciar(app, confirmacao="EXCLUIR 900", verificacaoId=v["id"], intervaloMs=2000)
    assert r["ok"] is True
    app.http.post(f"/_excluir/{r['id']}/cancelar")
    fim = _esperar(app, r["id"])
    assert _paginas_200(upstream) == lidas  # NÃO leu a pasta de novo
    assert fim["total"] == 900


def test_sem_verificacao_a_exclusao_le_a_pasta_de_novo(app: App, upstream: Upstream) -> None:
    upstream.dinamicas[LISTAGEM] = _paginado(900)
    r = _iniciar(app, confirmacao="EXCLUIR 900", intervaloMs=2000)
    _aguardar_execucao(app, r["id"])
    assert _paginas_200(upstream) == 5
    app.http.post(f"/_excluir/{r['id']}/cancelar")
    _esperar(app, r["id"])


def test_lista_da_verificacao_nao_vale_se_o_total_mudou(app: App, upstream: Upstream) -> None:
    upstream.dinamicas[LISTAGEM] = _paginado(900)
    v = _esperar_verificacao(app, _verificar(app).json()["id"])
    upstream.dinamicas[LISTAGEM] = _paginado(901)  # entrou um documento depois
    r = _iniciar(app, confirmacao="EXCLUIR 900", verificacaoId=v["id"])
    assert r["_status"] == 428  # a confirmação (900) não bate mais com os 901 de agora
    assert _destrutivas(upstream) == []


@pytest.mark.parametrize(
    "mudanca",
    [{"diretorioId": 6}, {"status": "assinado"}, {"subpastas": True}, {"perfil": "prod"}],
)
def test_lista_da_verificacao_nao_vale_para_outra_selecao(
    app: App, upstream: Upstream, mudanca: dict[str, Any]
) -> None:
    upstream.dinamicas[LISTAGEM] = _paginado(900)
    v = _esperar_verificacao(app, _verificar(app).json()["id"])
    antes = _paginas_200(upstream)
    extra = {"confirmarProtecao": True} if mudanca.get("perfil") == "prod" else {}
    r = _iniciar(
        app, confirmacao="EXCLUIR 900", verificacaoId=v["id"], intervaloMs=2000, **mudanca, **extra
    )
    assert r["ok"] is True
    _aguardar_execucao(app, r["id"])
    assert _paginas_200(upstream) > antes  # leu de novo, porque a seleção era outra
    app.http.post(f"/_excluir/{r['id']}/cancelar")
    _esperar(app, r["id"])


def test_lista_da_verificacao_vence_com_o_tempo(
    app: App, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream.dinamicas[LISTAGEM] = _paginado(400)
    v = _esperar_verificacao(app, _verificar(app).json()["id"])
    monkeypatch.setattr(excluir, "TTL_VERIFICACAO", -1.0)
    antes = _paginas_200(upstream)
    r = _iniciar(app, confirmacao="EXCLUIR 400", verificacaoId=v["id"], intervaloMs=2000)
    _aguardar_execucao(app, r["id"])
    assert _paginas_200(upstream) > antes
    app.http.post(f"/_excluir/{r['id']}/cancelar")
    _esperar(app, r["id"])


def test_so_as_ultimas_verificacoes_ficam_na_memoria(
    app: App, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(excluir, "MAX_VERIFICACOES", 2)
    _tres(upstream)
    ids = [_verificar(app).json()["id"] for _ in range(4)]
    time.sleep(0.5)
    assert app.http.get(f"/_excluir/verificacao/{ids[0]}").status_code == 404
    assert app.http.get(f"/_excluir/verificacao/{ids[-1]}").status_code == 200
