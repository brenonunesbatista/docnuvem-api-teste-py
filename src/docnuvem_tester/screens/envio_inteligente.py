"""Tela do endpoint POST /enviarParaEnvioInteligente."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.widgets import Button, Input, Static

from docnuvem_tester.client import OutputData
from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.models import tem_extensao
from docnuvem_tester.screens.base import FormScreen
from docnuvem_tester.screens.formfields import AutoFillNomeArquivoMixin, campo, campo_arquivo
from docnuvem_tester.validators import nome_com_extensao
from docnuvem_tester.widgets.result_panel import ResultScreen


class EnvioInteligenteScreen(AutoFillNomeArquivoMixin, FormScreen):
    def __init__(self) -> None:
        self._init_autofill()
        super().__init__()

    def titulo(self) -> str:
        return "Envio inteligente — POST /enviarParaEnvioInteligente"

    def compose_form(self):
        yield Static(
            "Envia um arquivo para a triagem automática do envio inteligente.",
            classes="form-hint",
        )
        yield campo_arquivo("Caminho do arquivo*", "in-caminho", "C:\\caminho\\Contrato.pdf")
        yield campo(
            "Nome do arquivo*", "in-nome-arquivo", "Contrato.pdf",
            validators=[nome_com_extensao()],
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        self._tratar_input_changed_autofill(event, "in-caminho", "in-nome-arquivo")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self._e_botao_seletor_arquivo(event.button.id, "in-caminho"):
            event.stop()
            self.run_worker(self._abrir_seletor_arquivo("in-caminho"))

    def validar(self) -> list[str]:
        avisos = []
        caminho = self.query_one("#in-caminho", Input).value.strip()
        nome_arquivo = self.query_one("#in-nome-arquivo", Input).value.strip()
        if not caminho:
            avisos.append("Informe o caminho do arquivo.")
        elif not Path(caminho).is_file():
            avisos.append(f"Arquivo não encontrado: {caminho}")
        if not nome_arquivo:
            avisos.append("Informe o nome do arquivo.")
        elif not tem_extensao(nome_arquivo):
            avisos.append(f"nomeArquivo precisa ter extensão (ex.: Contrato.pdf): {nome_arquivo!r}")
        return avisos

    async def chamar_api(self, perfil: PerfilConfig) -> OutputData:
        caminho = Path(self.query_one("#in-caminho", Input).value.strip())
        nome_arquivo = self.query_one("#in-nome-arquivo", Input).value.strip()
        client = self.app.client  # type: ignore[attr-defined]
        return await client.enviar_para_envio_inteligente(
            perfil, nome_arquivo=nome_arquivo, caminho_arquivo=caminho
        )

    async def ao_sucesso(self, resultado: Any) -> None:
        self.app.push_screen(
            ResultScreen(
                "Enviado para o envio inteligente",
                resultado,
                copiaveis=[("documentoId", str(resultado.documentoId))],
                chamada=self.app.ultima_chamada,  # type: ignore[attr-defined]
            )
        )
