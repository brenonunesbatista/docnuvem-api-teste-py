"""Tela de resultado: resposta (JSON/erro) + requisição enviada, com ações de copiar."""

from __future__ import annotations

from typing import Any

from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TabbedContent, TabPane, TextArea

from docnuvem_tester.client import CallLogEntry, DocNuvemAPIError, RequisicaoPrevista
from docnuvem_tester.formatting import formatar_json, valor_ou_traco


def _corpo(texto: str) -> VerticalScroll:
    area = TextArea(texto, read_only=True)
    area.show_line_numbers = False
    corpo = VerticalScroll(area)
    corpo.add_class("result-body")
    return corpo


def texto_requisicao(chamada: CallLogEntry) -> str:
    """Requisição enviada + status/duração da resposta, para a aba 'Requisição'."""
    req = chamada.requisicao
    partes = [req.texto() if req else f"{chamada.method} {chamada.url}", ""]
    duracao = f"{chamada.duration_ms:.0f} ms" if chamada.duration_ms is not None else "—"
    partes.append(f"Resposta: HTTP {valor_ou_traco(chamada.status_code)}  ·  {duracao}")
    return "\n".join(partes)


class ResultScreen(ModalScreen[None]):
    """Painel de saída somente-leitura com o JSON da resposta (ou erro) da API.

    `copiaveis` é uma lista de (rótulo, valor) que vira botões "Copiar <rótulo>",
    sempre copiando o valor original (nunca uma versão truncada para exibição).
    Com `chamada`, ganha uma aba "Requisição" e o botão "Copiar cURL".
    """

    BINDINGS = [("escape", "fechar", "Fechar")]

    def __init__(
        self,
        titulo: str,
        data: Any,
        *,
        is_error: bool = False,
        copiaveis: list[tuple[str, str]] | None = None,
        rodape: str | None = None,
        chamada: CallLogEntry | None = None,
    ) -> None:
        self._titulo = titulo
        self._texto = data if isinstance(data, str) else formatar_json(data)
        self._is_error = is_error
        self._copiaveis = copiaveis or []
        self._rodape = rodape
        self._chamada = chamada
        super().__init__()

    def compose(self):
        with Container(classes="card"):
            yield Static(self._titulo, classes="card-title")
            if self._chamada is not None:
                with TabbedContent(id="result-tabs"):
                    with TabPane("Resposta", id="tab-resposta"):
                        yield _corpo(self._texto)
                    with TabPane("Requisição", id="tab-requisicao"):
                        yield _corpo(texto_requisicao(self._chamada))
            else:
                yield _corpo(self._texto)
            botoes = list(enumerate(self._copiaveis))
            tem_curl = self._chamada is not None and self._chamada.requisicao is not None
            if botoes or tem_curl:
                with Horizontal(classes="result-copy-bar"):
                    for i, (label, _valor) in botoes:
                        yield Button(f"Copiar {label}", id=f"copy-{i}", classes="secondary")
                    if tem_curl:
                        yield Button("Copiar cURL", id="copy-curl", classes="secondary")
            if self._rodape:
                yield Static(self._rodape, classes="result-hint")
            yield Static("Esc para fechar", classes="result-hint")

    def on_mount(self) -> None:
        if self._is_error:
            self.add_class("-error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        botao = event.button.id or ""
        if botao == "copy-curl" and self._chamada and self._chamada.requisicao:
            self.app.copy_to_clipboard(self._chamada.requisicao.curl())
            self.app.notify("cURL copiado (token mascarado)!", timeout=2)
        elif botao.startswith("copy-") and botao != "copy-curl":
            label, valor = self._copiaveis[int(botao.removeprefix("copy-"))]
            self.app.copy_to_clipboard(valor)
            self.app.notify(f"{label} copiado!", timeout=2)

    def action_fechar(self) -> None:
        self.dismiss(None)


class RequisicaoScreen(ModalScreen[None]):
    """Prévia: mostra a requisição que SERIA enviada (método, URL final, corpo)."""

    BINDINGS = [("escape", "fechar", "Fechar")]

    def __init__(self, requisicao: RequisicaoPrevista) -> None:
        self._requisicao = requisicao
        super().__init__()

    def compose(self):
        with Container(classes="card"):
            yield Static("Requisição prevista (nada foi enviado)", classes="card-title")
            yield _corpo(self._requisicao.texto())
            with Horizontal(classes="result-copy-bar"):
                yield Button("Copiar cURL", id="copy-curl", classes="primary")
                yield Button("Copiar URL", id="copy-url", classes="secondary")
            yield Static("Esc para fechar", classes="result-hint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy-curl":
            self.app.copy_to_clipboard(self._requisicao.curl())
            self.app.notify("cURL copiado (token mascarado)!", timeout=2)
        elif event.button.id == "copy-url":
            self.app.copy_to_clipboard(self._requisicao.url)
            self.app.notify("URL copiada!", timeout=2)

    def action_fechar(self) -> None:
        self.dismiss(None)


def erro_para_tela(exc: DocNuvemAPIError) -> ResultScreen:
    """Formata uma DocNuvemAPIError (com URL completa e corpo bruto) numa ResultScreen."""
    linhas = [f"Status HTTP: {exc.status_code if exc.status_code is not None else '(sem resposta)'}"]
    linhas.append(f"URL: {exc.url}")
    linhas.append("")
    if exc.parsed:
        if "variaveisFaltantes" in exc.parsed:
            faltantes = ", ".join(exc.parsed["variaveisFaltantes"])
            linhas.append(f"Variáveis faltantes: {faltantes}")
            linhas.append("")
        if "retorno" in exc.parsed:
            linhas.append(f"Mensagem: {exc.parsed['retorno']}")
            linhas.append("")
    linhas.append("Corpo da resposta:")
    linhas.append(exc.body_text or "(vazio)")
    titulo = f"Erro {exc.status_code}" if exc.status_code else "Erro de conexão"
    return ResultScreen(titulo, "\n".join(linhas), is_error=True, chamada=exc.chamada)
