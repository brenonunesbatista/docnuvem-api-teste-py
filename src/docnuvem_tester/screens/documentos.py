"""Tela do endpoint GET /api/documentos (lista paginada com filtros)."""

from __future__ import annotations

from pydantic import ValidationError
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Input, LoadingIndicator, Select, Static

from docnuvem_tester.client import DocNuvemAPIError
from docnuvem_tester.formatting import valor_ou_traco
from docnuvem_tester.models import STATUS_FILTRO_VALIDOS, DocumentoListaItemDTO, FiltroDocumentos
from docnuvem_tester.screens.formfields import campo, erros_pydantic
from docnuvem_tester.widgets.confirm_dialog import AlertScreen
from docnuvem_tester.widgets.result_panel import erro_para_tela


class DocumentosScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "voltar", "Voltar"),
        ("f5", "buscar", "Buscar"),
        ("n", "proxima_pagina", "Próxima"),
        ("p", "pagina_anterior", "Anterior"),
    ]

    def __init__(self) -> None:
        self._pagina = 0
        self._tem_mais = False
        self._documentos: list[DocumentoListaItemDTO] = []
        super().__init__()

    def compose(self):
        with Container(classes="dos-window"):
            yield Static("Documentos — GET /api/documentos", classes="dos-window-title")
            with VerticalScroll(id="doc-filtros"):
                yield campo("Diretório ID", "in-diretorio-id", "789")
                yield Horizontal(
                    Static("Incluir subpastas", classes="form-label"),
                    Checkbox(id="in-subpastas"),
                    classes="form-row",
                )
                yield Horizontal(
                    Static("Status", classes="form-label"),
                    Select(
                        [(v, v) for v in sorted(STATUS_FILTRO_VALIDOS)],
                        allow_blank=True,
                        id="in-status",
                    ),
                    classes="form-row",
                )
                yield campo("Data início (yyyy-MM-dd)", "in-data-inicio", "2026-01-01")
                yield campo("Data fim (yyyy-MM-dd)", "in-data-fim", "2026-12-31")
                yield campo("Tamanho da página (máx. 200)", "in-tamanho", "50", value="50")
            yield LoadingIndicator(id="doc-loading")
            yield DataTable(id="doc-table", cursor_type="row")
            yield Static("", id="doc-paginacao")
            with Horizontal(classes="form-actions"):
                yield Button("Buscar (F5)", id="doc-buscar", classes="primary")
                yield Button("Anterior (P)", id="doc-anterior", classes="secondary")
                yield Button("Próxima (N)", id="doc-proxima", classes="secondary")
                yield Button("Voltar (Esc)", id="doc-voltar", classes="secondary")

    def on_mount(self) -> None:
        table = self.query_one("#doc-table", DataTable)
        table.add_columns(
            "Documento ID", "Nome", "Status", "Diretório ID", "Assinatura ID",
            "Referência", "Solicitado em", "Última alteração",
        )
        self.query_one("#doc-loading", LoadingIndicator).display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "doc-buscar":
            self.action_buscar()
        elif event.button.id == "doc-anterior":
            self.action_pagina_anterior()
        elif event.button.id == "doc-proxima":
            self.action_proxima_pagina()
        elif event.button.id == "doc-voltar":
            self.action_voltar()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.cursor_row >= len(self._documentos):
            return
        documento = self._documentos[event.cursor_row]
        if documento.documentoId is None:
            return
        from docnuvem_tester.screens.status import StatusResultScreen

        self.run_worker(self._consultar_status(documento.documentoId, StatusResultScreen))

    async def _consultar_status(self, documento_id: int, tela_status) -> None:
        perfil = getattr(self.app, "perfil_ativo", None)
        if perfil is None:
            return
        try:
            status = await self.app.client.get_status(perfil, documento_id)  # type: ignore[attr-defined]
        except DocNuvemAPIError as exc:
            self.app.push_screen(erro_para_tela(exc))
            return
        self.app.push_screen(tela_status(status))

    def _montar_filtro(self) -> FiltroDocumentos | list[str]:
        diretorio_raw = self.query_one("#in-diretorio-id", Input).value.strip()
        tamanho_raw = self.query_one("#in-tamanho", Input).value.strip() or "50"
        status = self.query_one("#in-status", Select).value
        # Select "sem seleção" varia entre versões do Textual (Select.BLANK vs
        # Select.NULL/NoSelection) — checar por isinstance(str) é robusto às duas.
        status_valor = status if isinstance(status, str) else None
        dados = {
            "diretorioId": int(diretorio_raw) if diretorio_raw else None,
            "incluirSubpastas": self.query_one("#in-subpastas", Checkbox).value,
            "status": status_valor,
            "dataInicio": self.query_one("#in-data-inicio", Input).value.strip() or None,
            "dataFim": self.query_one("#in-data-fim", Input).value.strip() or None,
            "pagina": self._pagina,
            "tamanho": int(tamanho_raw) if tamanho_raw.isdigit() else 50,
        }
        if diretorio_raw and not diretorio_raw.isdigit():
            return ["Diretório ID deve ser um número."]
        try:
            return FiltroDocumentos.model_validate(dados)
        except ValidationError as exc:
            return erros_pydantic(exc)

    def action_buscar(self) -> None:
        self._pagina = 0
        self.run_worker(self._carregar(), exclusive=True)

    def action_proxima_pagina(self) -> None:
        if self._tem_mais:
            self._pagina += 1
            self.run_worker(self._carregar(), exclusive=True)

    def action_pagina_anterior(self) -> None:
        if self._pagina > 0:
            self._pagina -= 1
            self.run_worker(self._carregar(), exclusive=True)

    async def _carregar(self) -> None:
        perfil = getattr(self.app, "perfil_ativo", None)
        if perfil is None:
            await self.app.push_screen_wait(
                AlertScreen(
                    "Sem perfil ativo",
                    ["Configure um perfil em Perfil antes de chamar a API."],
                )
            )
            return

        filtro = self._montar_filtro()
        if isinstance(filtro, list):
            await self.app.push_screen_wait(AlertScreen("Verifique os filtros", filtro))
            return

        loading = self.query_one("#doc-loading", LoadingIndicator)
        table = self.query_one("#doc-table", DataTable)
        loading.display = True
        try:
            resposta = await self.app.client.listar_documentos(perfil, filtro)  # type: ignore[attr-defined]
        except DocNuvemAPIError as exc:
            loading.display = False
            self.app.push_screen(erro_para_tela(exc))
            return
        loading.display = False

        self._documentos = resposta.documentos
        self._tem_mais = bool(resposta.temMais)
        table.clear()
        for doc in self._documentos:
            table.add_row(
                valor_ou_traco(doc.documentoId),
                valor_ou_traco(doc.nomeArquivo),
                valor_ou_traco(doc.statusDescricao or doc.status),
                valor_ou_traco(doc.diretorioId),
                valor_ou_traco(doc.assinaturaId),
                valor_ou_traco(doc.referenciaExterna),
                valor_ou_traco(doc.dataSolicitacao),
                valor_ou_traco(doc.dataUltimaAlteracao),
            )
        total = valor_ou_traco(resposta.total)
        self.query_one("#doc-paginacao", Static).update(
            f"Página {self._pagina}   |   {len(self._documentos)} nesta página   |   "
            f"total: {total}   |   {'há mais páginas' if self._tem_mais else 'última página'}"
        )

    def action_voltar(self) -> None:
        self.dismiss(None)
