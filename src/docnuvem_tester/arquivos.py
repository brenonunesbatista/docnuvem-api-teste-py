"""Gerador de PDFs de teste válidos, em vários tamanhos e números de páginas."""

from __future__ import annotations

TAMANHO_MIN = 1024
TAMANHO_MAX = 40 * 1024 * 1024  # abaixo do limite de 50 MB do proxy local
PAGINAS_MAX = 50
_ENCHIMENTO = b"Docnuvem API Tester - preenchimento do arquivo de teste.\n"


def _montar(paginas: int, enchimento: int) -> bytes:
    """PDF com N páginas e um objeto extra (stream) de `enchimento` bytes para ajustar o peso."""
    # 1 = catálogo, 2 = páginas, 3 = fonte, depois (página, conteúdo) por página, e o enchimento.
    n_obj = 3 + 2 * paginas + 1
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(paginas))
    objetos: list[bytes] = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        f"<</Type/Pages/Kids[{kids}]/Count {paginas}>>".encode(),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    for i in range(paginas):
        texto = f"BT /F1 18 Tf 50 150 Td (Docnuvem API Tester - pagina {i + 1} de {paginas}) Tj ET"
        conteudo = texto.encode()
        objetos.append(
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 400 300]"
            f"/Resources<</Font<</F1 3 0 R>>>>/Contents {5 + 2 * i} 0 R>>".encode()
        )
        objetos.append(
            f"<</Length {len(conteudo)}>>\nstream\n".encode() + conteudo + b"\nendstream"
        )
    bloco = (_ENCHIMENTO * (enchimento // len(_ENCHIMENTO) + 1))[:enchimento]
    objetos.append(f"<</Length {enchimento}>>\nstream\n".encode() + bloco + b"\nendstream")

    saida = bytearray(b"%PDF-1.4\n")
    posicoes = []
    for i, obj in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(saida)
    saida += f"xref\n0 {n_obj + 1}\n".encode() + b"0000000000 65535 f \n"
    for pos in posicoes:
        saida += f"{pos:010d} 00000 n \n".encode()
    saida += f"trailer\n<</Size {n_obj + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(saida)


def gerar_pdf(tamanho: int, paginas: int = 1) -> bytes:
    """PDF válido com aproximadamente `tamanho` bytes (exato quando cabe).

    O tamanho é limitado entre 1 KB e 40 MB; se o pedido for menor que o mínimo possível para
    o número de páginas, devolve o menor PDF possível.
    """
    paginas = max(1, min(PAGINAS_MAX, paginas))
    tamanho = max(TAMANHO_MIN, min(TAMANHO_MAX, tamanho))
    base = len(_montar(paginas, 0))
    enchimento = max(0, tamanho - base)
    pdf = _montar(paginas, enchimento)
    # O número de dígitos do /Length e das posições pode mudar o total em poucos bytes.
    for _ in range(4):
        diferenca = tamanho - len(pdf)
        if diferenca == 0 or enchimento + diferenca < 0:
            break
        enchimento += diferenca
        pdf = _montar(paginas, enchimento)
    return pdf
