"""Vigia de assinaturas pendentes."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

import pytest

from docnuvem_tester import vigia as vigia_mod
from docnuvem_tester.vigia import Vigia

from .conftest import App, Upstream

LISTAGEM = ("GET", "/api/documentos")


def _status_rota(id_: int) -> tuple[str, str]:
    return ("GET", f"/api/documento/{id_}/status")


def _sig(nome: str, status: str = "pendente", visualizou: bool = False) -> dict[str, Any]:
    email = f"{nome.lower()}@x.co"
    return {"nome": nome, "email": email, "status": status, "visualizou": visualizou}


def _doc(
    upstream: Upstream,
    id_: int,
    status: str = "pendente",
    *signatarios: dict[str, Any],
    validade: str = "",
    nome: str | None = None,
) -> None:
    corpo = {
        "documentoId": id_,
        "nomeArquivo": nome or f"doc{id_}.pdf",
        "status": status,
        "dataValidade": validade,
        "signatarios": list(signatarios) or [_sig("Ana")],
    }
    upstream.respostas[_status_rota(id_)] = (200, json.dumps(corpo))


def _pendentes(upstream: Upstream, *ids: int, total: int | None = None) -> None:
    docs = [{"documentoId": i, "nomeArquivo": f"doc{i}.pdf"} for i in ids]
    corpo = {"total": len(docs) if total is None else total, "documentos": docs}
    upstream.respostas[LISTAGEM] = (200, json.dumps(corpo))


def _vigia(app: App, alerta_h: int = 48, ids: set[int] | None = None) -> Vigia:
    """Um vigia sem thread: os testes chamam `ciclo()` quando querem."""
    perfil = app.servidor.cfg.perfis["cliente1"]
    return Vigia("cliente1", perfil, app.servidor.cliente, 10, alerta_h, ids or set())


def _tipos(v: Vigia) -> list[str]:
    return [e["tipo"] for e in v.eventos]


def _em(horas: float) -> str:
    return (datetime.now() + timedelta(hours=horas)).isoformat(timespec="seconds")


# ---------------------------------------------------------------- ciclos ---


def test_primeiro_ciclo_so_registra_a_base(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1, 2)
    _doc(upstream, 1)
    _doc(upstream, 2)
    v = _vigia(app)
    v.ciclo()
    assert sorted(v.monitorados) == [1, 2]
    assert v.eventos == []
    assert v.ciclos == 1


def test_documento_assinado_gera_eventos_e_sai_da_lista(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1)
    v = _vigia(app)
    v.ciclo()
    _doc(upstream, 1, "assinado", _sig("Ana", "assinado"))
    _pendentes(upstream)  # já não está entre os pendentes
    v.ciclo()
    assert _tipos(v) == ["signatario_assinou", "assinado"]
    assert v.monitorados == {}  # estado final: deixa de ser vigiado
    v.ciclo()
    assert len(v.eventos) == 2  # sem eventos repetidos


def test_signatario_que_visualiza(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1, "pendente", _sig("Ana"), _sig("Beto"))
    v = _vigia(app)
    v.ciclo()
    _doc(upstream, 1, "pendente", _sig("Ana"), _sig("Beto", visualizou=True))
    v.ciclo()
    assert _tipos(v) == ["signatario_visualizou"]
    assert v.eventos[0]["detalhe"] == "Beto visualizou."
    assert 1 in v.monitorados  # segue pendente


def test_assinatura_de_um_de_dois_signatarios(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1, "pendente", _sig("Ana"), _sig("Beto"))
    v = _vigia(app)
    v.ciclo()
    _doc(upstream, 1, "pendente", _sig("Ana", "assinado"), _sig("Beto"))
    v.ciclo()
    assert _tipos(v) == ["signatario_assinou"]
    assert 1 in v.monitorados


@pytest.mark.parametrize("final", ["cancelado", "expirado"])
def test_estados_finais(app: App, upstream: Upstream, final: str) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1)
    v = _vigia(app)
    v.ciclo()
    _doc(upstream, 1, final)
    v.ciclo()
    assert _tipos(v) == [final]
    assert "pendente → " + final in v.eventos[0]["detalhe"]
    assert v.monitorados == {}


def test_mudanca_para_outro_status(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1)
    v = _vigia(app)
    v.ciclo()
    _doc(upstream, 1, "em_analise")
    v.ciclo()
    assert _tipos(v) == ["status"]
    assert 1 in v.monitorados


def test_continua_consultando_quem_saiu_da_lista_de_pendentes(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1)
    v = _vigia(app)
    v.ciclo()
    _pendentes(upstream)  # a lista não traz mais o documento
    v.ciclo()
    assert 1 in v.monitorados  # ainda pendente: segue vigiado
    _doc(upstream, 1, "assinado", _sig("Ana", "assinado"))
    v.ciclo()
    assert "assinado" in _tipos(v)


def test_ids_manuais_sao_vigiados_mesmo_fora_da_lista(app: App, upstream: Upstream) -> None:
    _pendentes(upstream)  # nenhum pendente na lista
    _doc(upstream, 7)
    v = _vigia(app, ids={7})
    v.ciclo()
    assert list(v.monitorados) == [7]
    _doc(upstream, 7, "cancelado")
    v.ciclo()
    assert v.monitorados == {}
    assert 7 not in v.ids_manuais  # chegou ao fim: sai também dos ids manuais


def test_so_faz_leituras(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1)
    _vigia(app).ciclo()
    assert upstream.recebidas
    assert {c["method"] for c in upstream.recebidas} == {"GET"}


def test_lista_so_os_pendentes(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1)
    _vigia(app).ciclo()
    listagem = next(c for c in upstream.recebidas if c["rota"] == "/api/documentos")
    assert "status=pendente" in listagem["path"]
    assert "instancia=cliente1" in listagem["path"]


def test_truncado_quando_ha_mais_pendentes_que_o_limite(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1, total=250)
    _doc(upstream, 1)
    v = _vigia(app)
    v.ciclo()
    assert v.truncado is True


# ------------------------------------------------------------ vencimento ---


def test_aviso_de_expiracao_perto_do_prazo_uma_vez_so(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1, validade=_em(5))
    v = _vigia(app, alerta_h=48)
    v.ciclo()
    assert _tipos(v) == ["perto_de_expirar"]  # vale já na primeira verificação
    assert (
        "Expira em 4 hora(s)" in v.eventos[0]["detalhe"] or "5 hora(s)" in v.eventos[0]["detalhe"]
    )
    v.ciclo()
    v.ciclo()
    assert _tipos(v) == ["perto_de_expirar"]  # não repete a cada ciclo


def test_sem_aviso_quando_o_prazo_esta_longe(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1, validade=_em(24 * 20))
    v = _vigia(app, alerta_h=48)
    v.ciclo()
    assert v.eventos == []


def test_aviso_quando_o_prazo_ja_passou(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1, validade=_em(-3))
    v = _vigia(app)
    v.ciclo()
    assert v.eventos[0]["detalhe"] == "O prazo de validade já passou."


def test_documento_final_nao_gera_aviso_de_expiracao(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1, "assinado", validade=_em(2))
    v = _vigia(app, ids={1})
    v.ciclo()
    assert v.eventos == []


def test_validade_ilegivel_e_ignorada(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1, validade="amanha")
    v = _vigia(app)
    v.ciclo()
    assert v.eventos == []


# ----------------------------------------------------------------- erros ---


def test_erro_ao_listar_guarda_a_mensagem_e_o_estado(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1)
    v = _vigia(app)
    v.ciclo()
    upstream.respostas[LISTAGEM] = (500, "")
    v.ciclo()
    assert "Não consegui listar" in v.erro
    assert v.ciclos == 1  # o ciclo com erro não conta
    assert 1 in v.monitorados  # nada se perde
    _pendentes(upstream, 1)
    v.ciclo()
    assert v.erro == ""


def test_falha_num_documento_mantem_o_ultimo_estado(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1, 2)
    _doc(upstream, 1)
    _doc(upstream, 2)
    v = _vigia(app)
    v.ciclo()
    upstream.respostas[_status_rota(2)] = (500, "")
    v.ciclo()
    assert v.falhas_consulta == 1
    assert sorted(v.monitorados) == [1, 2]
    assert v.eventos == []  # a falha não vira evento nem apaga o estado


def test_eventos_ficam_limitados(
    app: App, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vigia_mod, "MAX_EVENTOS", 3)
    _pendentes(upstream, 1)
    _doc(upstream, 1, validade=_em(-1))
    v = _vigia(app)
    for i in range(6):
        v._evento("status", {"documentoId": 1, "nome": "x"}, f"e{i}")
    assert [e["detalhe"] for e in v.eventos] == ["e3", "e4", "e5"]
    assert v.eventos[-1]["id"] == 6  # a numeração continua


# ------------------------------------------------------------------- API ---


def _iniciar(app: App, **extra: Any) -> Any:
    corpo = {"perfil": "cliente1", "intervaloMin": 10, "alertaHoras": 48, "ids": "", **extra}
    return app.http.post("/_vigia/iniciar", json=corpo)


def _esperar_ciclos(app: App, n: int, perfil: str = "cliente1") -> dict[str, Any]:
    fim = time.monotonic() + 10
    while time.monotonic() < fim:
        for v in app.http.get("/_vigia").json()["vigias"]:
            if v["perfil"] == perfil and v["ciclos"] >= n:
                return dict(v)
        time.sleep(0.03)
    raise AssertionError("o vigia não completou os ciclos a tempo")


def test_api_inicia_e_mostra_o_que_esta_vigiando(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1, validade=_em(3))
    assert _iniciar(app).json() == {"ok": True}
    v = _esperar_ciclos(app, 1)
    assert v["ativo"] is True
    assert (v["intervaloMin"], v["alertaHoras"]) == (10, 48)
    assert [d["documentoId"] for d in v["monitorados"]] == [1]
    assert v["eventos"][0]["tipo"] == "perto_de_expirar"
    assert v["proxima"] is not None
    app.http.post("/_vigia/parar", json={"perfil": "cliente1"})


def test_api_agora_forca_uma_verificacao(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1)
    _iniciar(app)
    _esperar_ciclos(app, 1)
    _doc(upstream, 1, "assinado", _sig("Ana", "assinado"))
    assert app.http.post("/_vigia/agora", json={"perfil": "cliente1"}).json() == {"ok": True}
    v = _esperar_ciclos(app, 2)
    assert [e["tipo"] for e in v["eventos"]] == [
        "assinado",
        "signatario_assinou",
    ]  # mais novo primeiro
    app.http.post("/_vigia/parar", json={"perfil": "cliente1"})


def test_api_parar(app: App, upstream: Upstream) -> None:
    _pendentes(upstream)
    _iniciar(app)
    _esperar_ciclos(app, 1)
    assert app.http.post("/_vigia/parar", json={"perfil": "cliente1"}).json() == {"ok": True}
    assert app.http.get("/_vigia").json()["vigias"][0]["ativo"] is False


def test_api_ids_em_texto(app: App, upstream: Upstream) -> None:
    _pendentes(upstream)
    _doc(upstream, 7)
    _doc(upstream, 8)
    _iniciar(app, ids="7, 8;  7")
    v = _esperar_ciclos(app, 1)
    assert sorted(d["documentoId"] for d in v["monitorados"]) == [7, 8]
    app.http.post("/_vigia/parar", json={"perfil": "cliente1"})


def test_reiniciar_nao_perde_o_historico_de_eventos(app: App, upstream: Upstream) -> None:
    _pendentes(upstream, 1)
    _doc(upstream, 1, validade=_em(2))
    _iniciar(app)
    _esperar_ciclos(app, 1)
    antes = app.http.get("/_vigia").json()["vigias"][0]["eventos"]
    assert antes
    _iniciar(app, intervaloMin=30)
    v = _esperar_ciclos(app, 1)
    assert v["intervaloMin"] == 30
    assert v["eventos"][-1]["id"] == antes[-1]["id"]
    app.http.post("/_vigia/parar", json={"perfil": "cliente1"})


@pytest.mark.parametrize(
    "campos",
    [
        {"intervaloMin": 1},
        {"intervaloMin": 99999},
        {"alertaHoras": 0},
        {"alertaHoras": 99999},
        {"intervaloMin": "abc"},
        {"ids": "7, abc"},
        {"ids": "-3"},
    ],
)
def test_api_recusa_valores_invalidos(app: App, campos: dict[str, Any]) -> None:
    r = _iniciar(app, **campos)
    assert r.status_code == 400
    assert r.json()["ok"] is False
    assert app.http.get("/_vigia").json()["vigias"] == []


def test_api_perfil_desconhecido(app: App) -> None:
    assert _iniciar(app, perfil="ninguem").status_code == 404
    assert app.http.post("/_vigia/parar", json={"perfil": "ninguem"}).status_code == 404
    assert app.http.post("/_vigia/agora", json={"perfil": "ninguem"}).status_code == 404


def test_api_perfil_protegido_pode_vigiar(app: App, upstream: Upstream) -> None:
    """Vigiar só lê da API, então vale até para o perfil que bloqueia escritas."""
    _pendentes(upstream)
    assert _iniciar(app, perfil="leitura").json() == {"ok": True}
    app.http.post("/_vigia/parar", json={"perfil": "leitura"})


def test_api_exige_origem_da_pagina(app: App) -> None:
    ruim = {"Origin": "http://evil.example"}
    assert (
        app.http.post("/_vigia/iniciar", json={"perfil": "cliente1"}, headers=ruim).status_code
        == 403
    )
    assert (
        app.http.post("/_vigia/parar", json={"perfil": "cliente1"}, headers=ruim).status_code == 403
    )
    assert app.http.get("/_vigia", headers={"Host": "evil.example"}).status_code == 403
