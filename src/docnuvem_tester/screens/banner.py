"""Tela de abertura: marca do app e dica para continuar."""

from __future__ import annotations

from textual.events import Key, MouseDown
from textual.screen import Screen
from textual.widgets import Static

MARCA = (
    "[b #58a6ff]Docnuvem[/]\n"
    "[b]API Tester[/]\n"
    "\n"
    "[#8b949e]Teste manual da API REST  ·  v1.0[/]\n"
    "\n"
    "\n"
    "[#8b949e]Pressione qualquer tecla para começar[/]"
)


class BannerScreen(Screen[None]):
    """Primeira tela exibida: qualquer tecla ou clique avança para o menu principal."""

    def compose(self):
        yield Static(MARCA, id="banner-art")

    def on_key(self, event: Key) -> None:
        self._avancar()

    def on_mouse_down(self, event: MouseDown) -> None:
        self._avancar()

    def _avancar(self) -> None:
        from docnuvem_tester.screens.main_menu import MainMenuScreen

        self.app.switch_screen(MainMenuScreen())
