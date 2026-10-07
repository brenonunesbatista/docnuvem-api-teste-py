"""Criação de documento a partir de um modelo — POST /api/documento/from-template.

Entrada única: clicar num modelo na tela de Modelos (GET /api/modelos). O
formulário é montado dinamicamente a partir do array `variaveis` daquele
modelo específico.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Select, Static, Switch, TextArea

from docnuvem_tester.client import DocnuvemAPIError, DocumentoFromTemplateResponse
from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.formatting import valor_ou_traco
from docnuvem_tester.models import (
    DocumentoFromTemplateRequest,
    ModeloDTO,
    VariavelModeloDTO,
    validar_data_br,
    validar_numero_br,
)
from docnuvem_tester.screens.base import FormScreen
from docnuvem_tester.screens.formfields import campo, erros_pydantic
from docnuvem_tester.validators import data_br, max_caracteres, numero_br
from docnuvem_tester.widgets.result_panel import erro_para_tela

TIPOS_TEXTO_LONGO = {"TEXTO_LONGO", "PARAGRAFO"}
TIPOS_DATA = {"DATA", "DATA_VENCIMENTO"}


@dataclass
class CampoVariavel:
    chave: str
    rotulo: str
    tipo: str
    obrigatorio: bool
    incluir_no_payload: bool
    widget: Widget | None
    linha: Widget


class CriarDocumentoModeloScreen(FormScreen):
    """Formulário dinâmico: um campo por `VariavelModeloDTO` do modelo escolhido."""

    def __init__(self, modelo: ModeloDTO) -> None:
        self._modelo = modelo
        self._campos: list[CampoVariavel] = []
        self._contador = 0
        self._req: DocumentoFromTemplateRequest | None = None
        super().__init__()

    def _nome_modelo(self) -> str:
        return self._modelo.nome or self._modelo.codigo or str(self._modelo.id or "")

    def titulo(self) -> str:
        return f'Criar de "{self._nome_modelo()}" — POST /api/documento/from-template'

    def compose_form(self):
        yield Static(
            "Campos do documento (a pasta de destino precisa já existir — "
            "este endpoint não cria pastas):",
            classes="form-hint",
        )
        yield campo("Nome da pasta*", "in-nome-pasta", "Contratos")
        yield campo("Nome da pasta pai", "in-nome-pasta-pai", "Clientes/2026")
        yield campo("Nome do arquivo", "in-nome-arquivo", "Contrato Cliente X.pdf")
        yield campo(
            "Referência externa (até 64)", "in-referencia", "pedido-123",
            validators=[max_caracteres("Referência externa", 64)],
        )
        yield campo("Login do solicitante", "in-login", "usuario@empresa.com")

        variaveis = self._modelo.variaveis
        yield Static(f'Variáveis do modelo "{self._nome_modelo()}":', classes="form-hint")
        if not variaveis:
            yield Static("Este modelo não declara variáveis.", classes="form-hint")
        for var in variaveis:
            yield self._montar_linha_variavel(var)

    def _montar_linha_variavel(self, var: VariavelModeloDTO) -> Widget:
        rotulo = var.rotulo or var.chave
        tipo = (var.tipo or "TEXTO").upper()
        linha_id = f"var-row-{self._contador}"
        self._contador += 1

        somente_leitura = not var.aceitaPorApi or bool(var.preenchidoPeloDestinatario)
        if somente_leitura:
            if var.preenchidoPeloDestinatario:
                nota = (
                    "Preenchido depois pela pessoa que recebe a solicitação de "
                    "assinatura — não é enviado por aqui."
                )
            else:
                nota = (
                    "Este tipo de campo ainda não é preenchido pela API — "
                    "preencha depois pela tela do Docnuvem."
                )
            linha = Vertical(
                Static(rotulo, classes="var-label"),
                Input(disabled=True, placeholder=nota),
                Static(nota, classes="form-hint"),
                id=linha_id,
                classes="var-row",
            )
            self._campos.append(CampoVariavel(var.chave, rotulo, tipo, False, False, None, linha))
            return linha

        marcador = " *" if var.obrigatorio else ""
        widget: Widget
        if tipo in TIPOS_TEXTO_LONGO:
            widget = TextArea(id=f"{linha_id}-widget")
        elif tipo in TIPOS_DATA:
            widget = Input(
                placeholder="dd/MM/aaaa",
                id=f"{linha_id}-widget",
                validators=[data_br(rotulo)],
            )
        elif tipo == "NUMERICO":
            widget = Input(
                placeholder="1.234,56",
                id=f"{linha_id}-widget",
                validators=[numero_br(rotulo)],
            )
        elif tipo == "CHECKBOX":
            widget = Switch(id=f"{linha_id}-widget")
        elif tipo == "SELECT":
            opcoes = [(op, op) for op in (var.opcoes or [])]
            widget = Select(opcoes, allow_blank=True, id=f"{linha_id}-widget")
        else:
            widget = Input(id=f"{linha_id}-widget")

        linha = Vertical(
            Static(f"{rotulo}{marcador}", classes="var-label"),
            widget,
            id=linha_id,
            classes="var-row",
        )
        self._campos.append(
            CampoVariavel(var.chave, rotulo, tipo, bool(var.obrigatorio), True, widget, linha)
        )
        return linha

    @staticmethod
    def _ler_valor(campo_var: CampoVariavel) -> str:
        widget = campo_var.widget
        if campo_var.tipo == "CHECKBOX" and isinstance(widget, Switch):
            return "sim" if widget.value else "não"
        if isinstance(widget, TextArea):
            return widget.text.strip()
        if isinstance(widget, Select):
            valor = widget.value
            return valor if isinstance(valor, str) else ""
        if isinstance(widget, Input):
            return widget.value.strip()
        return ""

    def validar(self) -> list[str]:
        for campo_var in self._campos:
            campo_var.linha.remove_class("-campo-erro")

        avisos: list[str] = []
        nome_pasta = self.query_one("#in-nome-pasta", Input).value.strip()
        if not nome_pasta:
            avisos.append("Informe o nome da pasta de destino (ela precisa já existir).")

        variaveis: dict[str, str] = {}
        for campo_var in self._campos:
            if not campo_var.incluir_no_payload:
                continue
            valor = self._ler_valor(campo_var)
            if campo_var.obrigatorio and not valor:
                avisos.append(f"Campo obrigatório não preenchido: {campo_var.rotulo}")
                continue
            if valor and campo_var.tipo in TIPOS_DATA and not validar_data_br(valor):
                avisos.append(f"{campo_var.rotulo}: data deve estar no formato dd/MM/aaaa.")
                continue
            if valor and campo_var.tipo == "NUMERICO" and not validar_numero_br(valor):
                avisos.append(f"{campo_var.rotulo}: número deve estar no formato 1.234,56.")
                continue
            if valor:
                variaveis[campo_var.chave] = valor

        if avisos:
            return avisos

        dados = {
            "modelo": self._modelo.codigo or str(self._modelo.id or ""),
            "nomeArquivo": self.query_one("#in-nome-arquivo", Input).value.strip() or None,
            "nomePasta": nome_pasta,
            "nomePastaPai": self.query_one("#in-nome-pasta-pai", Input).value.strip() or None,
            "referenciaExterna": self.query_one("#in-referencia", Input).value.strip() or None,
            "loginSolicitante": self.query_one("#in-login", Input).value.strip() or None,
            "variaveis": variaveis,
        }
        try:
            self._req = DocumentoFromTemplateRequest.model_validate(dados)
        except ValidationError as exc:
            return erros_pydantic(exc)
        return []

    def ao_erro_api(self, exc: DocnuvemAPIError) -> bool:
        faltantes = set((exc.parsed or {}).get("variaveisFaltantes") or [])
        if not faltantes:
            return False
        for campo_var in self._campos:
            if campo_var.chave in faltantes:
                campo_var.linha.add_class("-campo-erro")
        self.app.push_screen(erro_para_tela(exc))
        return True

    async def chamar_api(self, perfil: PerfilConfig) -> DocumentoFromTemplateResponse:
        assert self._req is not None
        client = self.app.client  # type: ignore[attr-defined]
        return await client.documento_from_template(perfil, self._req)

    async def ao_sucesso(self, resultado: Any) -> None:
        self.app.push_screen(CriarDocumentoResultScreen(resultado))


class CriarDocumentoResultScreen(ModalScreen[None]):
    """Resultado da criação por modelo, com atalho opcional para Assinatura."""

    BINDINGS = [("escape", "fechar", "Fechar")]

    def __init__(self, resultado: DocumentoFromTemplateResponse) -> None:
        self._resultado = resultado
        super().__init__()

    def compose(self):
        r = self._resultado
        resumo = (
            f"documentoId: {valor_ou_traco(r.documentoId)}\n"
            f"nomeArquivo: {valor_ou_traco(r.nomeArquivo)}\n"
            f"diretorioId: {valor_ou_traco(r.diretorioId)}\n"
            f"referenciaExterna: {valor_ou_traco(r.referenciaExterna)}"
        )
        with Container(classes="card"):
            yield Static("Documento criado a partir do modelo", classes="card-title")
            yield Static(resumo)
            with Horizontal(classes="result-copy-bar"):
                yield Button("Copiar documentoId", id="copy-doc", classes="secondary")
                yield Button("Ir para Assinatura...", id="ir-assinar", classes="primary")
            yield Static("Esc para fechar", classes="result-hint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy-doc":
            if self._resultado.documentoId is not None:
                self.app.copy_to_clipboard(str(self._resultado.documentoId))
                self.app.notify("documentoId copiado!", timeout=2)
        elif event.button.id == "ir-assinar":
            from docnuvem_tester.screens.assinatura import AssinaturaScreen

            self.dismiss(None)
            self.app.push_screen(
                AssinaturaScreen(documento_id_inicial=self._resultado.documentoId)
            )

    def action_fechar(self) -> None:
        self.dismiss(None)
