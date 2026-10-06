"""Tema escuro moderno do app (paleta fixa, independente do terminal)."""

from __future__ import annotations

from textual.theme import Theme

DOCNUVEM_THEME = Theme(
    name="docnuvem",
    dark=True,
    primary="#58a6ff",
    secondary="#8b949e",
    accent="#a371f7",
    success="#3fb950",
    warning="#d29922",
    error="#f85149",
    foreground="#e6edf3",
    background="#0d1117",
    surface="#161b22",
    panel="#1c2330",
    variables={
        "border": "#3d444d",
        "border-blurred": "#3d444d",
        "text-muted": "#8b949e",
        "block-cursor-background": "#1f3a5f",
        "block-cursor-foreground": "#e6edf3",
        "block-cursor-text-style": "bold",
        "block-hover-background": "#21262d",
        "scrollbar": "#3d444d",
        "scrollbar-hover": "#484f58",
        "scrollbar-active": "#58a6ff",
        "scrollbar-background": "#0d1117",
        "scrollbar-background-hover": "#0d1117",
        "scrollbar-background-active": "#0d1117",
        "footer-background": "#161b22",
        "footer-foreground": "#8b949e",
        "footer-item-background": "#161b22",
        "footer-key-foreground": "#58a6ff",
        "footer-key-background": "#161b22",
        "footer-description-foreground": "#8b949e",
        "footer-description-background": "#161b22",
    },
)
