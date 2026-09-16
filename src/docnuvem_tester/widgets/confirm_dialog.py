"""Modais simples: confirmação (sim/não) e alerta (validação local)."""

from __future__ import annotations

from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmScreen(ModalScreen[bool]):
    """Pergunta 'Tem certeza? Sim/Não' — usado antes de ações destrutivas."""

    BINDINGS = [("escape", "cancelar", "Voltar")]

    def __init__(self, titulo: str, mensagem: str) -> None:
        self._titulo = titulo
        self._mensagem = mensagem
        super().__init__()

    def compose(self):
        with Container(classes="dos-window"):
            yield Static(self._titulo, classes="dos-window-title")
            yield Static(self._mensagem, classes="confirm-message")
            with Horizontal(classes="confirm-actions"):
                yield Button("Sim", id="confirm-yes", classes="danger")
                yield Button("Não", id="confirm-no", classes="secondary")

    def on_mount(self) -> None:
        self.query_one("#confirm-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_cancelar(self) -> None:
        self.dismiss(False)


class AlertScreen(ModalScreen[None]):
    """Alerta de validação local antes de disparar uma chamada à API."""

    BINDINGS = [("escape", "fechar", "Fechar"), ("enter", "fechar", "Fechar")]

    def __init__(self, titulo: str, mensagens: list[str]) -> None:
        self._titulo = titulo
        self._mensagens = mensagens
        super().__init__()

    def compose(self):
        with Container(classes="dos-window"):
            yield Static(self._titulo, classes="dos-window-title")
            with VerticalScroll():
                for msg in self._mensagens:
                    yield Static(f"• {msg}", classes="alert-message")
            yield Button("OK", id="alert-ok", classes="primary")

    def on_mount(self) -> None:
        self.query_one("#alert-ok", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_fechar(self) -> None:
        self.dismiss(None)
