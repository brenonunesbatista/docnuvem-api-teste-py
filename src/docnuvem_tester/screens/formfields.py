"""Helper para linhas de formulário padronizadas (label à esquerda, campo à direita)."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
from textual.containers import Horizontal
from textual.widgets import Input, Static


def campo(label: str, input_id: str, placeholder: str = "", value: str = "") -> Horizontal:
    return Horizontal(
        Static(label, classes="form-label"),
        Input(placeholder=placeholder, id=input_id, value=value),
        classes="form-row",
    )


def erros_pydantic(exc: ValidationError) -> list[str]:
    """Extrai mensagens legíveis de um ValidationError do pydantic v2."""
    mensagens = []
    for erro in exc.errors():
        msg = str(erro["msg"])
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        campo_path = ".".join(str(p) for p in erro["loc"])
        mensagens.append(f"{campo_path}: {msg}" if campo_path else msg)
    return mensagens


class AutoFillNomeArquivoMixin:
    """Preenche nomeArquivo a partir do caminho do arquivo, a menos que o usuário
    já tenha editado o campo manualmente (lição da v1 Node: não sobrescrever
    edição manual do usuário)."""

    def _init_autofill(self) -> None:
        self._nome_editado_manualmente = False
        self._preenchendo_automaticamente = False

    def _tratar_input_changed_autofill(
        self, event: Input.Changed, id_caminho: str, id_nome: str
    ) -> None:
        if event.input.id == id_caminho:
            if not self._nome_editado_manualmente:
                nome = Path(event.value).name
                self._preenchendo_automaticamente = True
                self.query_one(f"#{id_nome}", Input).value = nome  # type: ignore[attr-defined]
                self._preenchendo_automaticamente = False
        elif event.input.id == id_nome:
            if not self._preenchendo_automaticamente:
                self._nome_editado_manualmente = True
