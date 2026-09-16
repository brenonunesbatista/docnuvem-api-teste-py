"""Tela do endpoint GET /api/documento/{documentoId}/download."""

from __future__ import annotations

from typing import Any

from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from docnuvem_tester.client import DocumentoDownloadResponse
from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.formatting import valor_ou_traco
from docnuvem_tester.screens.base import FormScreen
from docnuvem_tester.screens.formfields import campo


class DownloadResultScreen(ModalScreen[None]):
    """Resultado do link de download: destaca a expiração e nunca trunca a URL."""

    BINDINGS = [("escape", "fechar", "Fechar"), ("c", "copiar_url", "Copiar URL")]

    def __init__(self, dados: DocumentoDownloadResponse) -> None:
        self._dados = dados
        super().__init__()

    def compose(self):
        d = self._dados
        resumo = (
            f"Arquivo: {valor_ou_traco(d.nomeArquivo)}\n"
            f"Extensão: {valor_ou_traco(d.extensao)}    Content-Type: {valor_ou_traco(d.contentType)}\n"
            f"Tamanho: {valor_ou_traco(d.tamanho)} bytes\n"
            f"Status da assinatura: {valor_ou_traco(d.statusAssinatura)}\n"
        )
        with Container(classes="dos-window"):
            yield Static(f"Download do documento {valor_ou_traco(d.documentoId)}", classes="dos-window-title")
            yield Static(resumo)
            yield Static(
                f"⚠ Expira em: {valor_ou_traco(d.expiraEm)} — URL temporária (S3), "
                "não pode ser revogada. NÃO guarde nem reencaminhe este link.",
                classes="alert-message",
            )
            yield Static(valor_ou_traco(d.urlDownload))
            with Horizontal(classes="result-copy-bar"):
                yield Button("Copiar urlDownload (C)", id="copy-url", classes="primary")
            if d.relatorioAssinatura:
                yield Static("Relatório de assinatura:")
                yield Static(d.relatorioAssinatura)
            yield Static("C copia a URL   |   Esc fecha", classes="result-hint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy-url":
            self.action_copiar_url()

    def action_copiar_url(self) -> None:
        if not self._dados.urlDownload:
            self.app.notify("Nenhuma URL disponível.", severity="warning")
            return
        self.app.copy_to_clipboard(self._dados.urlDownload)
        self.app.notify("urlDownload copiada!", timeout=2)

    def action_fechar(self) -> None:
        self.dismiss(None)


class DownloadScreen(FormScreen):
    def titulo(self) -> str:
        return "Baixar documento — GET /api/documento/{documentoId}/download"

    def compose_form(self):
        yield Static(
            "Gera um link de download temporário (S3, expira em 15 minutos).",
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

    async def chamar_api(self, perfil: PerfilConfig) -> DocumentoDownloadResponse:
        documento_id = int(self.query_one("#in-documento-id", Input).value.strip())
        client = self.app.client  # type: ignore[attr-defined]
        return await client.get_download(perfil, documento_id)

    async def ao_sucesso(self, resultado: Any) -> None:
        self.app.push_screen(DownloadResultScreen(resultado))
        self.dismiss(None)
