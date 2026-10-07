"""Servidor local da interface web.

Serve a página `webapp/index.html` e repassa as chamadas à API do Docnuvem usando
os perfis do config.json. O token fica só neste processo: o navegador nunca o
recebe (a página só vê o token mascarado).

Também guarda o histórico de chamadas em disco (JSON Lines) e verifica a saúde
da API para o indicador de status da página.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, unquote, urlsplit

import httpx

from docnuvem_tester.config import AppConfig, ConfigError, PerfilConfig, load_config

PASTA_WEB = Path(__file__).parent / "webapp"
PAGINA = PASTA_WEB / "index.html"
# Arquivos estáticos que a página pede (caminho -> arquivo, tipo).
ESTATICOS = {
    "/dc-runtime.js": ("dc-runtime.js", "application/javascript; charset=utf-8"),
    "/logo.png": ("logo.png", "image/png"),
}
PORTA_PADRAO = 8765
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_CORPO = 50 * 1024 * 1024  # maior arquivo/corpo aceito pelo proxy
MAX_ENTRADA = 1_000_000  # maior entrada do histórico (bytes)
MAX_HISTORICO = 5000  # entradas mantidas em disco
FOLGA_HISTORICO = 500  # só poda depois de passar do limite por essa folga
TTL_STATUS = 15.0  # segundos que o resultado do status fica em cache
LIMITE_API_LENTA_MS = 1500  # acima disso a API é considerada lenta
LIMITE_TOKEN_LENTO_MS = 3000  # idem para a consulta autenticada
CHAVES_HISTORICO = (
    "id",
    "ts",
    "method",
    "url",
    "status",
    "ms",
    "auth",
    "rv",
    "body",
    "req",
    "screen",
    "perfil",
)

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
        ("GET", r"^/api/diretorios$"),
        ("POST", r"^/api/solicitacaoAluno/solicitarEnvioDocumentos$"),
        ("GET", r"^/api/solicitacaoAluno/consultarStatus$"),
        ("GET", rf"^/api/documento/{_SEG}/status$"),
        ("GET", rf"^/api/documento/{_SEG}/download$"),
    ]
]
HEADERS_RESPOSTA = {"content-type", "x-request-id", "date", "content-length", "location"}


def mascarar_token(token: str) -> str:
    if not token:
        return "Bearer ***"
    return f"Bearer ***...{token[-4:] if len(token) >= 4 else token}"


def dados_dir() -> Path:
    """Pasta onde ficam os dados locais (histórico). Muda com DOCNUVEM_TESTER_DADOS."""
    env = os.environ.get("DOCNUVEM_TESTER_DADOS")
    return Path(env) if env else Path.home() / ".docnuvem-tester"


# --------------------------------------------------------------------------
# Histórico em disco
# --------------------------------------------------------------------------


class Historico:
    """Histórico de chamadas em JSON Lines. O token nunca entra aqui (só a máscara)."""

    def __init__(self, arquivo: Path) -> None:
        self.arquivo = arquivo
        self._lock = threading.Lock()
        self._n: int | None = None

    def _linhas(self) -> list[str]:
        try:
            texto = self.arquivo.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        return [linha for linha in texto.splitlines() if linha.strip()]

    def adicionar(self, entrada: dict[str, Any]) -> None:
        linha = json.dumps(entrada, ensure_ascii=False)
        with self._lock:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            with self.arquivo.open("a", encoding="utf-8") as f:
                f.write(linha + "\n")
            self._n = len(self._linhas()) if self._n is None else self._n + 1
            if self._n > MAX_HISTORICO + FOLGA_HISTORICO:
                linhas = self._linhas()[-MAX_HISTORICO:]
                self.arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
                self._n = len(linhas)

    def todas(self) -> list[dict[str, Any]]:
        """Da mais antiga para a mais recente."""
        with self._lock:
            linhas = self._linhas()
        itens: list[dict[str, Any]] = []
        for linha in linhas:
            try:
                dado = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if isinstance(dado, dict):
                itens.append(dado)
        return itens

    def recentes(self, limite: int) -> list[dict[str, Any]]:
        """Da mais recente para a mais antiga."""
        return self.todas()[-limite:][::-1]

    def limpar(self) -> None:
        with self._lock:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            self.arquivo.write_text("", encoding="utf-8")
            self._n = 0


def historico_csv(itens: list[dict[str, Any]]) -> bytes:
    """CSV (UTF-8 com BOM, abre direto no Excel) do histórico."""
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    w.writerow(["data_hora", "perfil", "metodo", "url", "status", "duracao_ms", "tela"])
    for e in itens:
        ts = e.get("ts")
        quando = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        w.writerow(
            [
                quando,
                e.get("perfil", ""),
                e.get("method", ""),
                e.get("url", ""),
                e.get("status", ""),
                e.get("ms", ""),
                e.get("screen", ""),
            ]
        )
    return saida.getvalue().encode("utf-8-sig")


# --------------------------------------------------------------------------
# Status da API
# --------------------------------------------------------------------------


def verificar_status(cliente: httpx.Client, perfil: PerfilConfig) -> dict[str, Any]:
    """Mede se a API responde e se o token do perfil é aceito (só leituras).

    nivel: ok | lento | token (401/403) | erro (5xx ou resposta inesperada) | offline.
    """
    base = perfil.baseUrl.rstrip("/")
    res: dict[str, Any] = {
        "verificadoEm": int(time.time() * 1000),
        "nivel": "offline",
        "api": {"ok": False, "ms": None, "status": None},
        "token": {"ok": None, "ms": None, "status": None},
    }
    t0 = time.perf_counter()
    try:
        r = cliente.get(base + "/v3/api-docs/swagger-config", timeout=8)
    except httpx.HTTPError as exc:
        res["api"]["erro"] = str(exc)[:200]
        return res
    api_ms = round((time.perf_counter() - t0) * 1000)
    res["api"].update(ok=r.status_code < 500, ms=api_ms, status=r.status_code)
    if r.status_code >= 500:
        res["nivel"] = "erro"
        return res
    t1 = time.perf_counter()
    try:
        r2 = cliente.get(
            base + "/api/modelos",
            params={"instancia": perfil.instancia.lower()},
            headers={"Authorization": f"Bearer {perfil.token}"},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        res["token"]["erro"] = str(exc)[:200]
        res["nivel"] = "erro"
        return res
    tok_ms = round((time.perf_counter() - t1) * 1000)
    res["token"].update(ok=r2.status_code == 200, ms=tok_ms, status=r2.status_code)
    if r2.status_code in (401, 403):
        res["nivel"] = "token"
    elif r2.status_code >= 400:
        res["nivel"] = "erro"
    elif api_ms > LIMITE_API_LENTA_MS or tok_ms > LIMITE_TOKEN_LENTO_MS:
        res["nivel"] = "lento"
    else:
        res["nivel"] = "ok"
    return res


# --------------------------------------------------------------------------
# Servidor
# --------------------------------------------------------------------------


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        endereco: tuple[str, int],
        cfg: AppConfig,
        historico: Historico | None = None,
        cliente: httpx.Client | None = None,
    ) -> None:
        super().__init__(endereco, Handler)
        self.cfg = cfg
        self.historico = historico
        self.cliente = cliente or httpx.Client(timeout=TIMEOUT)
        self._cliente_proprio = cliente is None
        porta = self.server_address[1]  # a real, mesmo quando se pediu a porta 0
        self.hosts_permitidos = {f"127.0.0.1:{porta}", f"localhost:{porta}", f"[::1]:{porta}"}
        self._status_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._status_lock = threading.Lock()

    def status_de(self, nome: str, perfil: PerfilConfig, forcar: bool = False) -> dict[str, Any]:
        agora = time.monotonic()
        with self._status_lock:
            guardado = self._status_cache.get(nome)
            if guardado and not forcar and agora - guardado[0] < TTL_STATUS:
                return guardado[1]
        res = verificar_status(self.cliente, perfil)
        with self._status_lock:
            self._status_cache[nome] = (agora, res)
        return res

    def server_close(self) -> None:
        super().server_close()
        if self._cliente_proprio:
            self.cliente.close()


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
    def _enviar(
        self, status: int, corpo: bytes, tipo: str, extra: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, dados: object, status: int = 200) -> None:
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self._enviar(status, corpo, "application/json; charset=utf-8")

    def _texto(self, status: int, texto: str) -> None:
        self._enviar(status, texto.encode("utf-8"), "text/plain; charset=utf-8")

    def _ler_corpo(self, limite: int) -> bytes | None:
        """Lê o corpo da requisição; devolve None (e responde 413) se passar do limite."""
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho > limite:
            self._texto(413, f"Corpo grande demais (máximo {limite // (1024 * 1024)} MB).")
            return None
        return self.rfile.read(tamanho) if tamanho else b""

    # --- rotas ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if not self._origem_ok():
            return self._texto(403, "Origem não permitida.")
        partes = urlsplit(self.path)
        caminho = partes.path
        if caminho in ("/", "/index.html"):
            return self._arquivo(PAGINA, "text/html; charset=utf-8")
        if caminho in ESTATICOS:
            nome, tipo = ESTATICOS[caminho]
            return self._arquivo(PASTA_WEB / nome, tipo)
        if caminho == "/_perfis":
            return self._perfis()
        if caminho.startswith("/_status/"):
            return self._status(unquote(caminho[len("/_status/") :]), parse_qs(partes.query))
        if caminho == "/_historico":
            return self._hist_listar(parse_qs(partes.query))
        if caminho == "/_historico/exportar":
            return self._hist_exportar(parse_qs(partes.query))
        if caminho.startswith("/_proxy/"):
            return self._proxy()
        self._texto(404, "Não encontrado.")

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path == "/_historico":
            return self._hist_gravar()
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        if urlsplit(self.path).path == "/_historico":
            return self._hist_limpar()
        self._proxy()

    def _arquivo(self, caminho: Path, tipo: str) -> None:
        try:
            corpo = caminho.read_bytes()
        except OSError:
            return self._texto(500, f"Arquivo não encontrado: {caminho.name}")
        self._enviar(200, corpo, tipo)

    def _perfis(self) -> None:
        cfg = self.server.cfg
        self._json(
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
                "historico": self.server.historico is not None,
                "arquivoHistorico": (
                    str(self.server.historico.arquivo) if self.server.historico else ""
                ),
            }
        )

    def _status(self, nome: str, query: dict[str, list[str]]) -> None:
        perfil = self.server.cfg.perfis.get(nome)
        if perfil is None:
            return self._texto(404, "Perfil desconhecido.")
        forcar = query.get("forcar", [""])[0] == "1"
        self._json(self.server.status_de(nome, perfil, forcar))

    # --- histórico ------------------------------------------------------
    def _hist_listar(self, query: dict[str, list[str]]) -> None:
        h = self.server.historico
        if h is None:
            return self._json({"itens": [], "desligado": True})
        try:
            limite = max(1, min(2000, int(query.get("limite", ["300"])[0])))
        except ValueError:
            limite = 300
        self._json({"itens": h.recentes(limite)})

    def _hist_gravar(self) -> None:
        if not self._origem_ok():
            return self._texto(403, "Origem não permitida.")
        corpo = self._ler_corpo(MAX_ENTRADA)
        if corpo is None:
            return
        h = self.server.historico
        if h is None:
            return self._json({"ok": False, "desligado": True})
        try:
            dado = json.loads(corpo)
        except json.JSONDecodeError:
            return self._texto(400, "JSON inválido.")
        if not isinstance(dado, dict) or not {"method", "url", "status"} <= dado.keys():
            return self._texto(400, "Entrada inválida: faltam method, url ou status.")
        entrada = {k: dado[k] for k in CHAVES_HISTORICO if k in dado}
        entrada["salvoEm"] = int(time.time() * 1000)
        h.adicionar(entrada)
        self._json({"ok": True})

    def _hist_limpar(self) -> None:
        if not self._origem_ok():
            return self._texto(403, "Origem não permitida.")
        h = self.server.historico
        if h is not None:
            h.limpar()
        self._json({"ok": True})

    def _hist_exportar(self, query: dict[str, list[str]]) -> None:
        h = self.server.historico
        itens = h.todas() if h is not None else []
        formato = query.get("formato", ["json"])[0]
        carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
        if formato == "csv":
            corpo, tipo, ext = historico_csv(itens), "text/csv; charset=utf-8", "csv"
        else:
            corpo = json.dumps(itens, ensure_ascii=False, indent=2).encode("utf-8")
            tipo, ext = "application/json; charset=utf-8", "json"
        self._enviar(
            200,
            corpo,
            tipo,
            {"Content-Disposition": f'attachment; filename="docnuvem-historico-{carimbo}.{ext}"'},
        )

    # --- proxy para a API -----------------------------------------------
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
        params += [
            (k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True) if k != "instancia"
        ]
        url = perfil.baseUrl.rstrip("/") + path
        url_final = str(httpx.Request(metodo, url, params=params).url)

        corpo = self._ler_corpo(MAX_CORPO)
        if corpo is None:
            return
        headers = {"Authorization": f"Bearer {perfil.token}"}
        if self.headers.get("Content-Type"):
            headers["Content-Type"] = self.headers["Content-Type"]

        inicio = time.perf_counter()
        try:
            resp = self.server.cliente.request(
                metodo, url, params=params, content=corpo or None, headers=headers
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
        description="Interface web do Docnuvem API Tester (usa os perfis do config.json).",
    )
    ap.add_argument(
        "--porta",
        type=int,
        default=None,
        help="porta local (padrão: 8765; se estiver bloqueada, usa uma porta livre)",
    )
    ap.add_argument("--nao-abrir", action="store_true", help="não abre o navegador")
    ap.add_argument("--sem-historico", action="store_true", help="não grava o histórico em disco")
    ap.add_argument(
        "--dados",
        type=Path,
        default=None,
        help="pasta dos dados locais (padrão: ~/.docnuvem-tester ou DOCNUVEM_TESTER_DADOS)",
    )
    args = ap.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    historico = None
    if not args.sem_historico:
        historico = Historico((args.dados or dados_dir()) / "historico.jsonl")
    # O Windows reserva faixas de portas (WinError 10013). Sem --porta explícita,
    # tenta a padrão e cai para uma porta livre escolhida pelo sistema.
    tentativas = [args.porta] if args.porta is not None else [PORTA_PADRAO, 0]
    servidor: Servidor | None = None
    for porta in tentativas:
        try:
            servidor = Servidor(("127.0.0.1", porta), cfg, historico)
            break
        except OSError as exc:
            sufixo = "; tentando uma porta livre..." if porta != tentativas[-1] else ""
            print(f"Porta {porta or '(automática)'} indisponível ({exc}){sufixo}", file=sys.stderr)
    if servidor is None:
        sys.exit(1)

    url = f"http://127.0.0.1:{servidor.server_address[1]}/"
    print(f"Interface web em {url}  (Ctrl+C para sair)")
    print("Perfis: " + ", ".join(cfg.perfis) + ". Os tokens ficam só neste processo.")
    print(
        f"Histórico: {historico.arquivo}"
        if historico
        else "Histórico em disco desligado (--sem-historico)."
    )
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
