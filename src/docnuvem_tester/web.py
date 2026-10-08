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
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, unquote, urlsplit

import httpx

from docnuvem_tester.arquivos import gerar_pdf
from docnuvem_tester.comparar import TIPOS as TIPOS_COMPARACAO
from docnuvem_tester.comparar import comparar
from docnuvem_tester.config import (
    AppConfig,
    ConfigError,
    PerfilConfig,
    PerfilJaExiste,
    PerfilNaoEncontrado,
    config_path,
    definir_padrao,
    load_config,
    remover_perfil,
    salvar_perfil,
)
from docnuvem_tester.download import ErroDownload, GerenciadorDownload, validar_destino
from docnuvem_tester.importar_pasta import (
    ErroImportacao,
    GerenciadorImportacao,
    Registro,
    validar_origem,
)
from docnuvem_tester.importar_pasta import ensaio as ensaio_importacao
from docnuvem_tester.lote import ErroLote, GerenciadorLote, preparar
from docnuvem_tester.roteiro import rodar_fumaca
from docnuvem_tester.vigia import ErroVigia, GerenciadorVigia

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
MAX_DESCARTE = 1_000_000  # corpo recusado até este tamanho é lido e jogado fora
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
    "fav",
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
# Cabeçalho que a página manda depois que o usuário confirma uma chamada em perfil protegido.
CABECALHO_CONFIRMACAO = "X-Docnuvem-Confirmado"
HEADERS_RESPOSTA = {"content-type", "x-request-id", "date", "content-length", "location"}


def mascarar_token(token: str) -> str:
    if not token:
        return "Bearer ***"
    return f"Bearer ***...{token[-4:] if len(token) >= 4 else token}"


def pasta_downloads_padrao() -> Path:
    """Sugestão de destino para o download de pastas."""
    return Path.home() / "Downloads" / "Docnuvem"


def escolher_pasta(inicial: str = "") -> str | None:
    """Abre a janela do Windows para escolher uma pasta (no computador onde o servidor roda).

    Devolve None se o usuário cancelar. Levanta ErroDownload se a janela não puder abrir.
    """
    try:
        import tkinter
        from tkinter import filedialog

        raiz = tkinter.Tk()
    except Exception as exc:  # noqa: BLE001 - sem tkinter ou sem tela
        raise ErroDownload(
            f"A janela de escolha de pasta não abriu ({exc}). Digite o caminho."
        ) from exc
    try:
        raiz.withdraw()
        raiz.attributes("-topmost", True)
        pasta = filedialog.askdirectory(
            initialdir=inicial if inicial and Path(inicial).is_dir() else None,
            title="Escolha a pasta onde salvar os documentos",
            mustexist=False,
            parent=raiz,
        )
    finally:
        raiz.destroy()
    return str(Path(pasta)) if pasta else None


def abrir_pasta(caminho: Path) -> None:
    """Abre a pasta no Explorer (ou equivalente)."""
    if sys.platform == "win32":
        os.startfile(caminho)  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(caminho)])  # noqa: S603, S607
    else:
        subprocess.Popen(["xdg-open", str(caminho)])  # noqa: S603, S607


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
                self._podar()

    def _gravar(self, linhas: list[str]) -> None:
        """Reescreve o arquivo com estas linhas (o lock já deve estar tomado)."""
        self.arquivo.write_text("".join(linha + "\n" for linha in linhas), encoding="utf-8")
        self._n = len(linhas)

    def _podar(self) -> None:
        """Mantém as últimas MAX_HISTORICO entradas e todas as favoritas (lock já tomado)."""
        linhas = self._linhas()
        corte = max(0, len(linhas) - MAX_HISTORICO)
        self._gravar([ln for i, ln in enumerate(linhas) if i >= corte or _eh_favorita(ln)])

    def marcar_favorito(self, id_: str, fav: bool) -> bool:
        """Liga/desliga a estrela de uma entrada. Devolve False se o id não existe."""
        with self._lock:
            achou = False
            saida: list[str] = []
            for linha in self._linhas():
                try:
                    dado = json.loads(linha)
                except json.JSONDecodeError:
                    saida.append(linha)
                    continue
                if isinstance(dado, dict) and dado.get("id") == id_:
                    dado["fav"] = fav
                    linha = json.dumps(dado, ensure_ascii=False)
                    achou = True
                saida.append(linha)
            if achou:
                self._gravar(saida)
            return achou

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
        """Da mais recente para a mais antiga; as favoritas mais velhas vêm junto, no fim."""
        todas = self.todas()
        corte = max(0, len(todas) - limite)
        antigas = [e for e in todas[:corte] if e.get("fav") is True]
        return (antigas + todas[corte:])[::-1]

    def limpar(self, manter_favoritas: bool = True) -> None:
        with self._lock:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            self._gravar([ln for ln in self._linhas() if manter_favoritas and _eh_favorita(ln)])


