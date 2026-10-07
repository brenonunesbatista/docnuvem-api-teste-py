"""Tela base para formulários que chamam a API: loading, erro e validação local."""

from __future__ import annotations

from typing import Any

from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, LoadingIndicator, Static

from docnuvem_tester.client import DocnuvemAPIError, PreviaCapturada, RequisicaoPrevista
from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.screens.inline import ValidacaoInlineMixin, caixa_avisos
from docnuvem_tester.widgets.confirm_dialog import AlertScreen
from docnuvem_tester.widgets.result_panel import RequisicaoScreen, ResultScreen, erro_para_tela

SUFIXO_ULTIMO_ID = "-ultimo"


class FormScreen(ValidacaoInlineMixin, ModalScreen[None]):
    """Tela modal de formulário: monta campos, valida localmente, chama a API.

    Subclasses implementam `titulo()`, `compose_form()`, `chamar_api()` e
    `ao_sucesso()`; opcionalmente `validar()` para checagens locais (CPF,
    extensão de arquivo, etc.) que abrem um AlertScreen antes de gastar uma
    chamada de rede.

    Observação: o Textual chama o handler de cada classe do MRO. Subclasses que
    definem `on_button_pressed`/`on_input_changed` NÃO devem chamar
    `super()` — o handler daqui já roda sozinho.
    """

    BINDINGS = [
        ("escape", "voltar", "Voltar"),
        ("f5", "executar", "Executar"),
        ("f2", "previa", "Prévia"),
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

    def ao_erro_api(self, exc: DocnuvemAPIError) -> bool:
        """Ponto de extensão para tratar um erro da API sem sair do formulário
        (ex.: destacar campos citados em `variaveisFaltantes`). Retornar True
        significa que a tela já tratou o erro (mostrou sua própria mensagem
        inclusive, se for o caso) e a ResultScreen padrão não deve aparecer."""
        return False

    async def chamar_api(self, perfil: PerfilConfig) -> Any:
        raise NotImplementedError

    async def ao_sucesso(self, resultado: Any) -> None:
        """Chamado com o formulário já desmontado (self.dismiss já foi feito).

        Normalmente basta empilhar uma tela de resultado com
        `self.app.push_screen(...)` — NÃO chame `self.dismiss()` aqui.
        """
        raise NotImplementedError

    def compose(self):
        with Container(classes="card"):
            yield Static(self.titulo(), classes="card-title")
            with VerticalScroll():
                yield from self.compose_form()
            yield caixa_avisos()
            yield LoadingIndicator(id="form-loading")
            with Horizontal(classes="form-actions"):
                yield Button("Prévia (F2)", id="form-previa", classes="secondary")
                yield Button("Executar (F5)", id="form-submit", classes="primary")
                yield Button("Voltar (Esc)", id="form-cancel", classes="secondary")

    def on_mount(self) -> None:
        self.query_one("#form-loading", LoadingIndicator).display = False

    def action_voltar(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        botao = event.button.id or ""
        if botao == "form-submit":
            event.stop()
            self.action_executar()
        elif botao == "form-cancel":
            event.stop()
            self.action_voltar()
        elif botao == "form-previa":
            event.stop()
            self.action_previa()
        elif botao.endswith(SUFIXO_ULTIMO_ID):
            event.stop()
            self._usar_ultimo_documento_id(botao.removesuffix(SUFIXO_ULTIMO_ID))

    def _usar_ultimo_documento_id(self, id_campo: str) -> None:
        ultimo = getattr(self.app, "ultimo_documento_id", None)
        if ultimo is None:
            self.app.notify("Nenhum documentoId recebido ainda nesta sessão.", severity="warning")
            return
        self.query_one(f"#{id_campo}", Input).value = str(ultimo)

    def action_executar(self) -> None:
        self.run_worker(self._executar(), exclusive=True)

    def action_previa(self) -> None:
        self.run_worker(self._previa(), exclusive=True)

    def _set_loading(self, ligado: bool) -> None:
        self.query_one("#form-loading", LoadingIndicator).display = ligado
        self.query_one("#form-submit", Button).disabled = ligado

    async def _sem_perfil(self) -> None:
        await self.app.push_screen_wait(
            AlertScreen(
                "Sem perfil ativo",
                ["Configure um perfil em Perfil antes de chamar a API."],
            )
        )

    async def _previa(self) -> None:
        perfil = getattr(self.app, "perfil_ativo", None)
        if perfil is None:
            await self._sem_perfil()
            return
        avisos = self.validar()
        if avisos:
            await self.app.push_screen_wait(AlertScreen("Verifique os dados", avisos))
            return
        client = self.app.client  # type: ignore[attr-defined]
        requisicao: RequisicaoPrevista | None = None
        with client.modo_previa():
            try:
                await self.chamar_api(perfil)
            except PreviaCapturada as capturada:
                requisicao = capturada.requisicao
            except Exception as exc:  # ex.: arquivo sumiu entre a validação e a prévia
                self.app.push_screen(ResultScreen("Erro inesperado", str(exc), is_error=True))
                return
        if requisicao is not None:
            self.app.push_screen(RequisicaoScreen(requisicao))

    async def _executar(self) -> None:
        perfil = getattr(self.app, "perfil_ativo", None)
        if perfil is None:
            await self._sem_perfil()
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
        except DocnuvemAPIError as exc:
            self._set_loading(False)
            if not self.ao_erro_api(exc):
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
