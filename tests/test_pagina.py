"""Consistência entre a página, o proxy e o verificador de contrato."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from docnuvem_tester.spec import COBERTOS
from docnuvem_tester.web import PAGINA, ROTAS_PERMITIDAS

RAIZ = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def html() -> str:
    return PAGINA.read_text(encoding="utf-8")


def _permitida(metodo: str, caminho: str) -> bool:
    return any(m == metodo and rx.match(caminho) for m, rx in ROTAS_PERMITIDAS)


def test_toda_chamada_literal_da_pagina_esta_liberada_no_proxy(html: str) -> None:
    # Só caminhos completos: os que a página monta com "+id" ficam de fora.
    chamadas = re.findall(r"method:'(GET|POST|DELETE)',path:'(/[^']*)'(?!\s*\+)", html)
    assert len(chamadas) >= 8, "o padrão de busca deixou de achar as chamadas da página"
    for metodo, caminho in chamadas:
        exemplo = re.sub(r"\{[^}]+\}", "123", caminho)  # os títulos mostram {id}
        assert _permitida(metodo, exemplo), f"{metodo} {caminho} não está em ROTAS_PERMITIDAS"


def test_todo_endpoint_conhecido_esta_liberado_no_proxy() -> None:
    for metodo, caminho in COBERTOS:
        exemplo = re.sub(r"\{[^}]+\}", "123", caminho)
        assert _permitida(metodo, exemplo), f"{metodo} {caminho} não está em ROTAS_PERMITIDAS"


def test_proxy_nao_libera_nada_alem_do_que_o_contrato_conhece() -> None:
    conhecidos = [(m, re.sub(r"\{[^}]+\}", "123", c)) for m, c in COBERTOS]
    for metodo, rx in ROTAS_PERMITIDAS:
        assert any(m == metodo and rx.match(c) for m, c in conhecidos), (
            f"{metodo} {rx.pattern} está liberada, mas não consta em COBERTOS"
        )


def test_pagina_nao_tem_token_nem_segredo(html: str) -> None:
    assert not re.search(r"Bearer [A-Za-z0-9._-]{12,}", html)
    assert "dnk_" not in html


def test_grafia_da_marca() -> None:
    """A marca é sempre 'Docnuvem' (nunca 'DocNuvem') em textos e documentação."""
    arquivos = [PAGINA, RAIZ / "README.md", *RAIZ.glob("docs/*.md"), *RAIZ.glob("src/**/*.py")]
    for arq in arquivos:
        assert "DocNuvem" not in arq.read_text(encoding="utf-8"), f"'DocNuvem' em {arq.name}"
