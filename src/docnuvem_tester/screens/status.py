"""Tela do endpoint GET /api/documento/{documentoId}/status."""

from __future__ import annotations

from typing import Any

from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static

from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.formatting import valor_ou_traco
from docnuvem_tester.models import DocumentoStatusResponse
from docnuvem_tester.screens.base import FormScreen
from docnuvem_tester.screens.formfields import campo


class StatusResultScreen(ModalScreen[None]):
    """Resultado especializado do status: resumo + tabela de signatários copiável."""

    BINDINGS = [("escape", "fechar", "Fechar"), ("c", "copiar_link", "Copiar link")]

    def __init__(self, status: DocumentoStatusResponse) -> None:
        self._status = status
        super().__init__()

    def compose(self):
        s = self._status
        resumo = (
            f"Status: {valor_ou_traco(s.status)} ({valor_ou_traco(s.statusDescricao)})\n"
            f"Referência externa: {valor_ou_traco(s.referenciaExterna)}\n"
            f"Solicitado em: {valor_ou_traco(s.dataSolicitacao)}\n"
            f"Validade: {valor_ou_traco(s.dataValidade)}\n"
            f"Última alteração: {valor_ou_traco(s.dataUltimaAlteracao)}\n"
            f"Considera ordem: {valor_ou_traco(s.consideraOrdem)}\n"
            f"Lembretes: {valor_ou_traco(s.lembretes)}\n"
            f"Último lembrete em: {valor_ou_traco(s.dataUltimoLembrete)}"
        )
        with Container(classes="dos-window"):
            yield Static(
                f"Status do documento {valor_ou_traco(s.documentoId)} — {valor_ou_traco(s.nomeArquivo)}",
                classes="dos-window-title",
            )
            yield Static(resumo)
            with Horizontal(classes="result-copy-bar"):
                yield Button("Copiar documentoId", id="copy-doc", classes="secondary")
                yield Button("Copiar assinaturaId", id="copy-assinatura", classes="secondary")
            yield Static("Signatários — selecione uma linha e pressione C para copiar o link:")
            yield DataTable(id="sig-table", cursor_type="row")
            yield Static("C copia o link do signatário   |   Esc fecha", classes="result-hint")

    def on_mount(self) -> None:
        table = self.query_one("#sig-table", DataTable)
        table.add_columns("Nome", "Email", "CPF", "Ordem", "Status", "Assinou em", "Visualizou")
        for sig in self._status.signatarios:
            table.add_row(
                valor_ou_traco(sig.nome),
                valor_ou_traco(sig.email),
                valor_ou_traco(sig.cpf),
                valor_ou_traco(sig.ordem),
                valor_ou_traco(sig.statusDescricao or sig.status),
                valor_ou_traco(sig.dataAssinatura),
                "sim" if sig.visualizou else "não",
            )
        table.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy-doc":
            self.app.copy_to_clipboard(str(self._status.documentoId))
            self.app.notify("documentoId copiado!", timeout=2)
        elif event.button.id == "copy-assinatura":
            self.app.copy_to_clipboard(str(self._status.assinaturaId))
            self.app.notify("assinaturaId copiado!", timeout=2)

    def action_copiar_link(self) -> None:
        table = self.query_one("#sig-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self._status.signatarios):
            return
        sig = self._status.signatarios[table.cursor_row]
        if not sig.linkAssinatura:
            self.app.notify("Este signatário não tem link disponível.", severity="warning")
            return
        self.app.copy_to_clipboard(sig.linkAssinatura)
        self.app.notify(f"Link de {sig.nome} copiado!", timeout=2)

    def action_fechar(self) -> None:
        self.dismiss(None)


class StatusScreen(FormScreen):
    def titulo(self) -> str:
        return "Consultar status — GET /api/documento/{documentoId}/status"

    def compose_form(self):
        yield Static(
            "linkAssinatura só é preenchido para signatários pendentes.",
            classes="form-hint",
        )
        yield campo("Documento ID*", "in-documento-id", "123")

    def validar(self) -> list[str]:
        avisos = []
        valor = self.query_one("#in-documento-id", Input).value.strip()
        if not valor:
            avisos.append("Informe o Documento ID.")
        elif not valor.isdigit():
            avisos.append("Documento ID deve ser um número.")
        return avisos

    async def chamar_api(self, perfil: PerfilConfig) -> DocumentoStatusResponse:
        documento_id = int(self.query_one("#in-documento-id", Input).value.strip())
        client = self.app.client  # type: ignore[attr-defined]
        return await client.get_status(perfil, documento_id)

    async def ao_sucesso(self, resultado: Any) -> None:
        self.app.push_screen(StatusResultScreen(resultado))
        self.dismiss(None)
