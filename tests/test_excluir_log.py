"""Excluir em massa: as falhas vão para o log (histórico) da ferramenta."""

from __future__ import annotations

from typing import Any

from .conftest import TOKEN_OK, App, Upstream
from .test_excluir import EXCLUIR, _docs, _excluir, _tres


def _log(app: App) -> list[dict[str, Any]]:
    assert app.historico is not None
    return [e for e in app.historico.todas() if e.get("screen") == "excluir"]


def test_falha_na_exclusao_vai_para_o_log(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    upstream.dinamicas[EXCLUIR] = lambda q: (500, "") if q["documentoId"][0] == "2" else (200, "{}")
    _excluir(app)
    falha, resumo = _log(app)
    assert (falha["method"], falha["status"], falha["perfil"]) == ("POST", 500, "cliente1")
    assert "documentoId=2" in falha["url"] and "instancia=cliente1" in falha["url"]
    assert "assinado.pdf" in falha["body"]["retorno"]
    assert falha["rv"]["headers"][0]["k"] == "Authorization"  # a página sabe abrir esta entrada
    assert (
        "1 falha(s)" in resumo["body"]["retorno"] and "2 excluído(s)" in resumo["body"]["retorno"]
    )


def test_o_log_nunca_leva_o_token(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    upstream.respostas[EXCLUIR] = (401, '{"retorno": "exige login"}')
    _excluir(app)
    bruto = app.historico.arquivo.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert TOKEN_OK not in bruto
    assert "Bearer ***" in bruto


def test_sem_problemas_nada_vai_para_o_log(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    _excluir(app)
    assert _log(app) == []


def test_falha_ao_cancelar_o_fluxo_tambem_fica_no_log(app: App, upstream: Upstream) -> None:
    _docs(upstream, (1, "ativo.pdf", "pendente", 101))
    upstream.respostas[("DELETE", "/api/assinatura/101")] = (500, '{"retorno": "falhou"}')
    _excluir(app, confirmacao="EXCLUIR 1")
    falha = _log(app)[0]
    assert falha["method"] == "DELETE" and falha["status"] == 500
    assert "/api/assinatura/101" in falha["url"]


def test_o_log_tem_limite_por_exclusao(app: App, upstream: Upstream, monkeypatch: Any) -> None:
    from docnuvem_tester import excluir

    monkeypatch.setattr(excluir, "MAX_FALHAS_NO_LOG", 2)
    docs = [(i, f"d{i}.pdf", "sem_assinatura", None) for i in range(1, 9)]
    _docs(upstream, *docs)
    upstream.dinamicas[EXCLUIR] = lambda q: (
        (500, "") if int(q["documentoId"][0]) % 2 else (200, "{}")
    )
    _excluir(app, confirmacao="EXCLUIR 8", intervaloMs=0)
    falhas = [e for e in _log(app) if e["method"] == "POST" and e["status"] == 500]
    assert len(falhas) == 2
