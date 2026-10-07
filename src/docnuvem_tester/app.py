"""App Textual principal: liga config, cliente HTTP e navegação por telas."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding

from docnuvem_tester.client import CallLogEntry, DocnuvemClient
from docnuvem_tester.config import AppConfig, ConfigError, PerfilConfig, load_config
from docnuvem_tester.theme import DOCNUVEM_THEME


class DocnuvemTesterApp(App[None]):
    """TUI para testar manualmente a API do Docnuvem."""

    CSS_PATH = str(Path(__file__).parent / "styles" / "app.tcss")
    TITLE = "Docnuvem API Tester"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("f1", "help", "Ajuda", show=True),
        Binding("l", "call_log", "Log", show=True),
        Binding("f10", "quit", "Sair", show=True, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.register_theme(DOCNUVEM_THEME)
        self.theme = DOCNUVEM_THEME.name
        self.config: AppConfig | None = None
        self.config_error: str | None = None
        self.perfil_atual: str | None = None
        self.call_log: list[CallLogEntry] = []
        self.ultimo_documento_id: int | None = None
        self.client = DocnuvemClient(
            on_call_logged=self.call_log.append,
            on_documento_id=self._guardar_documento_id,
        )

    def _guardar_documento_id(self, documento_id: int) -> None:
        self.ultimo_documento_id = documento_id

    @property
    def ultima_chamada(self) -> CallLogEntry | None:
        return self.call_log[-1] if self.call_log else None

    @property
    def perfil_ativo(self) -> PerfilConfig | None:
        if self.config is None or self.perfil_atual is None:
            return None
        return self.config.perfis.get(self.perfil_atual)

    def on_mount(self) -> None:
        try:
            self.config = load_config()
            if self.config.perfilPadrao in self.config.perfis:
                self.perfil_atual = self.config.perfilPadrao
            elif self.config.perfis:
                self.perfil_atual = next(iter(self.config.perfis))
        except ConfigError as exc:
            self.config = None
            self.config_error = str(exc)

        from docnuvem_tester.screens.banner import BannerScreen

        self.push_screen(BannerScreen())

    def action_help(self) -> None:
        from docnuvem_tester.screens.help import HelpScreen

        self.push_screen(HelpScreen())

    def action_call_log(self) -> None:
        from docnuvem_tester.screens.call_log import CallLogScreen

        self.push_screen(CallLogScreen())

    async def action_quit(self) -> None:
        from docnuvem_tester.widgets.confirm_dialog import ConfirmScreen

        ok = await self.push_screen_wait(
            ConfirmScreen("Sair", "Tem certeza que deseja sair do Docnuvem API Tester?")
        )
        if ok:
            await self.client.aclose()
            self.exit()


def main() -> None:
    DocnuvemTesterApp().run()


if __name__ == "__main__":
    main()