def _eh_favorita(linha: str) -> bool:
    try:
        dado = json.loads(linha)
    except json.JSONDecodeError:
        return False
    return isinstance(dado, dict) and dado.get("fav") is True


def historico_csv(itens: list[dict[str, Any]]) -> bytes:
    """CSV (UTF-8 com BOM, abre direto no Excel) do histórico."""
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    w.writerow(["data_hora", "perfil", "metodo", "url", "status", "duracao_ms", "tela", "favorita"])
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
                "sim" if e.get("fav") else "",
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


def _retorno(resp: httpx.Response) -> str:
    """Mensagem curta da resposta (campo "retorno" quando houver)."""
    try:
        dado = resp.json()
    except ValueError:
        return resp.text[:160].strip()
    if isinstance(dado, dict):
        for chave in ("retorno", "mensagem", "message", "erro", "error"):
            if isinstance(dado.get(chave), str):
                return str(dado[chave])[:160]
    return ""


def _json_dict(resp: httpx.Response) -> dict[str, Any]:
    try:
        dado = resp.json()
    except ValueError:
        return {}
    return dado if isinstance(dado, dict) else {}


def _http(resp: httpx.Response) -> str:
    return f"HTTP {resp.status_code}. {_retorno(resp)}".strip()


def diagnosticar(cliente: httpx.Client, perfil: PerfilConfig) -> dict[str, Any]:
    """Confere a instância só com leituras e explica a causa provável de cada falha.

    Cada item tem nivel ok | aviso | erro | info | pulado. O resultado geral é o pior deles.
    """
    base = perfil.baseUrl.rstrip("/")
    auth = {"Authorization": f"Bearer {perfil.token}"}
    inst = {"instancia": perfil.instancia.lower()}
    itens: list[dict[str, Any]] = []

    def item(
        id_: str, titulo: str, nivel: str, detalhe: str, causa: str = "", **extra: Any
    ) -> None:
        itens.append(
            {"id": id_, "titulo": titulo, "nivel": nivel, "detalhe": detalhe, "causa": causa}
            | extra
        )

    def pular(motivo: str) -> None:
        for id_, titulo in (
            ("token", "Token aceito"),
            ("modelos", "Modelos de documento"),
            ("pastas", "Pastas"),
            ("escola", "Solicitações de escola"),
        ):
            if not any(i["id"] == id_ for i in itens):
                item(id_, titulo, "pulado", motivo)

    def ler(
        caminho: str, autenticado: bool, **params: Any
    ) -> tuple[httpx.Response | None, int, str]:
        t0 = time.perf_counter()
        try:
            r = cliente.get(
                base + caminho,
                params={**(inst if autenticado else {}), **params},
                headers=auth if autenticado else None,
                timeout=10,
            )
        except httpx.HTTPError as exc:
            return None, round((time.perf_counter() - t0) * 1000), str(exc)[:200]
        return r, round((time.perf_counter() - t0) * 1000), ""

    # 1. A API responde?
    r, ms, erro = ler("/v3/api-docs/swagger-config", False)
    if r is None or r.status_code >= 500:
        item(
            "api",
            "API acessível",
            "erro",
            erro or (_http(r) if r else "sem resposta"),
            "O baseUrl do perfil está errado, a API está fora do ar ou algo bloqueia o acesso "
            "(VPN, firewall, porta).",
            ms=ms,
        )
        pular("Não verificado: a API não respondeu.")
        return _resumo_diag(itens, perfil)
    item("api", "API acessível", "ok", f"Respondeu em {ms} ms (HTTP {r.status_code}).", ms=ms)

    # 2. O token vale para a instância?
    rm, ms, erro = ler("/api/modelos", True)
    if rm is None:
        item("token", "Token aceito", "erro", erro, "A conexão caiu durante a consulta.", ms=ms)
        pular("Não verificado: a consulta com o token falhou.")
        return _resumo_diag(itens, perfil)
    if rm.status_code in (401, 403):
        item(
            "token",
            "Token aceito",
            "erro",
            _http(rm),
            "O token é fixo por instância: ele está errado, foi revogado ou é de outra "
            f'instância. Confira o token e o campo "instancia" ({perfil.instancia.lower()}) '
            "no config.json.",
            ms=ms,
        )
        pular("Não verificado: o token não foi aceito.")
        return _resumo_diag(itens, perfil)
    if rm.status_code >= 400:
        item(
            "token",
            "Token aceito",
            "erro",
            _http(rm),
            "A API recusou a consulta. Veja a mensagem acima; um nome de instância "
            "inexistente costuma cair aqui.",
            ms=ms,
        )
        pular("Não verificado: a consulta com o token falhou.")
        return _resumo_diag(itens, perfil)
    item("token", "Token aceito", "ok", f"HTTP {rm.status_code} em {ms} ms.", ms=ms)

    # 3. Modelos cadastrados e geráveis por API
    modelos = [m for m in _json_dict(rm).get("modelos") or [] if isinstance(m, dict)]
    geraveis = [m for m in modelos if m.get("geravelPorApi")]
    # Uma linha por modelo, os geráveis por API primeiro (a página mostra como lista).
    lista = [
        {
            "codigo": str(m.get("codigo") or m.get("id") or ""),
            "nome": str(m.get("nome") or "(sem nome)"),
            "geravel": bool(m.get("geravelPorApi")),
        }
        for m in sorted(modelos, key=lambda m: not m.get("geravelPorApi"))
    ]
    if not modelos:
        item(
            "modelos",
            "Modelos de documento",
            "aviso",
            "Nenhum modelo cadastrado.",
            "Criar documento de modelo não vai funcionar: cadastre os modelos no painel da "
            "instância.",
            total=0,
            geraveis=0,
        )
    elif not geraveis:
        item(
            "modelos",
            "Modelos de documento",
            "aviso",
            f"{len(modelos)} modelo(s), nenhum gerável por API.",
            "Só modelos marcados como geráveis por API aceitam from-template. Peça a "
            "liberação na configuração do modelo.",
            total=len(modelos),
            geraveis=0,
            lista=lista,
        )
    else:
        item(
            "modelos",
            "Modelos de documento",
            "ok",
            f"{len(modelos)} modelo(s), {len(geraveis)} gerável(is) por API.",
            total=len(modelos),
            geraveis=len(geraveis),
            lista=lista,
        )

    # 4. Pastas
    rp, ms, erro = ler("/api/diretorios", True, pagina=0, tamanho=1)
    if rp is None or rp.status_code >= 400:
        item(
            "pastas",
            "Pastas",
            "erro",
            erro or (_http(rp) if rp else "sem resposta"),
            "O token foi aceito em /api/modelos, mas /api/diretorios o recusou: o problema "
            "está no lado da API (permissão ou filtro deste endpoint), não no token. "
            "Avise quem mantém a API."
            if rp is not None and rp.status_code in (401, 403)
            else "Não foi possível listar as pastas da instância.",
            ms=ms,
        )
    else:
        total = _json_dict(rp).get("total")
        if total == 0:
            item(
                "pastas",
                "Pastas",
                "aviso",
                "Nenhuma pasta encontrada.",
                "Importar arquivo cria a árvore de pastas, mas criar documento de modelo "
                "exige que a pasta já exista.",
                ms=ms,
            )
        else:
            n = f"{total} pasta(s)" if isinstance(total, int) else "Pastas listadas"
            item("pastas", "Pastas", "ok", f"{n} em {ms} ms.", ms=ms)

    # 5. Módulo de escola (leitura com uma matrícula fictícia)
    re_, ms, erro = ler(
        "/api/solicitacaoAluno/consultarStatus", True, codigoMatricula="diagnostico"
    )
    if re_ is None:
        item("escola", "Solicitações de escola", "erro", erro, "A conexão caiu.", ms=ms)
    elif re_.status_code in (401, 403):
        item(
            "escola",
            "Solicitações de escola",
            "aviso",
            _http(re_),
            "O token não tem acesso ao módulo de escola. É esperado se a instância não for "
            "uma escola.",
            ms=ms,
        )
    elif re_.status_code >= 500:
        item(
            "escola",
            "Solicitações de escola",
            "erro",
            _http(re_),
            "A API falhou ao consultar. Tente de novo; se persistir, é um erro do servidor.",
            ms=ms,
        )
    else:
        item(
            "escola",
            "Solicitações de escola",
            "info",
            f"O módulo respondeu (HTTP {re_.status_code}) para uma matrícula fictícia. "
            + _retorno(re_),
            "Só com leitura não dá para saber se os modelos de solicitação (tipo 1 e 2) estão "
            "configurados: isso só aparece ao solicitar de verdade.",
            ms=ms,
        )
    return _resumo_diag(itens, perfil)


