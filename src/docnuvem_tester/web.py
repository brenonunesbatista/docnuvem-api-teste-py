"""Servidor local da interface web.

Serve a página `webapp/index.html` e repassa as chamadas à API do DocNuvem usando
os perfis do config.json. O token fica só neste processo: o navegador nunca o
recebe (a página só vê o token mascarado).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx

from docnuvem_tester.client import TIMEOUT, mascarar_token
from docnuvem_tester.config import AppConfig, ConfigError, load_config

PAGINA = Path(__file__).parent / "webapp" / "index.html"

# Um segmento de caminho não vazio e que não seja só pontos ("." / ".."), para que
# o ID de uma rota permitida nunca possa subir de diretório na API.
_SEG = r"(?!\.+(?:/|$))[A-Za-z0-9_.-]+"
# Só os endpoints da ferramenta: o servidor não é um proxy aberto.
ROTAS_PERMITIDAS: list[tuple[str, re.Pattern[str]]] = [
    (metodo, re.compile(padrao))
    for metodo, padrao in [
        ("POST", r"^/importar$"),
        ("POST", r"^/enviarParaEnvioInteligente$"),
        ("GET", r"^/api/modelos$"),
        ("POST", r"^/api/documento/from-template$"),
        ("POST", r"^/api/assinatura$"),
        ("DELETE", rf"^/api/assinatura/{_SEG}$"),
        ("GET", r"^/api/documentos$"),
        ("GET", rf"^/api/documento/{_SEG}/status$"),
        ("GET", rf"^/api/documento/{_SEG}/download$"),
    ]
]
HEADERS_RESPOSTA = {"content-type", "x-request-id", "date", "content-length", "location"}


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, endereco: tuple[str, int], cfg: AppConfig) -> None:
        super().__init__(endereco, Handler)
        self.cfg = cfg
        porta = endereco[1]
        self.hosts_permitidos = {f"127.0.0.1:{porta}", f"localhost:{porta}", f"[::1]:{porta}"}


class Handler(BaseHTTPRequestHandler):
    server: Servidor  # type: ignore[assignment]
    server_version = "docnuvem-web"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return  # a página já tem o log de chamadas

    # --- segurança: só aceita a própria página, em loopback -------------
    def _origem_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        if host not in self.server.hosts_permitidos:  # bloqueia DNS rebinding
            return False
        origin = self.headers.get("Origin")
        if origin and origin.lower() not in {f"http://{h}" for h in self.server.hosts_permitidos}:
            return False
        fetch_site = self.headers.get("Sec-Fetch-Site")
        return fetch_site in (None, "same-origin", "none")

    # --- respostas ------------------------------------------------------
    def _json(self, dados: object, status: int = 200) -> None:
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _texto(self, status: int, texto: str) -> None:
        corpo = texto.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    # --- rotas ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if not self._origem_ok():
            return self._texto(403, "Origem não permitida.")
        caminho = urlsplit(self.path).path
        if caminho in ("/", "/index.html"):
            try:
                corpo = PAGINA.read_bytes()
            except OSError:
                return self._texto(500, f"Página não encontrada: {PAGINA}")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)
            return
        if caminho == "/_perfis":
            cfg = self.server.cfg
            return self._json(
                {
                    "perfis": [
                        {
                            "id": nome,
                            "instancia": p.instancia,
                            "baseUrl": p.baseUrl,
                            "tokenMascarado": mascarar_token(p.token),
                        }
                        for nome, p in cfg.perfis.items()
                    ],
                    "padrao": cfg.perfilPadrao,
                }
            )
        if caminho.startswith("/_proxy/"):
            return self._proxy()
        self._texto(404, "Não encontrado.")

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy()

    def _proxy(self) -> None:
        if not self._origem_ok():
            return self._texto(403, "Origem não permitida.")
        partes = urlsplit(self.path)
        m = re.match(r"^/_proxy/([^/]+)(/.*)$", partes.path)
        if not m:
            return self._texto(404, "Não encontrado.")
        perfil = self.server.cfg.perfis.get(unquote(m.group(1)))
        if perfil is None:
            return self._texto(404, "Perfil desconhecido.")
        path = m.group(2)
        metodo = self.command
        if not any(mt == metodo and rx.match(path) for mt, rx in ROTAS_PERMITIDAS):
            return self._texto(403, f"Rota não permitida: {metodo} {path}")

        params: list[tuple[str, str | int | float | bool | None]] = [
            ("instancia", perfil.instancia.lower())
        ]
        params += [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True) if k != "instancia"]
        url = perfil.baseUrl.rstrip("/") + path
        url_final = str(httpx.Request(metodo, url, params=params).url)

        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else None
        headers = {"Authorization": f"Bearer {perfil.token}"}
        if self.headers.get("Content-Type"):
            headers["Content-Type"] = self.headers["Content-Type"]

        inicio = time.perf_counter()
        try:
            resp = httpx.request(
                metodo, url, params=params, content=corpo, headers=headers, timeout=TIMEOUT
            )
        except httpx.HTTPError as exc:
            ms = round((time.perf_counter() - inicio) * 1000)
            return self._json(
                {
                    "status": 502,
                    "ms": ms,
                    "url": url_final,
                    "headers": {},
                    "body": json.dumps(
                        {"retorno": f"Falha de conexão com {perfil.baseUrl}: {exc}"},
                        ensure_ascii=False,
                    ),
                }
            )
        ms = round((time.perf_counter() - inicio) * 1000)
        self._json(
            {
                "status": resp.status_code,
                "ms": ms,
                "url": url_final,
                "headers": {k: v for k, v in resp.headers.items() if k.lower() in HEADERS_RESPOSTA},
                "body": resp.text,
            }
        )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="docnuvem-web",
        description="Interface web do DocNuvem API Tester (usa os perfis do config.json).",
    )
    ap.add_argument("--porta", type=int, default=8765, help="porta local (padrão: 8765)")
    ap.add_argument("--nao-abrir", action="store_true", help="não abre o navegador")
    args = ap.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    try:
        servidor = Servidor(("127.0.0.1", args.porta), cfg)
    except OSError as exc:
        print(f"Não foi possível abrir a porta {args.porta}: {exc}", file=sys.stderr)
        sys.exit(1)

    url = f"http://127.0.0.1:{args.porta}/"
    print(f"Interface web em {url}  (Ctrl+C para sair)")
    print("Perfis: " + ", ".join(cfg.perfis) + ". Os tokens ficam só neste processo.")
    print("ATENÇÃO: as chamadas são REAIS e usam os tokens do config.json.")
    if not args.nao_abrir:
        webbrowser.open(url)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
