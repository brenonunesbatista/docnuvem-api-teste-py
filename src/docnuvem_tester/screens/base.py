"""Tela base para formulários que chamam a API: loading, erro e validação local."""

from __future__ import annotations

from typing import Any

from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, LoadingIndicator, Static

from docnuvem_tester.client import DocNuvemAPIError
from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.widgets.confirm_dialog import AlertScreen
from docnuvem_tester.widgets.result_panel import ResultScreen, erro_para_tela


class SemPerfilAtivoError(Exception):
    """Levantado quando não há perfil configurado/ativo para chamar a API."""


class FormScreen(ModalScreen[None]):
    """Tela modal de formulário: monta campos, valida localmente, chama a API.

    Subclasses implementam `titulo()`, `compose_form()`, `chamar_api()` e
    `ao_sucesso()`; opcionalmente `validar()` para checagens locais (CPF,
    extensão de arquivo, etc.) que abrem um AlertScreen antes de gastar uma
    chamada de rede.
    """

    BINDINGS = [
        ("escape", "voltar", "Voltar"),
        ("f5", "executar", "Executar"),
    ]

    def titulo(self) -> str:
        raise NotImplementedError

    def compose_form(self):
        raise NotImplementedError

    def validar(self) -> list[str]:
        return []

    async def confirmar(self) -> bool:
        """Ponto de extensão para ações destrutivas (ex.: cancelar assinatura)."""
        return True

    async def chamar_api(self, perfil: PerfilConfig) -> Any:
        raise NotImplementedError

    async def ao_sucesso(self, resultado: Any) -> None:
        """Chamado com o formulário já desmontado (self.dismiss já foi feito).

        Normalmente basta empilhar uma tela de resultado com
        `self.app.push_screen(...)` — NÃO chame `self.dismiss()` aqui.
        """
        raise NotImplementedError

    def compose(self):
        with Container(classes="dos-window"):
            yield Static(self.titulo(), classes="dos-window-title")
            with VerticalScroll():
                yield from self.compose_form()
            yield LoadingIndicator(id="form-loading")
            with Horizontal(classes="form-actions"):
                yield Button("Executar (F5)", id="form-submit", classes="primary")
                yield Button("Voltar (Esc)", id="form-cancel", classes="secondary")

    def on_mount(self) -> None:
        self.query_one("#form-loading", LoadingIndicator).display = False

    def action_voltar(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "form-submit":
            event.stop()
            self.action_executar()
        elif event.button.id == "form-cancel":
            event.stop()
            self.action_voltar()

    def action_executar(self) -> None:
        self.run_worker(self._executar(), exclusive=True)

    def _set_loading(self, ligado: bool) -> None:
        self.query_one("#form-loading", LoadingIndicator).display = ligado
        self.query_one("#form-submit", Button).disabled = ligado

    async def _executar(self) -> None:
        perfil = getattr(self.app, "perfil_ativo", None)
        if perfil is None:
            await self.app.push_screen_wait(
                AlertScreen(
                    "Sem perfil ativo",
                    ["Configure um perfil em Perfil antes de chamar a API."],
                )
            )
            return

        avisos = self.validar()
        if avisos:
            await self.app.push_screen_wait(AlertScreen("Verifique os dados", avisos))
            return

        if not await self.confirmar():
            return

        self._set_loading(True)
        try:
            resultado = await self.chamar_api(perfil)
        except DocNuvemAPIError as exc:
            self._set_loading(False)
            self.app.push_screen(erro_para_tela(exc))
            return
        except Exception as exc:  # erro local inesperado (não deve derrubar a TUI)
            self._set_loading(False)
            self.app.push_screen(ResultScreen("Erro inesperado", str(exc), is_error=True))
            return
        self._set_loading(False)
        # Sempre desmontar este formulário ANTES de empilhar a próxima tela: se
        # ao_sucesso() empilhar algo e só depois chamar dismiss(), este deixa de
        # ser o topo da pilha e o dismiss corrompe o future interno do Textual
        # (InvalidStateError). Centralizado aqui para nenhuma subclasse repetir.
        self.dismiss(None)
        await self.ao_sucesso(resultado)
