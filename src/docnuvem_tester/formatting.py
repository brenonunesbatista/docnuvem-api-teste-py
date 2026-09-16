"""Helpers de formatação — nunca usar padding manual (ver lição da v1 Node)."""

from __future__ import annotations

import json
from typing import Any


def formatar_json(data: Any) -> str:
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def valor_ou_traco(v: Any) -> str:
    """Formata um valor para exibição em tabela/texto, tratando None/"" com segurança."""
    if v is None:
        return "—"  # —
    if isinstance(v, str) and v.strip() == "":
        return "—"
    return str(v)
