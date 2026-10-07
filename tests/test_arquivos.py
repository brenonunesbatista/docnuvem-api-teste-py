"""Gerador de PDFs de teste."""

from __future__ import annotations

import re

import pytest

from docnuvem_tester import arquivos
from docnuvem_tester.arquivos import gerar_pdf

from .conftest import App


def _validar(pdf: bytes, paginas: int) -> None:
    assert pdf.startswith(b"%PDF-1.4\n")
    assert pdf.rstrip().endswith(b"%%EOF")
    inicio_xref = int(re.search(rb"startxref\n(\d+)\n", pdf).group(1))  # type: ignore[union-attr]
    assert pdf[inicio_xref:].startswith(b"xref\n")
    total = int(re.search(rb"/Size (\d+)", pdf).group(1))  # type: ignore[union-attr]
    posicoes = re.findall(rb"(\d{10}) 00000 n \n", pdf)
    assert len(posicoes) == total - 1
    for n, pos in enumerate(posicoes, start=1):  # cada entrada da xref aponta para o objeto certo
        assert pdf[int(pos) :].startswith(f"{n} 0 obj\n".encode())
    assert int(re.search(rb"/Count (\d+)", pdf).group(1)) == paginas  # type: ignore[union-attr]
    assert len(re.findall(rb"/Type/Page/", pdf)) == paginas


@pytest.mark.parametrize(
    ("tamanho", "paginas"),
    [(1024, 1), (10 * 1024, 1), (100 * 1024, 3), (1024 * 1024, 10), (5 * 1024 * 1024, 1)],
)
def test_pdf_tem_o_tamanho_pedido_e_e_valido(tamanho: int, paginas: int) -> None:
    pdf = gerar_pdf(tamanho, paginas)
    assert len(pdf) == tamanho
    _validar(pdf, paginas)


def test_tamanhos_quebrados_tambem_fecham() -> None:
    for tamanho in (1500, 4097, 99999, 123457):
        assert len(gerar_pdf(tamanho, 2)) == tamanho


def test_limites() -> None:
    assert len(gerar_pdf(10, 1)) == 1024  # mínimo
    assert gerar_pdf(2048, 0).count(b"/Type/Page/") == 1  # ao menos uma página
    assert gerar_pdf(2048, 9999).count(b"/Type/Page/") == arquivos.PAGINAS_MAX
    assert len(gerar_pdf(10**10, 1)) == arquivos.TAMANHO_MAX


def test_paginas_demais_para_o_tamanho_devolvem_o_menor_possivel() -> None:
    pdf = gerar_pdf(1024, 50)  # 50 páginas não cabem em 1 KB
    assert len(pdf) > 1024
    _validar(pdf, 50)


def test_endpoint_devolve_o_pdf(app: App) -> None:
    r = app.http.get("/_arquivo-teste?tamanho=20480&paginas=2")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert len(r.content) == 20480
    _validar(r.content, 2)


def test_endpoint_usa_padroes(app: App) -> None:
    assert len(app.http.get("/_arquivo-teste").content) == 102400


def test_endpoint_recusa_parametro_ruim(app: App) -> None:
    assert app.http.get("/_arquivo-teste?tamanho=abc").status_code == 400
    assert app.http.get("/_arquivo-teste?paginas=x").status_code == 400


def test_endpoint_exige_origem_da_pagina(app: App) -> None:
    assert app.http.get("/_arquivo-teste", headers={"Host": "evil.example"}).status_code == 403