def _resumo_diag(itens: list[dict[str, Any]], perfil: PerfilConfig) -> dict[str, Any]:
    niveis = {i["nivel"] for i in itens}
    geral = "erro" if "erro" in niveis else "aviso" if "aviso" in niveis else "ok"
    return {
        "verificadoEm": int(time.time() * 1000),
        "instancia": perfil.instancia.lower(),
        "baseUrl": perfil.baseUrl,
        "nivel": geral,
        "itens": itens,
    }


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
        arquivo_config: Path | None = None,
    ) -> None:
        super().__init__(endereco, Handler)
        self.cfg = cfg
        self.arquivo_config = arquivo_config  # None: a página não pode editar os perfis
        self._cfg_lock = threading.Lock()
        self.downloads = GerenciadorDownload()
        self.lotes = GerenciadorLote()
        self.vigias = GerenciadorVigia()
        registro = Registro(historico.arquivo.parent / "importados.jsonl") if historico else None
        self.importacoes = GerenciadorImportacao(registro)
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

    def editar_config(self, acao: Callable[[Path], AppConfig]) -> None:
        """Aplica uma edição ao config.json e passa a usar a configuração nova."""
        if self.arquivo_config is None:
            raise ConfigError("Este servidor foi iniciado sem arquivo de configuração.")
        with self._cfg_lock:
            self.cfg = acao(self.arquivo_config)
        with self._status_lock:
            self._status_cache.clear()

    def server_close(self) -> None:
        self.vigias.parar_todos()
        super().server_close()
        if self._cliente_proprio:
            self.cliente.close()


