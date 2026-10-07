"""Log de requisição/resposta (atalho L): método, URL, timestamp, status."""

from __future__ import annotations

import json

from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from docnuvem_tester.client import CallLogEntry, DocnuvemAPIError
from docnuvem_tester.formatting import valor_ou_traco
from docnuvem_tester.widgets.result_panel import ResultScreen, erro_para_tela

ESTRELA = "★"


def _corpo_legivel(texto: str | None) -> str:
    if not texto:
        return "(sem corpo registrado)"
    try:
        return json.dumps(json.loads(texto), indent=2, ensure_ascii=False)
    except ValueError:
        return texto


class CallLogScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "fechar", "Fechar"),
        ("f", "favoritar", "Favoritar"),
        ("r", "reenviar", "Reenviar"),
        ("c", "copiar_curl", "Copiar cURL"),
        ("s", "so_favoritos", "Só favoritos"),
    ]

    def __init__(self) -> None:
        self._entries: list[CallLogEntry] = []
        self._so_favoritos = False
        super().__init__()

    def compose(self):
        with Container(classes="card"):
            yield Static("Log de chamadas", classes="card-title")
            yield DataTable(id="log-table", cursor_type="row")
            yield Static(
                "Enter detalhes  ·  F favoritar  ·  R reenviar  ·  "
                "C copiar cURL  ·  S só favoritos  ·  Esc fechar",
                classes="result-hint",
            )

    def on_mount(self) -> None:
        table = self.query_one("#log-table", DataTable)
        table.add_columns(ESTRELA, "Hora", "Método", "Status", "Duração", "Auth", "URL")
        self._preencher()
        table.focus()

    def _preencher(self) -> None:
        table = self.query_one("#log-table", DataTable)
        historico = list(getattr(self.app, "call_log", []))
        self._entries = [e for e in reversed(historico) if e.favorito or not self._so_favoritos]
        table.clear()
        for entry in self._entries:
            table.add_row(
                ESTRELA if entry.favorito else "",
                entry.timestamp.strftime("%H:%M:%S"),
                entry.method,
                valor_ou_traco(entry.status_code),
                f"{entry.duration_ms:.0f}ms" if entry.duration_ms is not None else "—",
                entry.auth_masked,
                entry.url,
            )

    def _selecionada(self) -> CallLogEntry | None:
        table = self.query_one("#log-table", DataTable)
        if not self._entries or table.cursor_row >= len(self._entries):
            return None
        return self._entries[table.cursor_row]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        entry = self._entries[event.cursor_row]
        corpo = entry.error_body if entry.error_body else entry.response_text
        self.app.push_screen(
            ResultScreen(
                f"Chamada {entry.method}",
                _corpo_legivel(corpo),
                is_error=not entry.ok,
                chamada=entry,
            )
        )

    def action_favoritar(self) -> None:
        entry = self._selecionada()
        if entry is None:
            return
        entry.favorito = not entry.favorito
        linha = self.query_one("#log-table", DataTable).cursor_row
        self._preencher()
        if self._entries:
            self.query_one("#log-table", DataTable).move_cursor(row=min(linha, len(self._entries) - 1))

    def action_so_favoritos(self) -> None:
        self._so_favoritos = not self._so_favoritos
        self._preencher()
        self.app.notify("Mostrando só favoritos" if self._so_favoritos else "Mostrando todas", timeout=2)

    def action_copiar_curl(self) -> None:
        entry = self._selecionada()
        if entry is None or entry.requisicao is None:
            return
        self.app.copy_to_clipboard(entry.requisicao.curl())
        self.app.notify("cURL copiado (token mascarado)!", timeout=2)

    def action_reenviar(self) -> None:
        entry = self._selecionada()
        if entry is not None:
            self.run_worker(self._reenviar(entry), exclusive=True)

    async def _reenviar(self, entry: CallLogEntry) -> None:
        client = self.app.client  # type: ignore[attr-defined]
        try:
            resposta = await client.reenviar(entry)
        except DocnuvemAPIError as exc:
            self._preencher()
            self.app.push_screen(erro_para_tela(exc))
            return
        except (ValueError, OSError) as exc:  # sem dados para reenvio, arquivo sumiu...
            self.app.notify(f"Não foi possível reenviar: {exc}", severity="error")
            return
        self._preencher()
        self.app.push_screen(
            ResultScreen(
                f"Reenvio {entry.method} — resposta",
                _corpo_legivel(resposta.text),
                chamada=self.app.ultima_chamada,  # type: ignore[attr-defined]
            )
        )

    def action_fechar(self) -> None:
        self.dismiss(None)
