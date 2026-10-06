"""Listas dinâmicas editáveis (variáveis chave/valor, signatários)."""

from __future__ import annotations

from typing import Any

from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Button, Input

from docnuvem_tester.validators import cpf_valido, email_valido, so_digitos


class DynamicListEditor(Vertical):
    """Lista de linhas removíveis com um botão "+ adicionar" no rodapé."""

    def __init__(self, label_botao_add: str) -> None:
        self._label_botao_add = label_botao_add
        self._row_count = 0
        super().__init__()

    def compose(self):
        yield Vertical(id="dyn-rows")
        yield Button(self._label_botao_add, id="dyn-add", classes="secondary")

    def on_mount(self) -> None:
        self.adicionar_linha()

    def build_row(self, row_id: str) -> Widget:
        raise NotImplementedError

    def adicionar_linha(self) -> None:
        row_id = f"row-{self._row_count}"
        self._row_count += 1
        self.query_one("#dyn-rows", Vertical).mount(self.build_row(row_id))

    def remover_linha(self, row_id: str) -> None:
        try:
            self.query_one(f"#{row_id}").remove()
        except NoMatches:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dyn-add":
            event.stop()
            self.adicionar_linha()
        elif event.button.id and event.button.id.startswith("remove-"):
            event.stop()
            self.remover_linha(event.button.id.removeprefix("remove-"))


class SignatariosEditor(DynamicListEditor):
    """Editor de lista de signatários (nome, e-mail, CPF, telefone, ordem)."""

    def __init__(self) -> None:
        super().__init__("+ Signatário")

    def build_row(self, row_id: str) -> Widget:
        return Vertical(
            Horizontal(
                Input(placeholder="Nome*", classes="sig-nome"),
                Input(placeholder="E-mail*", classes="sig-email", validators=[email_valido()]),
            ),
            Horizontal(
                Input(placeholder="CPF*", classes="sig-cpf", validators=[cpf_valido()]),
                Input(placeholder="Telefone", classes="sig-telefone"),
                Input(
                    placeholder="Ordem (0, 1, 2...)",
                    classes="sig-ordem",
                    validators=[so_digitos("Ordem")],
                ),
                Button("Remover", id=f"remove-{row_id}", classes="secondary"),
            ),
            id=row_id,
            classes="dyn-row",
        )

    def valores(self) -> list[dict[str, Any]]:
        resultado: list[dict[str, Any]] = []
        for row in self.query(".dyn-row"):
            nome = row.query_one(".sig-nome", Input).value.strip()
            email = row.query_one(".sig-email", Input).value.strip()
            cpf = row.query_one(".sig-cpf", Input).value.strip()
            telefone = row.query_one(".sig-telefone", Input).value.strip()
            ordem = row.query_one(".sig-ordem", Input).value.strip()
            if nome or email or cpf:
                resultado.append(
                    {
                        "nome": nome,
                        "email": email,
                        "cpf": cpf,
                        "telefone": telefone or None,
                        "ordem": int(ordem) if ordem.isdigit() else (ordem or None),
                    }
                )
        return resultado
