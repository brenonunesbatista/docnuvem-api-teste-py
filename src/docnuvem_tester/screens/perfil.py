"""Troca do perfil (instância/token) ativo, sem sair do programa."""

from __future__ import annotations

from textual.containers import Container
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from docnuvem_tester.config import config_path


class PerfilScreen(ModalScreen[None]):
    BINDINGS = [("escape", "voltar", "Voltar")]

    def compose(self):
        with Container(classes="dos-window"):
            yield Static("Perfil ativo", classes="dos-window-title")
            config = getattr(self.app, "config", None)
            if config is None:
                yield Static(
                    "config.json não encontrado ou inválido.\n"
                    f"Caminho esperado: {config_path()}\n"
                    "Copie config.example.json para config.json e preencha os tokens, "
                    "depois reabra esta tela.",
                    classes="alert-message",
                )
            else:
                with ListView(id="perfil-list"):
                    for nome, perfil in config.perfis.items():
                        marca = " (ativo)" if nome == getattr(self.app, "perfil_atual", None) else ""
                        yield ListItem(
                            Label(f"{nome}{marca} — {perfil.instancia} @ {perfil.baseUrl}")
                        )
            yield Static("Enter para ativar   |   Esc para voltar", classes="result-hint")

    def on_mount(self) -> None:
        try:
            self.query_one("#perfil-list", ListView).focus()
        except NoMatches:
            pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        config = getattr(self.app, "config", None)
        if config is None:
            return
        nomes = list(config.perfis.keys())
        index = event.list_view.index
        if index is None:
            return
        nome = nomes[index]
        self.app.perfil_atual = nome  # type: ignore[attr-defined]
        self.app.notify(f"Perfil ativo: {nome}", timeout=2)
        self.dismiss(None)

    def action_voltar(self) -> None:
        self.dismiss(None)
