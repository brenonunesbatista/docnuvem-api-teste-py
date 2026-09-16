"""Helper para linhas de formulário padronizadas (label à esquerda, campo à direita)."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
from textual.containers import Horizontal
from textual.widgets import Button, Input, Static


def campo(label: str, input_id: str, placeholder: str = "", value: str = "") -> Horizontal:
    return Horizontal(
        Static(label, classes="form-label"),
        Input(placeholder=placeholder, id=input_id, value=value),
        classes="form-row",
    )


def campo_arquivo(label: str, input_id: str, placeholder: str = "") -> Horizontal:
    """Linha com Input de caminho + botão que abre o explorador nativo do SO."""
    return Horizontal(
        Static(label, classes="form-label"),
        Input(placeholder=placeholder, id=input_id),
        Button("Selecionar arquivo...", id=f"{input_id}-picker", classes="secondary"),
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

    def _e_botao_seletor_arquivo(self, button_id: str | None, id_caminho: str) -> bool:
        return button_id == f"{id_caminho}-picker"

    async def _abrir_seletor_arquivo(self, id_caminho: str) -> None:
        """Abre o explorador nativo do SO e, se um arquivo for escolhido, preenche
        o Input de caminho (o que por sua vez dispara o autofill de nomeArquivo
        via `_tratar_input_changed_autofill`, chamado a partir de on_input_changed)."""
        from docnuvem_tester.widgets.file_picker import escolher_arquivo

        caminho = await escolher_arquivo(self.app, "Selecionar arquivo")  # type: ignore[attr-defined]
        if caminho:
            self.query_one(f"#{id_caminho}", Input).value = caminho  # type: ignore[attr-defined]
