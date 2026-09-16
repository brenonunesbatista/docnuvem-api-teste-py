"""Tela do endpoint GET /api/modelos."""

from __future__ import annotations

from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DataTable, LoadingIndicator, Static

from docnuvem_tester.client import DocNuvemAPIError
from docnuvem_tester.formatting import valor_ou_traco
from docnuvem_tester.models import ModeloDTO
from docnuvem_tester.widgets.confirm_dialog import AlertScreen
from docnuvem_tester.widgets.result_panel import ResultScreen, erro_para_tela


class ModelosScreen(ModalScreen[None]):
    BINDINGS = [("escape", "voltar", "Voltar"), ("f5", "atualizar", "Atualizar")]

    def __init__(self) -> None:
        self._modelos: list[ModeloDTO] = []
        super().__init__()

    def compose(self):
        with Container(classes="dos-window"):
            yield Static("Modelos — GET /api/modelos", classes="dos-window-title")
            yield LoadingIndicator(id="modelos-loading")
            yield DataTable(id="modelos-table", cursor_type="row")
            yield Static(
                "Enter para ver variáveis   |   F5 Atualizar   |   Esc Voltar",
                classes="result-hint",
            )

    def on_mount(self) -> None:
        table = self.query_one("#modelos-table", DataTable)
        table.add_columns("ID", "Código", "Nome", "Gerável p/ API", "Descrição")
        self.query_one("#modelos-loading", LoadingIndicator).display = False
        self.action_atualizar()

    def action_atualizar(self) -> None:
        self.run_worker(self._carregar(), exclusive=True)

    async def _carregar(self) -> None:
        perfil = getattr(self.app, "perfil_ativo", None)
        loading = self.query_one("#modelos-loading", LoadingIndicator)
        table = self.query_one("#modelos-table", DataTable)
        if perfil is None:
            await self.app.push_screen_wait(
                AlertScreen(
                    "Sem perfil ativo",
                    ["Configure um perfil em Perfil antes de chamar a API."],
                )
            )
            return
        loading.display = True
        table.clear()
        try:
            resposta = await self.app.client.get_modelos(perfil)  # type: ignore[attr-defined]
        except DocNuvemAPIError as exc:
            loading.display = False
            self.app.push_screen(erro_para_tela(exc))
            return
        loading.display = False
        self._modelos = resposta.modelos
        for modelo in self._modelos:
            table.add_row(
                valor_ou_traco(modelo.id),
                valor_ou_traco(modelo.codigo),
                valor_ou_traco(modelo.nome),
                "sim" if modelo.geravelPorApi else "não",
                valor_ou_traco(modelo.descricao),
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        modelo = self._modelos[event.cursor_row]
        titulo = f"Variáveis do modelo {modelo.codigo or modelo.id or ''}".strip()
        self.app.push_screen(ResultScreen(titulo, modelo))

    def action_voltar(self) -> None:
        self.dismiss(None)
