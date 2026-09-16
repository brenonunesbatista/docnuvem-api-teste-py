"""Tela do endpoint POST /api/documento/from-template."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from textual.widgets import Input, Static

from docnuvem_tester.client import DocumentoFromTemplateResponse
from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.models import DocumentoFromTemplateRequest
from docnuvem_tester.screens.base import FormScreen
from docnuvem_tester.screens.formfields import campo, erros_pydantic
from docnuvem_tester.widgets.dynamic_list import KeyValueEditor
from docnuvem_tester.widgets.result_panel import ResultScreen


class FromTemplateScreen(FormScreen):
    def __init__(self) -> None:
        self._req: DocumentoFromTemplateRequest | None = None
        super().__init__()

    def titulo(self) -> str:
        return "Criar de modelo — POST /api/documento/from-template"

    def compose_form(self):
        yield Static(
            "A pasta de destino (nomePasta) precisa já existir — este endpoint não cria pastas.",
            classes="form-hint",
        )
        yield campo("Modelo (código)*", "in-modelo", "contrato-padrao")
        yield campo("Nome do arquivo", "in-nome-arquivo", "Contrato Cliente X.pdf")
        yield campo("Nome da pasta*", "in-nome-pasta", "Contratos")
        yield campo("Nome da pasta pai", "in-nome-pasta-pai", "Clientes/2026")
        yield campo("Referência externa (até 64)", "in-referencia", "pedido-123")
        yield campo("Login do solicitante", "in-login", "usuario@empresa.com")
        yield Static("Variáveis do modelo:", classes="form-hint")
        yield KeyValueEditor()

    def validar(self) -> list[str]:
        modelo = self.query_one("#in-modelo", Input).value.strip()
        nome_pasta = self.query_one("#in-nome-pasta", Input).value.strip()
        avisos = []
        if not modelo:
            avisos.append("Informe o código do modelo.")
        if not nome_pasta:
            avisos.append("Informe o nome da pasta de destino (ela precisa já existir).")
        if avisos:
            return avisos

        dados = {
            "modelo": modelo,
            "nomeArquivo": self.query_one("#in-nome-arquivo", Input).value.strip() or None,
            "nomePasta": nome_pasta,
            "nomePastaPai": self.query_one("#in-nome-pasta-pai", Input).value.strip() or None,
            "referenciaExterna": self.query_one("#in-referencia", Input).value.strip() or None,
            "loginSolicitante": self.query_one("#in-login", Input).value.strip() or None,
            "variaveis": self.query_one(KeyValueEditor).valores(),
        }
        try:
            self._req = DocumentoFromTemplateRequest.model_validate(dados)
        except ValidationError as exc:
            return erros_pydantic(exc)
        return []

    async def chamar_api(self, perfil: PerfilConfig) -> DocumentoFromTemplateResponse:
        assert self._req is not None
        client = self.app.client  # type: ignore[attr-defined]
        return await client.documento_from_template(perfil, self._req)

    async def ao_sucesso(self, resultado: Any) -> None:
        copiaveis = [("documentoId", str(resultado.documentoId))]
        if resultado.referenciaExterna:
            copiaveis.append(("referenciaExterna", resultado.referenciaExterna))
        self.app.push_screen(ResultScreen("Documento criado a partir do modelo", resultado, copiaveis=copiaveis))
