"""Fixtures dos testes de navegador (Playwright).

Eles abrem a página de verdade num navegador sem janela, contra a API falsa dos outros testes.
São mais lentos, então ficam de fora do `pytest` comum: rode com `pytest -m browser`.

No Windows usa o Edge que já vem instalado. Em outros sistemas (ou para outro navegador), defina
DOCNUVEM_BROWSER_CHANNEL (ex.: "chrome") ou deixe vazio para o Chromium do Playwright
(`playwright install chromium`).
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from ..conftest import App

TEMPO = 10_000  # ms: quanto esperar por algo aparecer na tela


class Pagina:
    """A página aberta, com atalhos para o que os testes fazem o tempo todo."""

    def __init__(self, page: Any, base: str) -> None:
        self.page = page
        self.base = base
        self.erros: list[str] = []
        page.set_default_timeout(TEMPO)
        page.on("pageerror", lambda e: self.erros.append(f"pageerror: {e}"))
        page.on("console", self._console)

    def _console(self, msg: Any) -> None:
        if msg.type != "error":
            return
        texto = msg.text
        if "favicon" in texto or "Failed to load resource" in texto:  # fontes/ícone fora do ar
            return
        # A marcação <x-dc> chega ao navegador com os "{{ ... }}" ainda por preencher; os
        # <path d="{{ic.x}}"> dos ícones geram este aviso antes do runtime renderizar. É inofensivo.
        if "attribute d: Expected moveto path command" in texto and "{{" in texto:
            return
        self.erros.append(f"console.error: {texto}")

    def abrir(self, base: str | None = None) -> None:
        self.page.goto(base or self.base)
        self.page.wait_for_selector("#perfil option", state="attached")

    def ir(self, rotulo: str) -> None:
        """Clica no item do menu (o texto pode ganhar um contador, como em 'Vigia (2)')."""
        padrao = re.compile(rf"^\s*{re.escape(rotulo)}(\s*\(\d+\))?\s*$")
        self.page.locator(".navb").filter(has_text=padrao).click()

    def botao(self, texto: str, *, exato: bool = False) -> Any:
        return self.page.locator("main").get_by_role("button", name=texto, exact=exato)

    def texto(self) -> str:
        return str(self.page.locator("main").inner_text())


@pytest.fixture(scope="session")
def navegador() -> Iterator[Any]:
    sync_api = pytest.importorskip("playwright.sync_api")
    canal = os.environ.get("DOCNUVEM_BROWSER_CHANNEL", "msedge" if sys.platform == "win32" else "")
    with sync_api.sync_playwright() as p:
        try:
            navegador = p.chromium.launch(channel=canal or None, headless=True)
        except Exception as exc:  # noqa: BLE001 - sem navegador: pula, não falha
            pytest.skip(f"não consegui abrir o navegador ({canal or 'chromium'}): {exc}")
        yield navegador
        navegador.close()


@pytest.fixture
def abrir(navegador: Any, app: App) -> Iterator[Any]:
    """Abre a página do servidor de teste. `abrir()` usa o `app`; passe outra base para outro."""
    contexto = navegador.new_context(viewport={"width": 1366, "height": 900}, accept_downloads=True)
    paginas: list[Pagina] = []

    def _abrir(base: str | None = None) -> Pagina:
        p = Pagina(contexto.new_page(), app.base)
        p.abrir(base)
        paginas.append(p)
        return p

    yield _abrir
    contexto.close()
    for p in paginas:  # um erro de JavaScript em qualquer teste é falha
        assert p.erros == [], "\n".join(p.erros)


@pytest.fixture
def pagina(abrir: Any) -> Pagina:
    return abrir()  # type: ignore[no-any-return]
