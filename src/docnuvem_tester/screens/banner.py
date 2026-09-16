"""Tela de abertura (banner ASCII), estilo boot screen de DOS."""

from __future__ import annotations

from textual.events import Key, MouseDown
from textual.screen import Screen
from textual.widgets import Static

ASCII_ART = r"""
 ____   ___   ____  _   _ _   ___     _______ __  __
|  _ \ / _ \ / ___|| \ | | | | \ \   / / ____|  \/  |
| | | | | | | |    |  \| | | | |\ \ / /|  _| | |\/| |
| |_| | |_| | |___ | |\  | |_| | \ V / | |___| |  | |
|____/ \___/ \____||_| \_|\___/   \_/  |_____|_|  |_|

           A P I   T E S T E R   v1.0
"""


class BannerScreen(Screen[None]):
    """Primeira tela exibida: qualquer tecla ou clique avança para o menu principal."""

    def compose(self):
        yield Static(ASCII_ART, id="banner-art")
        yield Static("Pressione qualquer tecla ou clique para continuar...", id="banner-hint")

    def on_key(self, event: Key) -> None:
        self._avancar()

    def on_mouse_down(self, event: MouseDown) -> None:
        self._avancar()

    def _avancar(self) -> None:
        from docnuvem_tester.screens.main_menu import MainMenuScreen

        self.app.switch_screen(MainMenuScreen())
