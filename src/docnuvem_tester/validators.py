"""Validadores de campo para os Input do Textual (feedback inline, enquanto digita).

Todos tratam valor vazio como válido: "obrigatório" continua sendo checado no
envio (FormScreen.validar), para não gritar com campos que o usuário ainda
nem tocou.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from textual.validation import Function

from docnuvem_tester.models import tem_extensao, validar_cpf, validar_data_br, validar_numero_br

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _ou_vazio(fn: Callable[[str], bool]) -> Callable[[str], bool]:
    return lambda valor: not valor.strip() or fn(valor.strip())


def so_digitos(rotulo: str) -> Function:
    return Function(_ou_vazio(str.isdigit), f"{rotulo}: use apenas números")


def inteiro_entre(rotulo: str, minimo: int, maximo: int) -> Function:
    return Function(
        _ou_vazio(lambda v: v.isdigit() and minimo <= int(v) <= maximo),
        f"{rotulo}: informe um número entre {minimo} e {maximo}",
    )


def cpf_valido() -> Function:
    return Function(_ou_vazio(validar_cpf), "CPF inválido (confira o dígito verificador)")


def email_valido() -> Function:
    return Function(_ou_vazio(lambda v: bool(_EMAIL_RE.match(v))), "E-mail inválido")


def arquivo_existente() -> Function:
    return Function(_ou_vazio(lambda v: Path(v).is_file()), "Caminho: arquivo não encontrado")


def nome_com_extensao() -> Function:
    return Function(
        _ou_vazio(tem_extensao), "Nome do arquivo: precisa de extensão (ex.: Contrato.pdf)"
    )


def data_br(rotulo: str) -> Function:
    return Function(_ou_vazio(validar_data_br), f"{rotulo}: use o formato dd/MM/aaaa")


def numero_br(rotulo: str) -> Function:
    return Function(_ou_vazio(validar_numero_br), f"{rotulo}: use o formato 1.234,56")


def data_iso(rotulo: str) -> Function:
    def _ok(valor: str) -> bool:
        try:
            datetime.strptime(valor, "%Y-%m-%d")
        except ValueError:
            return False
        return True

    return Function(_ou_vazio(_ok), f"{rotulo}: use o formato yyyy-MM-dd")


def max_caracteres(rotulo: str, limite: int) -> Function:
    return Function(
        lambda valor: len(valor) <= limite, f"{rotulo}: no máximo {limite} caracteres"
    )


def lista_inteiros(rotulo: str) -> Function:
    return Function(
        _ou_vazio(lambda v: all(p.strip().isdigit() for p in v.split(",") if p.strip())),
        f"{rotulo}: use números separados por vírgula (ex.: 1,7)",
    )