class Handler(BaseHTTPRequestHandler):
    server: Servidor  # type: ignore[assignment]
    server_version = "docnuvem-web"
    _corpo_lido = False

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
        self._descartar_corpo()
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def _descartar_corpo(self) -> None:
        """Consome o corpo que ficou sem ler antes de responder (recusas, 413).

        Sem isso o cliente ainda está enviando quando a resposta chega e o Windows pode
        derrubar a conexão (reset) em vez de entregar o erro. Corpos enormes não são lidos.
        """
        restante = int(self.headers.get("Content-Length") or 0)
        if self._corpo_lido or restante <= 0:
            return
        self._corpo_lido = True
        if restante > MAX_DESCARTE:
            return
        while restante > 0:
            pedaco = self.rfile.read(min(restante, 65536))
            if not pedaco:
                break
            restante -= len(pedaco)

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
        self._corpo_lido = True
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
        if caminho.startswith("/_importar/"):
            resto = unquote(caminho[len("/_importar/") :])
            if resto.endswith("/relatorio.csv"):
                return self._ip_relatorio(resto[: -len("/relatorio.csv")])
            return self._ip_estado(resto)
        if caminho == "/_vigia":
            return self._json({"vigias": [v.snapshot() for v in self.server.vigias.todos()]})
        if caminho == "/_arquivo-teste":
            return self._arquivo_teste(parse_qs(partes.query))
        if caminho.startswith("/_lote/"):
            resto = unquote(caminho[len("/_lote/") :])
            if resto.endswith("/relatorio.csv"):
                return self._lote_relatorio(resto[: -len("/relatorio.csv")])
            return self._lote_estado(resto)
        if caminho.startswith("/_baixar/"):
            return self._bx_estado(unquote(caminho[len("/_baixar/") :]))
        if caminho.startswith("/_diagnostico/"):
            return self._diagnostico(unquote(caminho[len("/_diagnostico/") :]))
        if caminho == "/_historico":
            return self._hist_listar(parse_qs(partes.query))
        if caminho == "/_historico/exportar":
            return self._hist_exportar(parse_qs(partes.query))
        if caminho.startswith("/_proxy/"):
            return self._proxy()
        self._texto(404, "Não encontrado.")

    def do_POST(self) -> None:  # noqa: N802
        caminho = urlsplit(self.path).path
        if caminho == "/_historico":
            return self._hist_gravar()
        if caminho == "/_historico/favorito":
            return self._hist_favorito()
        if caminho == "/_importar/ensaio":
            return self._ip_ensaio()
        if caminho == "/_importar/iniciar":
            return self._ip_iniciar()
        if caminho.startswith("/_importar/") and caminho.endswith("/cancelar"):
            return self._ip_cancelar(unquote(caminho[len("/_importar/") : -len("/cancelar")]))
        if caminho == "/_vigia/iniciar":
            return self._vigia_iniciar()
        if caminho in ("/_vigia/parar", "/_vigia/agora"):
            return self._vigia_acao(caminho.rsplit("/", 1)[1])
        if caminho == "/_comparar":
            return self._comparar()
        if caminho == "/_roteiro/fumaca":
            return self._fumaca()
        if caminho == "/_lote/validar":
            return self._lote_validar()
        if caminho == "/_lote/iniciar":
            return self._lote_iniciar()
        if caminho.startswith("/_lote/") and caminho.endswith("/cancelar"):
            return self._lote_cancelar(unquote(caminho[len("/_lote/") : -len("/cancelar")]))
        if caminho == "/_baixar/iniciar":
            return self._bx_iniciar()
        if caminho.startswith("/_baixar/") and caminho.endswith("/cancelar"):
            return self._bx_cancelar(unquote(caminho[len("/_baixar/") : -len("/cancelar")]))
        if caminho == "/_pasta/escolher":
            return self._pasta_escolher()
        if caminho == "/_pasta/abrir":
            return self._pasta_abrir()
        if caminho == "/_config/perfil":
            return self._cfg_salvar()
        if caminho == "/_config/padrao":
            return self._cfg_padrao()
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        partes = urlsplit(self.path)
        if partes.path == "/_historico":
            return self._hist_limpar(parse_qs(partes.query))
        if partes.path.startswith("/_config/perfil/"):
            return self._cfg_remover(unquote(partes.path[len("/_config/perfil/") :]))
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
                        "protegido": p.protecao,
                    }
                    for nome, p in cfg.perfis.items()
                ],
                "padrao": cfg.perfilPadrao,
                "historico": self.server.historico is not None,
                "arquivoHistorico": (
                    str(self.server.historico.arquivo) if self.server.historico else ""
                ),
                "pastaDownloads": str(pasta_downloads_padrao()),
                "editavel": self.server.arquivo_config is not None,
                "arquivoConfig": str(self.server.arquivo_config or ""),
            }
        )

    def _ip_campos(self, dado: dict[str, Any]) -> dict[str, Any] | None:
        """Lê e valida os campos comuns do ensaio e do envio; responde o erro e devolve None."""
        alvo = self._perfil_do_corpo(dado)
        if alvo is None:
            return None
        try:
            origem = validar_origem(str(dado.get("origem", "")))
        except ErroImportacao as exc:
            self._json({"ok": False, "erro": str(exc)}, 400)
            return None
        return {
            "nome": alvo[0],
            "perfil": alvo[1],
            "origem": origem,
            "subpastas": dado.get("subpastas") is True,
            "pasta_pai": str(dado.get("pastaPai") or ""),
            "pasta": str(dado.get("pasta") or ""),
            "tipo": str(dado.get("tipo") or ""),
            "pular_enviados": dado.get("pularEnviados") is not False,
        }

    def _ip_ensaio(self) -> None:
        dado = self._corpo_json()
        if dado is None:
            return
        c = self._ip_campos(dado)
        if c is None:
            return
        try:
            r = ensaio_importacao(
                c["origem"],
                subpastas=c["subpastas"],
                pasta_pai=c["pasta_pai"],
                pasta=c["pasta"],
                perfil=c["nome"],
                registro=self.server.importacoes.registro,
                pular_enviados=c["pular_enviados"],
            )
        except ErroImportacao as exc:
            return self._json({"ok": False, "erro": str(exc)}, 400)
        self._json({"ok": True, **r})

    def _ip_iniciar(self) -> None:
        dado = self._corpo_json()
        if dado is None:
            return
        c = self._ip_campos(dado)
        if c is None:
            return
        perfil: PerfilConfig = c["perfil"]
        if perfil.protecao == "bloquear":
            return self._json(
                {"ok": False, "erro": "Perfil protegido (só leitura): não dá para importar."}, 403
            )
        if dado.get("confirmarEnvio") is not True:
            return self._json(
                {"ok": False, "erro": "O envio de arquivos precisa de confirmação."}, 428
            )
        if perfil.protecao == "confirmar" and dado.get("confirmarProtecao") is not True:
            return self._json(
                {"ok": False, "erro": "Perfil protegido: confirme que quer importar em produção."},
                428,
            )
        try:
            intervalo = int(dado.get("intervaloMs", 300))
        except (TypeError, ValueError):
            return self._json({"ok": False, "erro": "Intervalo inválido."}, 400)
        try:
            imp = self.server.importacoes.iniciar(
                self.server.cliente,
                c["nome"],
                perfil,
                origem=c["origem"],
                subpastas=c["subpastas"],
                pasta_pai=c["pasta_pai"],
                pasta=c["pasta"],
                tipo=c["tipo"],
                pular_enviados=c["pular_enviados"],
                intervalo_ms=intervalo,
            )
        except ErroImportacao as exc:
            return self._json(
                {"ok": False, "erro": str(exc)}, 409 if "andamento" in str(exc) else 400
            )
        self._json({"ok": True, "id": imp.id})

    def _ip_estado(self, id_: str) -> None:
        imp = self.server.importacoes.obter(id_)
        if imp is None:
            return self._texto(404, "Importação desconhecida.")
        self._json(imp.snapshot())

    def _ip_cancelar(self, id_: str) -> None:
        if not self._origem_ok():
            return self._texto(403, "Origem não permitida.")
        imp = self.server.importacoes.obter(id_)
        if imp is None:
            return self._texto(404, "Importação desconhecida.")
        imp.cancelar.set()
        self._json({"ok": True})

    def _ip_relatorio(self, id_: str) -> None:
        imp = self.server.importacoes.obter(id_)
        if imp is None:
            return self._texto(404, "Importação desconhecida.")
        carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._enviar(
            200,
            imp.relatorio_csv(),
            "text/csv; charset=utf-8",
            {"Content-Disposition": f'attachment; filename="docnuvem-importacao-{carimbo}.csv"'},
        )

    def _vigia_iniciar(self) -> None:
        dado = self._corpo_json()
        if dado is None:
            return
        alvo = self._perfil_do_corpo(dado)
        if alvo is None:
            return
        nome, perfil = alvo
        bruto = dado.get("ids", [])
        pedacos = re.split(r"[\s,;]+", bruto) if isinstance(bruto, str) else list(bruto or [])
        try:
            ids = {int(str(p).strip()) for p in pedacos if str(p).strip()}
            intervalo = int(dado.get("intervaloMin", 10))
            alerta = int(dado.get("alertaHoras", 48))
            if any(i <= 0 for i in ids):
                raise ValueError
        except (TypeError, ValueError):
            return self._json(
                {"ok": False, "erro": "Intervalo, aviso e ids devem ser números."}, 400
            )
        try:
            self.server.vigias.iniciar(
                self.server.cliente,
                nome,
                perfil,
                intervalo_min=intervalo,
                alerta_h=alerta,
                ids=ids,
            )
        except ErroVigia as exc:
            return self._json({"ok": False, "erro": str(exc)}, 400)
        self._json({"ok": True})

    def _vigia_acao(self, acao: str) -> None:
        dado = self._corpo_json()
        if dado is None:
            return
        v = self.server.vigias.obter(str(dado.get("perfil", "")))
        if v is None:
            return self._json({"ok": False, "erro": "Não há vigia ativo para esse perfil."}, 404)
        if acao == "parar":
            v.parar()
        else:
            v.agora()
        self._json({"ok": True})

    def _arquivo_teste(self, query: dict[str, list[str]]) -> None:
        """PDF válido de teste, com o tamanho (bytes) e o número de páginas pedidos."""
        try:
            tamanho = int(query.get("tamanho", ["102400"])[0])
            paginas = int(query.get("paginas", ["1"])[0])
        except ValueError:
            return self._texto(400, "tamanho e paginas devem ser números.")
        self._enviar(200, gerar_pdf(tamanho, paginas), "application/pdf")

    # --- rotina de testes: comparar, lote para escolas, teste de fumaça -----
    def _perfil_do_corpo(
        self, dado: dict[str, Any], campo: str = "perfil"
    ) -> tuple[str, PerfilConfig] | None:
        nome = str(dado.get(campo, ""))
        perfil = self.server.cfg.perfis.get(nome)
        if perfil is None:
            self._json({"ok": False, "erro": f'Perfil desconhecido: "{nome}".'}, 404)
            return None
        return nome, perfil

    def _comparar(self) -> None:
        dado = self._corpo_json()
        if dado is None:
            return
        tipo = str(dado.get("tipo", ""))
        if tipo not in TIPOS_COMPARACAO:
            return self._json({"ok": False, "erro": "Tipo de comparação desconhecido."}, 400)
        if dado.get("a") == dado.get("b"):
            return self._json({"ok": False, "erro": "Escolha dois perfis diferentes."}, 400)
        a = self._perfil_do_corpo(dado, "a")
        b = self._perfil_do_corpo(dado, "b") if a else None
        if a is None or b is None:
            return
        self._json({"ok": True, **comparar(self.server.cliente, tipo, a, b)})

    def _fumaca(self) -> None:
        dado = self._corpo_json()
        if dado is None:
            return
        alvo = self._perfil_do_corpo(dado)
        if alvo is None:
            return
        nome, perfil = alvo
        if perfil.protecao == "bloquear":
            return self._json(
                {
                    "ok": False,
                    "erro": "Perfil protegido (só leitura): o teste de fumaça escreve nele.",
                },
                403,
            )
        if dado.get("confirmar") is not True:
            return self._json(
                {"ok": False, "erro": "O teste de fumaça precisa de confirmação."}, 428
            )
        self._json({"ok": True, **rodar_fumaca(self.server.cliente, perfil)})

    def _lote_validar(self) -> None:
        dado = self._corpo_json()
        if dado is None:
            return
        try:
            r = preparar(str(dado.get("csv", "")))
        except ErroLote as exc:
            return self._json({"ok": False, "erro": str(exc)}, 400)
        self._json(
            {
                "ok": True,
                "linhas": len(r["linhas"]),
                "validas": len(r["validas"]),
                "invalidas": r["invalidas"][:200],
                "nInvalidas": len(r["invalidas"]),
                "amostra": [
                    {
                        "codigoMatricula": v["codigoMatricula"],
                        "nome": v["nome"],
                        "email": v["email"],
                    }
                    for v in r["validas"][:5]
                ],
            }
        )

    def _lote_iniciar(self) -> None:
        dado = self._corpo_json()
        if dado is None:
            return
        alvo = self._perfil_do_corpo(dado)
        if alvo is None:
            return
        nome, perfil = alvo
        modo = str(dado.get("modo", ""))
        if modo not in ("enviar", "consultar"):
            return self._json({"ok": False, "erro": "Modo deve ser enviar ou consultar."}, 400)
        if modo == "enviar":
            if perfil.protecao == "bloquear":
                return self._json(
                    {
                        "ok": False,
                        "erro": "Perfil protegido (só leitura): não dá para enviar solicitações.",
                    },
                    403,
                )
            if dado.get("confirmarEnvio") is not True:
                return self._json(
                    {"ok": False, "erro": "O envio de e-mails precisa de confirmação."}, 428
                )
            if perfil.protecao == "confirmar" and dado.get("confirmarProtecao") is not True:
                return self._json(
                    {
                        "ok": False,
                        "erro": "Perfil protegido: confirme que quer enviar em produção.",
                    },
                    428,
                )
        try:
            intervalo = int(dado.get("intervaloMs", 500))
        except (TypeError, ValueError):
            return self._json({"ok": False, "erro": "Intervalo inválido."}, 400)
        try:
            lote = self.server.lotes.iniciar(
                self.server.cliente,
                nome,
                perfil,
                texto=str(dado.get("csv", "")),
                modo=modo,
                intervalo_ms=intervalo,
            )
        except ErroLote as exc:
            return self._json(
                {"ok": False, "erro": str(exc)}, 409 if "andamento" in str(exc) else 400
            )
        self._json({"ok": True, "id": lote.id})

    def _lote_estado(self, id_: str) -> None:
        lote = self.server.lotes.obter(id_)
        if lote is None:
            return self._texto(404, "Lote desconhecido.")
        self._json(lote.snapshot())

    def _lote_cancelar(self, id_: str) -> None:
        if not self._origem_ok():
            return self._texto(403, "Origem não permitida.")
        lote = self.server.lotes.obter(id_)
        if lote is None:
            return self._texto(404, "Lote desconhecido.")
        lote.cancelar.set()
        self._json({"ok": True})

    def _lote_relatorio(self, id_: str) -> None:
        lote = self.server.lotes.obter(id_)
        if lote is None:
            return self._texto(404, "Lote desconhecido.")
        carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._enviar(
            200,
            lote.relatorio_csv(),
            "text/csv; charset=utf-8",
            {"Content-Disposition": f'attachment; filename="docnuvem-lote-{carimbo}.csv"'},
        )

    # --- download de pasta ------------------------------------------------
    def _bx_iniciar(self) -> None:
        dado = self._corpo_json()
        if dado is None:
            return
        nome = str(dado.get("perfil", ""))
        perfil = self.server.cfg.perfis.get(nome)
        if perfil is None:
            return self._json({"ok": False, "erro": "Perfil desconhecido."}, 404)
        try:
            diretorio_id = int(str(dado.get("diretorioId", "")).strip())
            if diretorio_id < 0:
                raise ValueError
        except ValueError:
            return self._json(
                {"ok": False, "erro": "Informe o diretorioId (um número; 0 é a raiz)."}, 400
            )
        conflito = str(dado.get("conflito", "renomear"))
        if conflito not in ("renomear", "pular"):
            return self._json({"ok": False, "erro": "Conflito deve ser renomear ou pular."}, 400)
        ensaio = dado.get("ensaio") is True
        try:
            texto = str(dado.get("destino", ""))
            destino = Path(texto.strip() or ".") if ensaio else validar_destino(texto)
            t = self.server.downloads.iniciar(
                self.server.cliente,
                nome,
                perfil,
                diretorio_id=diretorio_id,
                destino=destino,
                subpastas=dado.get("subpastas") is True,
                estrutura=dado.get("estrutura") is True,
                conflito=conflito,
                ensaio=ensaio,
                status=str(dado.get("status") or ""),
                data_inicio=str(dado.get("dataInicio") or ""),
                data_fim=str(dado.get("dataFim") or ""),
            )
        except ErroDownload as exc:
            return self._json(
                {"ok": False, "erro": str(exc)}, 409 if "andamento" in str(exc) else 400
            )
        self._json({"ok": True, "id": t.id})

    def _bx_estado(self, id_: str) -> None:
        t = self.server.downloads.obter(id_)
        if t is None:
            return self._texto(404, "Download desconhecido.")
        self._json(t.snapshot())

    def _bx_cancelar(self, id_: str) -> None:
        if not self._origem_ok():
            return self._texto(403, "Origem não permitida.")
        t = self.server.downloads.obter(id_)
        if t is None:
            return self._texto(404, "Download desconhecido.")
        t.cancelar.set()
        self._json({"ok": True})

    def _pasta_escolher(self) -> None:
        dado = self._corpo_json()
        if dado is None:
            return
        try:
            pasta = escolher_pasta(str(dado.get("inicial", "")))
        except ErroDownload as exc:
            return self._json({"ok": False, "erro": str(exc)}, 501)
        self._json({"ok": True, "caminho": pasta or "", "cancelado": pasta is None})

    def _pasta_abrir(self) -> None:
        """Só abre a pasta de destino de um download feito por este servidor."""
        dado = self._corpo_json()
        if dado is None:
            return
        t = self.server.downloads.obter(str(dado.get("id", "")))
        if t is None or t.ensaio or not t.destino.is_dir():
            return self._json({"ok": False, "erro": "Pasta não encontrada."}, 404)
        try:
            abrir_pasta(t.destino)
        except OSError as exc:
            return self._json({"ok": False, "erro": f"Não consegui abrir a pasta: {exc}"}, 500)
        self._json({"ok": True})

    # --- edição dos perfis (grava no config.json, que fica fora do git) ----
    def _cfg_aplicar(self, acao: Callable[[Path], AppConfig]) -> None:
        try:
            self.server.editar_config(acao)
        except PerfilNaoEncontrado as exc:
            return self._json({"ok": False, "erro": str(exc)}, 404)
        except PerfilJaExiste as exc:
            return self._json({"ok": False, "erro": str(exc)}, 409)
        except ConfigError as exc:
            return self._json({"ok": False, "erro": str(exc)}, 400)
        except OSError as exc:
            return self._json({"ok": False, "erro": f"Não foi possível gravar: {exc}"}, 500)
        self._json({"ok": True})

    def _corpo_json(self) -> dict[str, Any] | None:
        if not self._origem_ok():
            self._texto(403, "Origem não permitida.")
            return None
        corpo = self._ler_corpo(MAX_ENTRADA)
        if corpo is None:
            return None
        try:
            dado = json.loads(corpo)
        except json.JSONDecodeError:
            self._texto(400, "JSON inválido.")
            return None
        if not isinstance(dado, dict):
            self._texto(400, "Esperava um objeto JSON.")
            return None
        return dado

    def _cfg_salvar(self) -> None:
        dado = self._corpo_json()
        if dado is None:
            return
        campos = ("nome", "instancia", "baseUrl", "token", "protecao")
        if not all(isinstance(dado.get(c, ""), str) for c in (*campos, "original")):
            return self._json({"ok": False, "erro": "Campos devem ser texto."}, 400)
        original = dado.get("original") or None
        nome, instancia, base, token, protecao = (str(dado.get(c, "")) for c in campos)
        self._cfg_aplicar(
            lambda arq: salvar_perfil(
                arq,
                original=original,
                nome=nome,
                instancia=instancia,
                baseUrl=base,
                token=token,
                protecao=protecao,
            )
        )

    def _cfg_padrao(self) -> None:
        dado = self._corpo_json()
        if dado is None:
            return
        nome = str(dado.get("nome", ""))
        self._cfg_aplicar(lambda arq: definir_padrao(arq, nome))

    def _cfg_remover(self, nome: str) -> None:
        if not self._origem_ok():
            return self._texto(403, "Origem não permitida.")
        self._cfg_aplicar(lambda arq: remover_perfil(arq, nome))

    def _status(self, nome: str, query: dict[str, list[str]]) -> None:
        perfil = self.server.cfg.perfis.get(nome)
        if perfil is None:
            return self._texto(404, "Perfil desconhecido.")
        forcar = query.get("forcar", [""])[0] == "1"
        self._json(self.server.status_de(nome, perfil, forcar))

    def _diagnostico(self, nome: str) -> None:
        perfil = self.server.cfg.perfis.get(nome)
        if perfil is None:
            return self._texto(404, "Perfil desconhecido.")
        self._json(diagnosticar(self.server.cliente, perfil))

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

    def _hist_favorito(self) -> None:
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
        if not isinstance(dado, dict) or not isinstance(dado.get("id"), str):
            return self._texto(400, "Informe o id da entrada.")
        self._json({"ok": h.marcar_favorito(dado["id"], dado.get("fav") is True)})

    def _hist_limpar(self, query: dict[str, list[str]]) -> None:
        if not self._origem_ok():
            return self._texto(403, "Origem não permitida.")
        h = self.server.historico
        if h is not None:
            h.limpar(manter_favoritas=query.get("tudo", [""])[0] != "1")
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

    def _recusa_protecao(self, perfil: PerfilConfig, url_final: str) -> None:
        """Perfil protegido: a chamada que altera dados não chega à API."""
        if perfil.protecao == "bloquear":
            msg = (
                'Perfil protegido: este perfil só aceita leituras (protegido = "bloquear" '
                "no config.json). A chamada não foi enviada."
            )
        else:
            msg = (
                "Perfil protegido: esta chamada altera dados e precisa de confirmação. "
                "A chamada não foi enviada."
            )
        self._json(
            {
                "status": 403,
                "ms": 0,
                "url": url_final,
                "headers": {},
                "body": json.dumps({"retorno": msg}, ensure_ascii=False),
                "protecao": perfil.protecao,
            }
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
        if metodo != "GET" and perfil.protecao:
            confirmou = self.headers.get(CABECALHO_CONFIRMACAO, "").lower() == "sim"
            if perfil.protecao == "bloquear" or not confirmou:
                return self._recusa_protecao(perfil, url_final)
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
            servidor = Servidor(("127.0.0.1", porta), cfg, historico, arquivo_config=config_path())
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
