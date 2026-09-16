"""Fluxo Completo: importa um arquivo e, se der certo, já abre a assinatura."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.widgets import Input, Static

from docnuvem_tester.client import DocumentoStatusResponse, OutputData
from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.models import tem_extensao
from docnuvem_tester.screens.assinatura import AssinaturaScreen
from docnuvem_tester.screens.base import FormScreen
from docnuvem_tester.screens.formfields import AutoFillNomeArquivoMixin, campo
from docnuvem_tester.widgets.result_panel import ResultScreen


class FluxoCompletoScreen(AutoFillNomeArquivoMixin, FormScreen):
    """Passo 1 (importar). Ao concluir, empilha o passo 2 (assinatura) já com
    o documentoId preenchido; o resultado final destaca o link de cada
    signatário."""

    def __init__(self) -> None:
        self._init_autofill()
        super().__init__()

    def titulo(self) -> str:
        return "Fluxo Completo — Passo 1/2: Importar (POST /importar)"

    def compose_form(self):
        yield Static(
            "Passo 1 de 2: importa o arquivo. Se der certo, o formulário de "
            "assinatura abre em seguida com o Documento ID já preenchido.",
            classes="form-hint",
        )
        yield campo("Caminho do arquivo*", "in-caminho", "C:\\caminho\\Contrato.pdf")
        yield campo("Nome do arquivo*", "in-nome-arquivo", "Contrato.pdf")
        yield campo("Nome da pasta*", "in-nome-pasta", "Contratos")
        yield campo("Nome da pasta pai", "in-nome-pasta-pai", "Clientes/2026")

    def on_input_changed(self, event: Input.Changed) -> None:
        self._tratar_input_changed_autofill(event, "in-caminho", "in-nome-arquivo")

    def validar(self) -> list[str]:
        avisos = []
        caminho = self.query_one("#in-caminho", Input).value.strip()
        nome_arquivo = self.query_one("#in-nome-arquivo", Input).value.strip()
        nome_pasta = self.query_one("#in-nome-pasta", Input).value.strip()
        if not caminho:
            avisos.append("Informe o caminho do arquivo.")
        elif not Path(caminho).is_file():
            avisos.append(f"Arquivo não encontrado: {caminho}")
        if not nome_arquivo:
            avisos.append("Informe o nome do arquivo.")
        elif not tem_extensao(nome_arquivo):
            avisos.append(f"nomeArquivo precisa ter extensão (ex.: Contrato.pdf): {nome_arquivo!r}")
        if not nome_pasta:
            avisos.append("Informe o nome da pasta de destino.")
        return avisos

    async def chamar_api(self, perfil: PerfilConfig) -> OutputData:
        caminho = Path(self.query_one("#in-caminho", Input).value.strip())
        nome_arquivo = self.query_one("#in-nome-arquivo", Input).value.strip()
        nome_pasta = self.query_one("#in-nome-pasta", Input).value.strip()
        nome_pasta_pai = self.query_one("#in-nome-pasta-pai", Input).value.strip() or None
        client = self.app.client  # type: ignore[attr-defined]
        return await client.importar(
            perfil,
            nome_arquivo=nome_arquivo,
            nome_pasta=nome_pasta,
            nome_pasta_pai=nome_pasta_pai,
            caminho_arquivo=caminho,
        )

    async def ao_sucesso(self, resultado: Any) -> None:
        documento_id = resultado.documentoId

        async def ao_assinar(status: DocumentoStatusResponse) -> None:
            copiaveis = [(f"link de {s.nome}", s.linkAssinatura) for s in status.signatarios if s.linkAssinatura]
            copiaveis.insert(0, ("documentoId", str(documento_id)))
            self.app.push_screen(
                ResultScreen(
                    "Fluxo Completo concluído — documento importado e assinatura solicitada",
                    status,
                    copiaveis=copiaveis,
                )
            )

        self.app.push_screen(
            AssinaturaScreen(
                documento_id_inicial=documento_id,
                on_sucesso=ao_assinar,
                titulo_extra="Fluxo Completo — Passo 2/2: Assinatura (POST /api/assinatura)",
            )
        )
