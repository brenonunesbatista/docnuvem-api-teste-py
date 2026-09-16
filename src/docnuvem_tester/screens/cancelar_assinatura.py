"""Tela do endpoint DELETE /api/assinatura/{assinaturaId}."""

from __future__ import annotations

from typing import Any

from textual.widgets import Input, Static

from docnuvem_tester.client import DocumentoStatusResponse
from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.screens.base import FormScreen
from docnuvem_tester.screens.formfields import campo
from docnuvem_tester.widgets.confirm_dialog import ConfirmScreen
from docnuvem_tester.widgets.result_panel import ResultScreen


class CancelarAssinaturaScreen(FormScreen):
    def titulo(self) -> str:
        return "Cancelar assinatura — DELETE /api/assinatura/{assinaturaId}"

    def compose_form(self):
        yield Static(
            "Cancela a solicitação inteira: todos os signatários viram 'cancelado' "
            "(inclusive quem já assinou) e os links param de funcionar. Ação destrutiva.",
            classes="form-hint",
        )
        yield campo("Assinatura ID*", "in-assinatura-id", "456")
        yield campo("Motivo", "in-motivo", "Solicitado pelo cliente")

    def validar(self) -> list[str]:
        avisos = []
        if not self.query_one("#in-assinatura-id", Input).value.strip():
            avisos.append("Informe o Assinatura ID.")
        return avisos

    async def confirmar(self) -> bool:
        assinatura_id = self.query_one("#in-assinatura-id", Input).value.strip()
        return await self.app.push_screen_wait(
            ConfirmScreen(
                "Confirmar cancelamento",
                f"Tem certeza que deseja cancelar a assinatura {assinatura_id}? "
                "Essa ação não pode ser desfeita pela API.",
            )
        )

    async def chamar_api(self, perfil: PerfilConfig) -> DocumentoStatusResponse:
        assinatura_id = self.query_one("#in-assinatura-id", Input).value.strip()
        motivo = self.query_one("#in-motivo", Input).value.strip() or None
        client = self.app.client  # type: ignore[attr-defined]
        return await client.cancelar_assinatura(perfil, assinatura_id, motivo)

    async def ao_sucesso(self, resultado: Any) -> None:
        self.app.push_screen(ResultScreen("Assinatura cancelada", resultado))
        self.dismiss(None)
