"""Tela do endpoint POST /api/assinatura."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from pydantic import ValidationError
from textual.containers import Horizontal
from textual.widgets import Checkbox, Input, Static

from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.models import DocumentoStatusResponse, SolicitacaoAssinaturaRequest
from docnuvem_tester.screens.base import FormScreen
from docnuvem_tester.screens.formfields import campo, campo_documento_id, erros_pydantic
from docnuvem_tester.validators import lista_inteiros, max_caracteres, so_digitos
from docnuvem_tester.widgets.dynamic_list import SignatariosEditor
from docnuvem_tester.widgets.result_panel import ResultScreen


class AssinaturaScreen(FormScreen):
    """Formulário de POST /api/assinatura.

    Aceita `documento_id_inicial` e `on_sucesso` para ser reaproveitado pelo
    Fluxo Completo (importar + assinar em sequência) sem duplicar o formulário.
    """

    def __init__(
        self,
        documento_id_inicial: int | None = None,
        on_sucesso: Callable[[DocumentoStatusResponse], Awaitable[None]] | None = None,
        titulo_extra: str | None = None,
    ) -> None:
        self._documento_id_inicial = documento_id_inicial
        self._on_sucesso_callback = on_sucesso
        self._titulo_extra = titulo_extra
        self._req: SolicitacaoAssinaturaRequest | None = None
        super().__init__()

    def titulo(self) -> str:
        return self._titulo_extra or "Solicitar assinatura — POST /api/assinatura"

    def compose_form(self):
        yield Static(
            "Cria a solicitação e devolve o link de cada signatário (não envia e-mail).",
            classes="form-hint",
        )
        yield campo_documento_id(
            "Documento ID*",
            "in-documento-id",
            "123",
            value=str(self._documento_id_inicial) if self._documento_id_inicial else "",
        )
        yield Static("Signatários:", classes="form-hint")
        yield SignatariosEditor()
        yield Horizontal(
            Static("Considera ordem", classes="form-label"),
            Checkbox(id="in-considera-ordem"),
            classes="form-row",
        )
        yield campo("Data de validade (ISO)", "in-data-validade", "2026-12-31T23:59:59")
        yield campo("Prazo em dias", "in-prazo-dias", "15", validators=[so_digitos("Prazo em dias")])
        yield Static("Métodos:", classes="form-hint")
        yield Horizontal(
            Checkbox("Eletrônica", value=True, id="in-metodo-eletronica"),
            Checkbox("Digital", id="in-metodo-digital"),
            Checkbox("BirdID", id="in-metodo-birdid"),
            classes="form-row",
        )
        yield campo("Texto do e-mail", "in-texto-email")
        yield campo("Login do solicitante", "in-login")
        yield campo(
            "Referência externa (até 64)", "in-referencia",
            validators=[max_caracteres("Referência externa", 64)],
        )
        yield campo(
            "Lembretes (dias, separados por vírgula)", "in-lembretes", "1,7",
            validators=[lista_inteiros("Lembretes")],
        )

    def validar(self) -> list[str]:
        avisos: list[str] = []
        signatarios = self.query_one(SignatariosEditor).valores()
        if not signatarios:
            avisos.append("Adicione ao menos um signatário.")
        else:
            for i, sig in enumerate(signatarios, start=1):
                if not sig["nome"] or not sig["email"] or not sig["cpf"]:
                    avisos.append(f"Signatário {i}: nome, e-mail e CPF são obrigatórios.")

        metodos = []
        if self.query_one("#in-metodo-eletronica", Checkbox).value:
            metodos.append("eletronica")
        if self.query_one("#in-metodo-digital", Checkbox).value:
            metodos.append("digital")
        if self.query_one("#in-metodo-birdid", Checkbox).value:
            metodos.append("birdid")

        lembretes_raw = self.query_one("#in-lembretes", Input).value.strip()
        lembretes = None
        if lembretes_raw:
            try:
                lembretes = [int(x.strip()) for x in lembretes_raw.split(",") if x.strip()]
            except ValueError:
                avisos.append("Lembretes deve ser números separados por vírgula (ex.: 1,7).")

        documento_id_raw = self.query_one("#in-documento-id", Input).value.strip()
        documento_id = None
        if not documento_id_raw:
            avisos.append("Informe o Documento ID.")
        elif not documento_id_raw.isdigit():
            avisos.append("Documento ID deve ser um número.")
        else:
            documento_id = int(documento_id_raw)

        prazo_raw = self.query_one("#in-prazo-dias", Input).value.strip()
        prazo_dias = None
        if prazo_raw:
            if not prazo_raw.isdigit():
                avisos.append("Prazo em dias deve ser um número.")
            else:
                prazo_dias = int(prazo_raw)

        if avisos:
            return avisos

        dados = {
            "documentoId": documento_id,
            "signatarios": signatarios,
            "consideraOrdem": self.query_one("#in-considera-ordem", Checkbox).value,
            "dataValidade": self.query_one("#in-data-validade", Input).value.strip() or None,
            "prazoDias": prazo_dias,
            "metodos": metodos or ["eletronica"],
            "textoEmail": self.query_one("#in-texto-email", Input).value.strip() or None,
            "loginSolicitante": self.query_one("#in-login", Input).value.strip() or None,
            "referenciaExterna": self.query_one("#in-referencia", Input).value.strip() or None,
            "lembretes": lembretes,
        }
        try:
            self._req = SolicitacaoAssinaturaRequest.model_validate(dados)
        except ValidationError as exc:
            return erros_pydantic(exc)
        return []

    async def chamar_api(self, perfil: PerfilConfig) -> DocumentoStatusResponse:
        assert self._req is not None
        client = self.app.client  # type: ignore[attr-defined]
        return await client.criar_assinatura(perfil, self._req)

    async def ao_sucesso(self, resultado: Any) -> None:
        if self._on_sucesso_callback is not None:
            await self._on_sucesso_callback(resultado)
            return
        copiaveis = [
            (f"link de {s.nome}", s.linkAssinatura)
            for s in resultado.signatarios
            if s.linkAssinatura
        ]
        self.app.push_screen(
            ResultScreen(
                "Assinatura solicitada",
                resultado,
                copiaveis=copiaveis,
                chamada=self.app.ultima_chamada,  # type: ignore[attr-defined]
            )
        )
