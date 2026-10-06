"""Aviso inline de validação: lista, abaixo do formulário, o que está inválido agora."""

from __future__ import annotations

from textual.css.query import NoMatches
from textual.widgets import Input, Static


def caixa_avisos() -> Static:
    caixa = Static("", id="inline-erros", classes="inline-erros")
    caixa.display = False
    return caixa


class ValidacaoInlineMixin:
    """Atualiza a caixa `#inline-erros` a cada mudança em qualquer Input com validadores.

    O Input já pinta a própria borda de vermelho (classe -invalid); aqui só
    juntamos as mensagens. Como o Textual chama o handler de cada classe do
    MRO, subclasses podem ter o próprio on_input_changed sem conflito.
    """

    def on_input_changed(self, event: Input.Changed) -> None:
        self._atualizar_avisos_inline()

    def _atualizar_avisos_inline(self) -> None:
        mensagens: list[str] = []
        for campo in self.query(Input):  # type: ignore[attr-defined]
            if campo.value and campo.validators:
                resultado = campo.validate(campo.value)
                if resultado is not None and not resultado.is_valid:
                    for descricao in resultado.failure_descriptions:
                        if descricao not in mensagens:
                            mensagens.append(descricao)
        try:
            caixa = self.query_one("#inline-erros", Static)  # type: ignore[attr-defined]
        except NoMatches:
            return
        caixa.update("\n".join(f"⚠ {m}" for m in mensagens))
        caixa.display = bool(mensagens)
