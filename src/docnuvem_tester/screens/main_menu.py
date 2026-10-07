"""Menu principal: barra de categorias no topo + atalhos globais no rodapé."""

from __future__ import annotations

from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Footer, Static

from docnuvem_tester.screens.category import CategoryItem, CategoryScreen
from docnuvem_tester.widgets.confirm_dialog import ConfirmScreen
from docnuvem_tester.widgets.menu_bar import MenuBar, MenuEntry

MENU_ENTRIES = [
    MenuEntry("importar", "Importar"),
    MenuEntry("modelos", "Modelos"),
    MenuEntry("assinatura", "Assinatura"),
    MenuEntry("documentos", "Documentos"),
    MenuEntry("fluxo", "Fluxo Completo"),
    MenuEntry("perfil", "Perfil"),
    MenuEntry("sair", "Sair"),
]


class MainMenuScreen(Screen[None]):
    """Tela raiz da aplicação: menu de categorias, sempre no topo da pilha."""

    def compose(self):
        yield MenuBar(MENU_ENTRIES)
        with Container(id="home", classes="card"):
            yield Static("Docnuvem API Tester", id="main-menu-title", classes="card-title")
            yield Static(self._texto_perfil(), id="perfil-ativo")
            yield Static(
                "Escolha uma categoria na barra acima.\n"
                "F1 Ajuda   L Log de chamadas   F10 Sair",
                id="main-menu-hint",
            )
        yield Footer()

    def _texto_perfil(self) -> str:
        perfil = getattr(self.app, "perfil_atual", None)
        if perfil:
            return f"● Perfil ativo: {perfil}"
        return "Nenhum perfil configurado — abra 'Perfil' para configurar."

    def on_screen_resume(self) -> None:
        self.query_one("#perfil-ativo", Static).update(self._texto_perfil())

    def on_menu_bar_selected(self, event: MenuBar.Selected) -> None:
        handler = {
            "importar": self._abrir_importar,
            "modelos": self._abrir_modelos,
            "assinatura": self._abrir_assinatura,
            "documentos": self._abrir_documentos,
            "fluxo": self._abrir_fluxo,
            "perfil": self._abrir_perfil,
            "sair": self._sair,
        }.get(event.key)
        if handler:
            handler()

    def _abrir_importar(self) -> None:
        from docnuvem_tester.screens.envio_inteligente import EnvioInteligenteScreen
        from docnuvem_tester.screens.importar import ImportarScreen

        self.app.push_screen(
            CategoryScreen(
                "Importar",
                [
                    CategoryItem("Importar arquivo (POST /importar)", ImportarScreen),
                    CategoryItem(
                        "Envio inteligente (POST /enviarParaEnvioInteligente)",
                        EnvioInteligenteScreen,
                    ),
                ],
            )
        )

    def _abrir_modelos(self) -> None:
        from docnuvem_tester.screens.modelos import ModelosScreen

        self.app.push_screen(ModelosScreen())

    def _abrir_assinatura(self) -> None:
        from docnuvem_tester.screens.assinatura import AssinaturaScreen
        from docnuvem_tester.screens.cancelar_assinatura import CancelarAssinaturaScreen
        from docnuvem_tester.screens.status import StatusScreen

        self.app.push_screen(
            CategoryScreen(
                "Assinatura",
                [
                    CategoryItem("Solicitar assinatura (POST /api/assinatura)", AssinaturaScreen),
                    CategoryItem(
                        "Cancelar assinatura (DELETE /api/assinatura/{id})",
                        CancelarAssinaturaScreen,
                    ),
                    CategoryItem(
                        "Consultar status (GET /api/documento/{id}/status)", StatusScreen
                    ),
                ],
            )
        )

    def _abrir_documentos(self) -> None:
        from docnuvem_tester.screens.documentos import DocumentosScreen
        from docnuvem_tester.screens.download import DownloadScreen

        self.app.push_screen(
            CategoryScreen(
                "Documentos",
                [
                    CategoryItem("Listar documentos (GET /api/documentos)", DocumentosScreen),
                    CategoryItem(
                        "Baixar documento (GET /api/documento/{id}/download)", DownloadScreen
                    ),
                ],
            )
        )

    def _abrir_fluxo(self) -> None:
        from docnuvem_tester.screens.fluxo_completo import FluxoCompletoScreen

        self.app.push_screen(FluxoCompletoScreen())

    def _abrir_perfil(self) -> None:
        from docnuvem_tester.screens.perfil import PerfilScreen

        self.app.push_screen(PerfilScreen())

    def _sair(self) -> None:
        async def confirmar() -> None:
            ok = await self.app.push_screen_wait(
                ConfirmScreen("Sair", "Tem certeza que deseja sair do Docnuvem API Tester?")
            )
            if ok:
                self.app.exit()

        self.run_worker(confirmar())
