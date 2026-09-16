"""Barra de menu fixa no topo, estilo Turbo Pascal/Norton Commander."""

from __future__ import annotations

from dataclasses import dataclass

from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button


@dataclass(frozen=True)
class MenuEntry:
    key: str
    label: str


class MenuBar(Horizontal):
    """Linha de botões clicáveis/navegáveis por teclado no topo da tela."""

    class Selected(Message):
        def __init__(self, key: str) -> None:
            self.key = key
            super().__init__()

    def __init__(self, entries: list[MenuEntry]) -> None:
        self._entries = entries
        super().__init__()

    def compose(self):
        for entry in self._entries:
            yield Button(entry.label, id=f"menu-{entry.key}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id and event.button.id.startswith("menu-"):
            self.post_message(self.Selected(event.button.id.removeprefix("menu-")))
