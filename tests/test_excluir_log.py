"""Excluir em massa: as falhas vão para o log (histórico) da ferramenta."""

from __future__ import annotations

from typing import Any

from .conftest import TOKEN_OK, App, Upstream
from .test_excluir import EXCLUIR, _docs, _excluir, _tres


def _log(app: App) -> list[dict[str, Any]]:
    assert app.historico is not None
    return [
        e
        for e in app.historico.todas()
        if e.get("screen") == "excluir" and "iniciada" not in str(e["body"])
    ]


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


def _esperar_log(app: App, trecho: str, limite: float = 8.0) -> list[dict[str, Any]]:
    import time

    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        itens = app.historico.todas() if app.historico else []
        if any(trecho in str(e.get("body")) for e in itens):
            return itens
        time.sleep(0.1)
    raise AssertionError(f"o log não ganhou {trecho!r}")


def test_o_inicio_da_exclusao_fica_no_log_com_os_filtros(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    _excluir(app, status="pendente")
    textos = [e["body"]["retorno"] for e in app.historico.todas()]  # type: ignore[union-attr]
    assert any("iniciada" in t and "status pendente" in t and "pasta 5" in t for t in textos)


def test_cancelar_a_exclusao_fica_no_log(app: App, upstream: Upstream) -> None:
    from .test_excluir import _esperar, _iniciar
    from .test_excluir_leitura import _aguardar_execucao

    _tres(upstream)
    r = _iniciar(app, intervaloMs=2000)
    _aguardar_execucao(app, r["id"])
    app.http.post(f"/_excluir/{r['id']}/cancelar")
    _esperar(app, r["id"])
    assert any("cancelado" in e["body"]["retorno"] for e in _log(app))


def test_login_vai_para_o_log_sem_senha_e_sem_token(app_cfg: App, upstream: Upstream) -> None:
    from .test_login import SENHA, TOKEN_USUARIO, _entrar, _login_ok

    _login_ok(upstream)
    assert _entrar(app_cfg, senha="errada").status_code == 401
    assert _entrar(app_cfg).status_code == 200
    textos = [str(e["body"]) for e in app_cfg.historico.todas()]  # type: ignore[union-attr]
    assert any("recusado" in t for t in textos) and any(
        "Login de" in t and "feito" in t for t in textos
    )
    bruto = app_cfg.historico.arquivo.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert SENHA not in bruto and TOKEN_USUARIO not in bruto and "errada" not in bruto
    assert "senha=" not in bruto


def test_falhas_e_resumo_do_lote_vao_para_o_log(app: App, upstream: Upstream) -> None:
    from .test_rotina import OUTRO, SOLICITAR, _aluno, _csv, _lote

    upstream.respostas[SOLICITAR] = (422, '{"retorno": "Modelo não encontrado"}')
    r = _lote(app, _csv(_aluno(1), _aluno(2, OUTRO)))
    assert r["ok"] is True
    itens = _esperar_log(app, "Lote (enviar) concluido")
    textos = [str(e["body"]["retorno"]) for e in itens if e.get("screen") == "excluir"]
    assert any("iniciada" in t for t in textos)
    assert any("Modelo não encontrado" in t for t in textos)
    assert any("2 falha(s)" in t for t in textos)
