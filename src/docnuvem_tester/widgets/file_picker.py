"""Seletor de arquivo: diálogo nativo do SO (tkinter), com fallback em
DirectoryTree do próprio Textual quando não há display gráfico disponível
(ex.: sessão SSH sem X11)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DirectoryTree, Static

if TYPE_CHECKING:
    from textual.app import App


class NativeDialogUnavailable(Exception):
    """O diálogo nativo do SO não pôde ser aberto (sem display, tkinter ausente, etc.)."""


def _abrir_dialogo_tk(titulo: str) -> str | None:
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        caminho = filedialog.askopenfilename(title=titulo, parent=root)
    finally:
        root.destroy()
    return caminho or None


async def _escolher_arquivo_nativo(titulo: str) -> str | None:
    try:
        return await asyncio.to_thread(_abrir_dialogo_tk, titulo)
    except Exception as exc:  # tkinter.TclError, ImportError, RuntimeError sem display...
        raise NativeDialogUnavailable(str(exc)) from exc


class FilePickerModal(ModalScreen[str | None]):
    """Fallback dentro do terminal (DirectoryTree) quando o diálogo nativo falha."""

    BINDINGS = [("escape", "cancelar", "Cancelar")]

    def __init__(self, titulo: str = "Selecionar arquivo") -> None:
        self._titulo = titulo
        super().__init__()

    def compose(self):
        with Container(classes="dos-window"):
            yield Static(self._titulo, classes="dos-window-title")
            yield Static(
                "Diálogo nativo indisponível (sem display gráfico) — navegue e "
                "selecione um arquivo abaixo.",
                classes="form-hint",
            )
            yield DirectoryTree(str(Path.home()), id="file-tree")
            yield Static("Enter/clique seleciona o arquivo   |   Esc cancela", classes="result-hint")

    def on_mount(self) -> None:
        self.query_one(DirectoryTree).focus()

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(str(event.path))

    def action_cancelar(self) -> None:
        self.dismiss(None)


async def escolher_arquivo(app: "App", titulo: str = "Selecionar arquivo") -> str | None:
    """Abre o diálogo nativo do SO; se indisponível, cai para o seletor em Textual.

    Retorna o caminho escolhido, ou None se o usuário cancelou.
    """
    try:
        return await _escolher_arquivo_nativo(titulo)
    except NativeDialogUnavailable:
        return await app.push_screen_wait(FilePickerModal(titulo))
