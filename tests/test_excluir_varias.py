"""Excluir em massa: várias pastas marcadas dentro de uma pasta pai."""

from __future__ import annotations

import json
from typing import Any

from .conftest import App, Upstream
from .test_excluir import (
    EXCLUIR,
    LISTAGEM,
    _consulta,
    _destrutivas,
    _esperar,
    _iniciar,
)

# pasta -> ids dos documentos que ela tem
PASTAS = {"5": [1, 2, 3], "6": [11, 12], "7": []}


def _por_pasta(upstream: Upstream, pastas: dict[str, list[int]] = PASTAS) -> None:
    def resposta(q: dict[str, list[str]]) -> tuple[int, str]:
        ids = pastas.get(q["diretorioId"][0], [])
        ini = int(q["pagina"][0]) * int(q["tamanho"][0])
        parte = ids[ini : ini + int(q["tamanho"][0])]
        docs = [
            {"documentoId": i, "nomeArquivo": f"d{i}.pdf", "status": "sem_assinatura"}
            for i in parte
        ]
        corpo = {"total": len(ids), "temMais": ini + len(parte) < len(ids), "documentos": docs}
        return 200, json.dumps(corpo)

    upstream.dinamicas[LISTAGEM] = resposta


def _ensaio(app: App, ids: list[Any], **extra: Any) -> Any:
    corpo = {"perfil": "cliente1", "diretorioIds": ids, "cancelarFluxos": True, **extra}
    return app.http.post("/_excluir/ensaio", json=corpo)


def _varias(app: App, ids: list[Any], confirmacao: str, **extra: Any) -> dict[str, Any]:
    r = _iniciar(app, diretorioIds=ids, confirmacao=confirmacao, **extra)
    assert r["ok"] is True, r
    return _esperar(app, r["id"])


def test_ensaio_soma_as_pastas_marcadas(app: App, upstream: Upstream) -> None:
    _por_pasta(upstream)
    r = _ensaio(app, [5, 6, 7]).json()
    assert r["total"] == 5
    assert r["confirmacao"] == "EXCLUIR 5"


def test_exclui_os_documentos_de_todas_as_pastas_marcadas(app: App, upstream: Upstream) -> None:
    _por_pasta(upstream)
    fim = _varias(app, [5, 6], "EXCLUIR 5")
    assert (fim["estado"], fim["excluidos"], fim["diretorioIds"]) == ("concluido", 5, [5, 6])
    ids = {_consulta(c)["documentoId"] for c in _destrutivas(upstream) if c["rota"] == EXCLUIR[1]}
    assert ids == {"1", "2", "3", "11", "12"}


def test_pasta_nao_marcada_fica_intacta(app: App, upstream: Upstream) -> None:
    _por_pasta(upstream, {"5": [1, 2], "6": [11], "8": [99]})
    _varias(app, [5, 6], "EXCLUIR 3")
    ids = {_consulta(c)["documentoId"] for c in _destrutivas(upstream)}
    assert "99" not in ids


def test_confirmacao_vale_para_o_total_de_todas(app: App, upstream: Upstream) -> None:
    _por_pasta(upstream)
    r = _iniciar(app, diretorioIds=[5, 6], confirmacao="EXCLUIR 3")  # só a primeira pasta
    assert r["_status"] == 428
    assert _destrutivas(upstream) == []


def test_pasta_repetida_conta_uma_vez(app: App, upstream: Upstream) -> None:
    _por_pasta(upstream)
    assert _ensaio(app, [5, 5, 6]).json()["total"] == 5


def test_verificacao_de_varias_pastas_e_reaproveitada(app: App, upstream: Upstream) -> None:
    _por_pasta(upstream)
    corpo = {"perfil": "cliente1", "diretorioIds": [5, 6], "cancelarFluxos": True}
    v = app.http.post("/_excluir/verificar", json=corpo).json()
    for _ in range(200):
        sn = app.http.get(f"/_excluir/verificacao/{v['id']}").json()
        if sn["estado"] != "lendo":
            break
    assert sn["estado"] == "pronta" and sn["resultado"]["total"] == 5
    antes = len([c for c in upstream.recebidas if c["rota"] == "/api/documentos"])
    fim = _varias(app, [5, 6], "EXCLUIR 5", verificacaoId=v["id"])
    depois = len([c for c in upstream.recebidas if c["rota"] == "/api/documentos"])
    assert fim["excluidos"] == 5
    assert depois - antes == 2  # só a contagem de cada pasta; a lista veio da verificação


def test_selecao_diferente_nao_reaproveita_a_verificacao(app: App, upstream: Upstream) -> None:
    _por_pasta(upstream)
    corpo = {"perfil": "cliente1", "diretorioIds": [5], "cancelarFluxos": True}
    v = app.http.post("/_excluir/verificar", json=corpo).json()
    for _ in range(200):
        if app.http.get(f"/_excluir/verificacao/{v['id']}").json()["estado"] != "lendo":
            break
    fim = _varias(app, [5, 6], "EXCLUIR 5", verificacaoId=v["id"])
    assert fim["excluidos"] == 5  # leu as duas pastas de novo


def test_recusa_raiz_vazio_e_valor_invalido(app: App, upstream: Upstream) -> None:
    _por_pasta(upstream)
    assert _ensaio(app, [5, 0]).status_code == 400
    assert _ensaio(app, []).status_code == 400
    assert _ensaio(app, ["x"]).status_code == 400
    assert _destrutivas(upstream) == []


def test_limite_de_pastas_por_vez(app: App, upstream: Upstream) -> None:
    _por_pasta(upstream)
    r = _ensaio(app, list(range(1, 600)))
    assert r.status_code == 400 and "no máximo" in r.json()["erro"]


def test_o_log_cita_as_pastas(app: App, upstream: Upstream) -> None:
    _por_pasta(upstream)
    _varias(app, [5, 6], "EXCLUIR 5")
    textos = [e["body"]["retorno"] for e in app.historico.todas()]  # type: ignore[union-attr]
    assert any("iniciada" in t and "2 pastas (5, 6)" in t for t in textos)
