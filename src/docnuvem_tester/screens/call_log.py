"""Log de requisição/resposta (atalho L): método, URL, timestamp, status."""

from __future__ import annotations

from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from docnuvem_tester.client import CallLogEntry
from docnuvem_tester.formatting import valor_ou_traco
from docnuvem_tester.widgets.result_panel import ResultScreen


class CallLogScreen(ModalScreen[None]):
    BINDINGS = [("escape", "fechar", "Fechar")]

    def __init__(self) -> None:
        self._entries: list[CallLogEntry] = []
        super().__init__()

    def compose(self):
        with Container(classes="card"):
            yield Static("Log de chamadas", classes="card-title")
            yield DataTable(id="log-table", cursor_type="row")
            yield Static("Enter para ver detalhes   |   Esc para fechar", classes="result-hint")

    def on_mount(self) -> None:
        table = self.query_one("#log-table", DataTable)
        table.add_columns("Hora", "Método", "Status", "Duração", "Auth", "URL")
        historico = list(getattr(self.app, "call_log", []))
        self._entries = list(reversed(historico))
        for entry in self._entries:
            table.add_row(
                entry.timestamp.strftime("%H:%M:%S"),
                entry.method,
                valor_ou_traco(entry.status_code),
                f"{entry.duration_ms:.0f}ms" if entry.duration_ms is not None else "—",
                entry.auth_masked,
                entry.url,
            )
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        entry = self._entries[event.cursor_row]
        linhas = [
            f"Método: {entry.method}",
            f"URL: {entry.url}",
            f"Status: {valor_ou_traco(entry.status_code)}",
            f"Auth: {entry.auth_masked}",
            f"Duração: {entry.duration_ms:.0f}ms" if entry.duration_ms is not None else "Duração: —",
            "",
        ]
        if entry.error_body:
            linhas.append("Corpo do erro:")
            linhas.append(entry.error_body)
        else:
            linhas.append("(sem corpo de erro registrado)")
        self.app.push_screen(
            ResultScreen(f"Chamada {entry.method}", "\n".join(linhas), is_error=not entry.ok)
        )

    def action_fechar(self) -> None:
        self.dismiss(None)
