"""Painel das instâncias e monitor de disponibilidade."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest

from docnuvem_tester import monitor as monitor_mod
from docnuvem_tester.config import AppConfig, PerfilConfig
from docnuvem_tester.monitor import ErroMonitor, Monitor

from .conftest import App, Upstream

# ----------------------------------------------------------- monitor (unidade) ---


class Roteiro:
    """Verificador falso: devolve, perfil a perfil, o que o teste programou."""

    def __init__(self) -> None:
        self.niveis: dict[str, str] = {}
        self.ms = 40

    def __call__(self, cliente: httpx.Client, perfil: PerfilConfig) -> dict[str, Any]:
        nivel = self.niveis.get(perfil.instancia, "ok")
        return {
            "verificadoEm": 0,
            "nivel": nivel,
            "api": {
                "ok": nivel != "offline",
                "ms": self.ms,
                "status": 503 if nivel == "erro" else 200,
                "erro": "recusou a conexão" if nivel == "offline" else None,
            },
            "token": {
                "ok": nivel in ("ok", "lento"),
                "ms": self.ms,
                "status": 401 if nivel == "token" else 200,
            },
        }


def _monitor(*nomes: str) -> tuple[Monitor, Roteiro]:
    cfg = AppConfig(
        perfis={n: PerfilConfig(n, "http://x", "t") for n in ("a", "b", "c")}, perfilPadrao="a"
    )
    roteiro = Roteiro()
    m = Monitor(httpx.Client(), lambda: cfg, roteiro)
    m.perfis = set(nomes or ("a",))
    return m, roteiro


def _tipos(m: Monitor) -> list[tuple[str, str]]:
    return [(e["perfil"], e["tipo"]) for e in m.eventos]


def test_base_ok_nao_gera_evento() -> None:
    m, _ = _monitor("a", "b")
    m.ciclo()
    assert m.eventos == []
    assert m.ciclos == 1
    assert {n: e.nivel for n, e in m.estados.items()} == {"a": "ok", "b": "ok"}


def test_problema_ja_existente_na_primeira_verificacao_avisa_na_hora() -> None:
    m, r = _monitor("a")
    r.niveis["a"] = "token"
    m.ciclo()
    assert _tipos(m) == [("a", "token")]
    assert "já estava assim" in m.eventos[0]["detalhe"]


def test_queda_so_e_declarada_depois_de_duas_verificacoes() -> None:
    m, r = _monitor("a")
    m.ciclo()
    r.niveis["a"] = "offline"
    m.ciclo()
    assert m.eventos == []  # um tropeço sozinho não alarma
    m.ciclo()
    assert _tipos(m) == [("a", "queda")]
    assert "recusou a conexão" in m.eventos[0]["detalhe"]
    m.ciclo()
    m.ciclo()
    assert len(m.eventos) == 1  # sem repetir enquanto continua fora do ar


def test_tropeco_isolado_e_esquecido() -> None:
    m, r = _monitor("a")
    m.ciclo()
    r.niveis["a"] = "offline"
    m.ciclo()
    r.niveis["a"] = "ok"
    m.ciclo()
    r.niveis["a"] = "offline"
    m.ciclo()  # a contagem recomeçou
    assert m.eventos == []


def test_resultados_diferentes_nao_somam_confirmacoes() -> None:
    m, r = _monitor("a")
    m.ciclo()
    r.niveis["a"] = "offline"
    m.ciclo()
    r.niveis["a"] = "erro"
    m.ciclo()
    assert m.eventos == []  # offline, depois erro: nenhum dos dois confirmou
    m.ciclo()
    assert _tipos(m) == [("a", "erro")]


def test_recuperacao_informa_quanto_tempo_ficou_fora() -> None:
    m, r = _monitor("a")
    m.ciclo()
    r.niveis["a"] = "offline"
    m.ciclo()
    m.ciclo()
    m.estados["a"].desde -= 12 * 60  # como se tivesse caído 12 minutos atrás
    r.niveis["a"] = "ok"
    m.ciclo()
    assert len(m.eventos) == 1  # ainda não confirmou a volta
    m.ciclo()
    assert _tipos(m) == [("a", "queda"), ("a", "recuperou")]
    assert "depois de 12 minuto(s)" in m.eventos[-1]["detalhe"]


def test_token_e_lentidao() -> None:
    m, r = _monitor("a", "b")
    m.ciclo()
    r.niveis.update(a="token", b="lento")
    r.ms = 2300
    m.ciclo()
    m.ciclo()
    assert sorted(_tipos(m)) == [("a", "token"), ("b", "lenta")]
    detalhes = {e["perfil"]: e["detalhe"] for e in m.eventos}
    assert "HTTP 401" in detalhes["a"]
    assert "2300 ms" in detalhes["b"]


def test_cada_perfil_tem_a_sua_contagem() -> None:
    m, r = _monitor("a", "b")
    m.ciclo()
    r.niveis["a"] = "offline"
    m.ciclo()
    r.niveis["b"] = "offline"
    m.ciclo()
    assert _tipos(m) == [("a", "queda")]  # b ainda não confirmou


def test_so_monitora_os_perfis_escolhidos() -> None:
    m, r = _monitor("a")
    r.niveis["b"] = "offline"
    m.ciclo()
    m.ciclo()
    assert m.eventos == [] and "b" not in m.historico


def test_historico_fica_limitado(monkeypatch: pytest.MonkeyPatch) -> None:
    from collections import deque

    m, _ = _monitor("a")
    m.historico["a"] = deque(maxlen=3)
    for _ in range(5):
        m.ciclo()
    assert len(m.historico["a"]) == 3


def test_eventos_ficam_limitados(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitor_mod, "MAX_EVENTOS", 3)
    m, _ = _monitor("a")
    for i in range(6):
        m._evento("a", "erro", "erro", f"e{i}")
    assert [e["detalhe"] for e in m.eventos] == ["e3", "e4", "e5"]
    assert m.eventos[-1]["id"] == 6


def test_snapshot() -> None:
    m, r = _monitor("a")
    r.niveis["a"] = "erro"
    m.ciclo()
    s = m.snapshot()
    assert s["perfis"] == ["a"] and s["ciclos"] == 1
    assert s["estado"]["a"]["nivel"] == "erro"
    assert s["historico"]["a"][0]["nivel"] == "erro"
    assert s["eventos"][0]["tipo"] == "erro" and s["ultimoEvento"] == 1


@pytest.mark.parametrize(
    ("perfis", "intervalo", "trecho"),
    [
        (set(), 5, "ao menos um perfil"),
        ({"zzz"}, 5, "Perfil desconhecido: zzz"),
        ({"a"}, 0, "entre 1 e 60"),
        ({"a"}, 61, "entre 1 e 60"),
    ],
)
def test_iniciar_valida(perfis: set[str], intervalo: int, trecho: str) -> None:
    m, _ = _monitor("a")
    with pytest.raises(ErroMonitor, match=trecho):
        m.iniciar(perfis, intervalo)


# ----------------------------------------------------------------------- painel ---


def _painel(app: App, forcar: bool = True) -> dict[str, dict[str, Any]]:
    r = app.http.get("/_painel" + ("?forcar=1" if forcar else ""))
    assert r.status_code == 200
    return {p["perfil"]: p for p in r.json()["perfis"]}


def test_painel_mostra_todos_os_perfis_de_uma_vez(app: App, upstream: Upstream) -> None:
    modelos = [{"id": 1, "geravelPorApi": True}, {"id": 2, "geravelPorApi": True}, {"id": 3}]
    upstream.respostas[("GET", "/api/modelos")] = (200, json.dumps({"modelos": modelos}))
    upstream.respostas[("GET", "/api/documentos")] = (
        200,
        json.dumps({"total": 7, "documentos": []}),
    )
    p = _painel(app)
    assert set(p) == {"cliente1", "ruim", "fora", "prod", "leitura"}
    ok = p["cliente1"]
    assert ok["nivel"] == "ok"
    assert ok["modelos"] == {"total": 3, "geraveis": 2}
    assert ok["pendentes"] == 7
    assert ok["instancia"] == "CLIENTE1"
    assert p["prod"]["protegido"] == "confirmar" and p["leitura"]["protegido"] == "bloquear"


def test_painel_perfis_com_problema_nao_gastam_consultas_extras(
    app: App, upstream: Upstream
) -> None:
    p = _painel(app)
    assert p["ruim"]["nivel"] == "token"
    assert p["ruim"]["modelos"] is None and p["ruim"]["pendentes"] is None
    assert p["fora"]["nivel"] == "offline"
    assert p["fora"]["pendentes"] is None


def test_painel_pede_so_os_pendentes_sem_baixar_a_lista(app: App, upstream: Upstream) -> None:
    _painel(app)
    consulta = next(c for c in upstream.recebidas if c["rota"] == "/api/documentos")["path"]
    assert "status=pendente" in consulta and "tamanho=1" in consulta


def test_painel_so_faz_leituras(app: App, upstream: Upstream) -> None:
    _painel(app)
    assert {c["method"] for c in upstream.recebidas} == {"GET"}


def test_painel_usa_cache_e_forcar_ignora(app: App, upstream: Upstream) -> None:
    _painel(app)
    n = len(upstream.recebidas)
    _painel(app, forcar=False)
    assert len(upstream.recebidas) == n  # veio do cache
    _painel(app)
    assert len(upstream.recebidas) > n


def test_painel_pendentes_ausentes_quando_a_consulta_falha(app: App, upstream: Upstream) -> None:
    upstream.respostas[("GET", "/api/documentos")] = (500, "")
    assert _painel(app)["cliente1"]["pendentes"] is None


def test_painel_exige_origem_da_pagina(app: App) -> None:
    assert app.http.get("/_painel", headers={"Host": "evil.example"}).status_code == 403


# ------------------------------------------------------------------ monitor (API) ---


def _iniciar(app: App, **extra: Any) -> Any:
    corpo = {"perfis": ["cliente1", "ruim", "fora"], "intervaloMin": 5, **extra}
    return app.http.post("/_monitor/iniciar", json=corpo)


def _esperar(app: App, ciclos: int, limite: float = 15.0) -> dict[str, Any]:
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        s = dict(app.http.get("/_monitor").json())
        if s["ciclos"] >= ciclos:
            return s
        time.sleep(0.03)
    raise AssertionError("o monitor não completou os ciclos a tempo")


def test_api_monitor_inicia_e_registra_a_base(app: App) -> None:
    assert _iniciar(app).json() == {"ok": True}
    s = _esperar(app, 1)
    assert s["ativo"] is True and s["perfis"] == ["cliente1", "fora", "ruim"]
    assert {n: e["nivel"] for n, e in s["estado"].items()} == {
        "cliente1": "ok",
        "ruim": "token",
        "fora": "offline",
    }
    tipos = {(e["perfil"], e["tipo"]) for e in s["eventos"]}
    assert tipos == {("ruim", "token"), ("fora", "queda")}  # o que já estava ruim avisa
    assert all(len(h) == 1 for h in s["historico"].values())
    assert s["proxima"] is not None
    app.http.post("/_monitor/parar", json={})


def test_api_monitor_agora_forca_uma_verificacao(app: App, upstream: Upstream) -> None:
    _iniciar(app, perfis=["cliente1"])
    _esperar(app, 1)
    assert app.http.post("/_monitor/agora", json={}).json() == {"ok": True}
    s = _esperar(app, 2)
    assert len(s["historico"]["cliente1"]) == 2
    app.http.post("/_monitor/parar", json={})


def test_api_monitor_parar(app: App) -> None:
    _iniciar(app, perfis=["cliente1"])
    _esperar(app, 1)
    app.http.post("/_monitor/parar", json={})
    s = app.http.get("/_monitor").json()
    assert s["ativo"] is False and s["proxima"] is None
    n = s["ciclos"]
    app.http.post("/_monitor/agora", json={})
    time.sleep(0.4)
    assert app.http.get("/_monitor").json()["ciclos"] == n  # parado não verifica


def test_api_monitor_reiniciar_troca_a_selecao(app: App) -> None:
    _iniciar(app, perfis=["cliente1"])
    _esperar(app, 1)
    _iniciar(app, perfis=["ruim"], intervaloMin=10)
    s = app.http.get("/_monitor").json()
    assert s["perfis"] == ["ruim"] and s["intervaloMin"] == 10
    app.http.post("/_monitor/parar", json={})


def test_api_monitor_sem_iniciar(app: App) -> None:
    s = app.http.get("/_monitor").json()
    assert s["ativo"] is False and s["eventos"] == [] and s["perfis"] == []


@pytest.mark.parametrize(
    "campos",
    [
        {"perfis": []},
        {"perfis": ["ninguem"]},
        {"perfis": "cliente1"},
        {"perfis": [1]},
        {"intervaloMin": 0},
        {"intervaloMin": "abc"},
    ],
)
def test_api_monitor_recusa_pedido_invalido(app: App, campos: dict[str, Any]) -> None:
    r = _iniciar(app, **campos)
    assert r.status_code == 400 and r.json()["ok"] is False
    assert app.http.get("/_monitor").json()["ativo"] is False


def test_api_monitor_exige_origem_da_pagina(app: App) -> None:
    ruim = {"Origin": "http://evil.example"}
    assert (
        app.http.post("/_monitor/iniciar", json={"perfis": ["cliente1"]}, headers=ruim).status_code
        == 403
    )
    assert app.http.post("/_monitor/parar", json={}, headers=ruim).status_code == 403
    assert app.http.get("/_monitor", headers={"Host": "evil.example"}).status_code == 403
