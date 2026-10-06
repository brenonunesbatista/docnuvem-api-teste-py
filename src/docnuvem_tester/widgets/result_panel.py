"""Tela de resultado: console de saída com JSON formatado + ações de copiar."""

from __future__ import annotations

from typing import Any

from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea

from docnuvem_tester.client import DocNuvemAPIError
from docnuvem_tester.formatting import formatar_json


class ResultScreen(ModalScreen[None]):
    """Painel de saída somente-leitura com o JSON da resposta (ou erro) da API.

    `copiaveis` é uma lista de (rótulo, valor) que vira botões "Copiar <rótulo>",
    sempre copiando o valor original (nunca uma versão truncada para exibição).
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
    ) -> None:
        self._titulo = titulo
        self._texto = data if isinstance(data, str) else formatar_json(data)
        self._is_error = is_error
        self._copiaveis = copiaveis or []
        self._rodape = rodape
        super().__init__()

    def compose(self):
        with Container(classes="card"):
            yield Static(self._titulo, classes="card-title")
            with VerticalScroll(id="result-body"):
                area = TextArea(self._texto, read_only=True, id="result-text")
                area.show_line_numbers = False
                yield area
            if self._copiaveis:
                with Horizontal(classes="result-copy-bar"):
                    for i, (label, _valor) in enumerate(self._copiaveis):
                        yield Button(f"Copiar {label}", id=f"copy-{i}", classes="secondary")
            if self._rodape:
                yield Static(self._rodape, classes="result-hint")
            yield Static("Esc para fechar", classes="result-hint")

    def on_mount(self) -> None:
        if self._is_error:
            self.add_class("-error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("copy-"):
            idx = int(event.button.id.removeprefix("copy-"))
            label, valor = self._copiaveis[idx]
            self.app.copy_to_clipboard(valor)
            self.app.notify(f"{label} copiado!", timeout=2)

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
    return ResultScreen(titulo, "\n".join(linhas), is_error=True)
