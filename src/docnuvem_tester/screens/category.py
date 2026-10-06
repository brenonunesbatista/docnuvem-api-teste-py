"""Submenu de categoria: lista de ações que abrem outra tela ao confirmar."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from textual.containers import Container
from textual.screen import ModalScreen, Screen
from textual.widgets import Label, ListItem, ListView, Static


@dataclass(frozen=True)
class CategoryItem:
    label: str
    factory: Callable[[], Screen]


class CategoryScreen(ModalScreen[None]):
    """Lista de sub-telas de uma categoria do menu principal (ex.: Importar)."""

    BINDINGS = [("escape", "voltar", "Voltar")]

    def __init__(self, titulo: str, itens: list[CategoryItem]) -> None:
        self._titulo = titulo
        self._itens = itens
        super().__init__()

    def compose(self):
        with Container(classes="card"):
            yield Static(self._titulo, classes="card-title")
            with ListView(id="category-list"):
                for item in self._itens:
                    yield ListItem(Label(item.label))

    def on_mount(self) -> None:
        self.query_one("#category-list", ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is not None:
            self.app.push_screen(self._itens[index].factory())

    def action_voltar(self) -> None:
        self.dismiss(None)
