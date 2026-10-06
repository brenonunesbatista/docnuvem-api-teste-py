"""Barra de menu fixa no topo, com a marca do app e as categorias."""

from __future__ import annotations

from dataclasses import dataclass

from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button, Static


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
        yield Static("[b #58a6ff]DocNuvem[/] [#8b949e]API Tester[/]", id="menu-brand")
        for entry in self._entries:
            yield Button(entry.label, id=f"menu-{entry.key}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id and event.button.id.startswith("menu-"):
            self.post_message(self.Selected(event.button.id.removeprefix("menu-")))
