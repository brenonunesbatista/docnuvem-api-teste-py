"""Baixar todos os documentos de uma pasta do Docnuvem para uma pasta do computador.

O servidor local faz o trabalho inteiro (a página só acompanha o progresso): lista os
documentos da pasta (e das subpastas), pede o link temporário de cada um e grava o arquivo.
O link temporário vem da própria API e é baixado SEM o token do perfil.
"""

from __future__ import annotations

import csv
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from docnuvem_tester.config import PerfilConfig

TAMANHO_PAGINA = 200  # máximo aceito pela API
MAX_PAGINAS = 500  # trava de segurança contra laço infinito (100 mil documentos)
MAX_FALHAS_EXIBIDAS = 50
TIMEOUT_ARQUIVO = httpx.Timeout(120.0, connect=15.0)

_INVALIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
               *(f"LPT{i}" for i in range(1, 10))}  # fmt: skip


class ErroDownload(Exception):
    """Pedido de download inválido (a mensagem pode ser mostrada ao usuário)."""


def nome_seguro(nome: str, padrao: str) -> str:
    """Nome de arquivo ou pasta válido no Windows e que nunca sobe de diretório."""
    limpo = _INVALIDOS.sub("_", str(nome)).strip().rstrip(". ")
    if not limpo:
        limpo = padrao
    if limpo.split(".")[0].upper() in _RESERVADOS:
        limpo = "_" + limpo
    if len(limpo) > 150:
        raiz, ext = os.path.splitext(limpo)
        limpo = raiz[: max(1, 150 - len(ext))] + ext
    return limpo


def validar_destino(texto: str) -> Path:
    """Confere que o destino é um caminho absoluto onde dá para gravar (cria se preciso)."""
    texto = (texto or "").strip()
    if not texto:
        raise ErroDownload("Escolha a pasta onde os arquivos serão salvos.")
    caminho = Path(texto).expanduser()
    if not caminho.is_absolute():
        raise ErroDownload(
            "Use o caminho completo da pasta (por exemplo, C:\\Downloads\\Docnuvem)."
        )
    try:
        caminho.mkdir(parents=True, exist_ok=True)
        teste = caminho / f".teste-{uuid.uuid4().hex[:8]}"
        teste.write_bytes(b"")
        teste.unlink()
    except OSError as exc:
        raise ErroDownload(f"Não consigo gravar em {caminho}: {exc.strerror or exc}") from exc
    return caminho


@dataclass
class Trabalho:
    id: str
    perfil: str
    diretorio_id: int
    destino: Path
    subpastas: bool
    estrutura: bool
    conflito: str  # "renomear" | "pular"
    ensaio: bool
    estado: str = "listando"  # listando | baixando | concluido | cancelado | erro
    total: int = 0
    baixados: int = 0
    pulados: int = 0
    bytes: int = 0
    atual: str = ""
    erro: str = ""
    aviso: str = ""
    relatorio: str = ""
    pastas: int = 0
    amostra: list[str] = field(default_factory=list)
    falhas: list[dict[str, Any]] = field(default_factory=list)
    iniciado_em: float = field(default_factory=time.time)
    terminou_em: float | None = None
    cancelar: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    linhas: list[list[Any]] = field(default_factory=list)

    @property
    def ativo(self) -> bool:
        return self.estado in ("listando", "baixando")

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            feitos = self.baixados + self.pulados + len(self.falhas)
            return {
                "id": self.id,
                "perfil": self.perfil,
                "diretorioId": self.diretorio_id,
                "destino": str(self.destino),
                "ensaio": self.ensaio,
                "estado": self.estado,
                "total": self.total,
                "feitos": feitos,
                "baixados": self.baixados,
                "pulados": self.pulados,
                "nFalhas": len(self.falhas),
                "falhas": self.falhas[-MAX_FALHAS_EXIBIDAS:],
                "bytes": self.bytes,
                "atual": self.atual,
                "erro": self.erro,
                "aviso": self.aviso,
                "relatorio": self.relatorio,
                "pastas": self.pastas,
                "amostra": list(self.amostra),
                "iniciadoEm": int(self.iniciado_em * 1000),
                "terminouEm": int(self.terminou_em * 1000) if self.terminou_em else None,
            }


class GerenciadorDownload:
    """Guarda os trabalhos em memória; só um roda por vez."""

    def __init__(self) -> None:
        self._trabalhos: dict[str, Trabalho] = {}
        self._lock = threading.Lock()

    def obter(self, id_: str) -> Trabalho | None:
        with self._lock:
            return self._trabalhos.get(id_)

    def iniciar(
        self,
        cliente: httpx.Client,
        perfil_nome: str,
        perfil: PerfilConfig,
        *,
        diretorio_id: int,
        destino: Path,
        subpastas: bool,
        estrutura: bool,
        conflito: str,
        ensaio: bool,
    ) -> Trabalho:
        with self._lock:
            if any(t.ativo for t in self._trabalhos.values()):
                raise ErroDownload("Já existe um download em andamento. Aguarde ou cancele.")
            t = Trabalho(
                id=uuid.uuid4().hex[:12],
                perfil=perfil_nome,
                diretorio_id=diretorio_id,
                destino=destino,
                subpastas=subpastas,
                estrutura=estrutura,
                conflito=conflito,
                ensaio=ensaio,
            )
            self._trabalhos[t.id] = t
        threading.Thread(target=executar, args=(t, cliente, perfil), daemon=True).start()
        return t


# --------------------------------------------------------------------------
# Execução
# --------------------------------------------------------------------------


def _mensagem(resp: httpx.Response) -> str:
    try:
        dado = resp.json()
    except ValueError:
        return resp.text[:120].strip()
    if isinstance(dado, dict):
        for chave in ("retorno", "mensagem", "message", "erro", "error"):
            if isinstance(dado.get(chave), str):
                return str(dado[chave])[:120]
    return ""


def _pagina(
    cliente: httpx.Client, base: str, caminho: str, auth: dict[str, str], params: dict[str, Any]
) -> dict[str, Any]:
    r = cliente.get(base + caminho, params=params, headers=auth, timeout=30)
    if r.status_code != 200:
        msg = _mensagem(r)
        raise ErroDownload(f"A API respondeu HTTP {r.status_code} em {caminho}. {msg}".strip())
    try:
        dado = r.json()
    except ValueError as exc:
        raise ErroDownload(f"Resposta inesperada da API em {caminho}.") from exc
    return dado if isinstance(dado, dict) else {}


def listar_documentos(
    t: Trabalho, cliente: httpx.Client, base: str, auth: dict[str, str], instancia: str
) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for pagina in range(MAX_PAGINAS):
        if t.cancelar.is_set():
            break
        dado = _pagina(
            cliente,
            base,
            "/api/documentos",
            auth,
            {
                "instancia": instancia,
                "diretorioId": t.diretorio_id,
                "incluirSubpastas": "true" if t.subpastas else "false",
                "pagina": pagina,
                "tamanho": TAMANHO_PAGINA,
            },
        )
        lote = [d for d in dado.get("documentos") or [] if isinstance(d, dict)]
        docs.extend(lote)
        with t.lock:
            total = dado.get("total")
            t.total = total if isinstance(total, int) and total >= len(docs) else len(docs)
        if not dado.get("temMais") or not lote:
            break
    return docs


def mapa_pastas(
    cliente: httpx.Client, base: str, auth: dict[str, str], instancia: str
) -> dict[int, str]:
    """diretorioId -> caminho completo, para refazer a árvore de pastas no computador."""
    mapa: dict[int, str] = {}
    for pagina in range(MAX_PAGINAS):
        dado = _pagina(
            cliente,
            base,
            "/api/diretorios",
            auth,
            {"instancia": instancia, "pagina": pagina, "tamanho": TAMANHO_PAGINA},
        )
        lote = [d for d in dado.get("diretorios") or [] if isinstance(d, dict)]
        for d in lote:
            if isinstance(d.get("diretorioId"), int) and isinstance(d.get("caminho"), str):
                mapa[d["diretorioId"]] = d["caminho"]
        if not dado.get("temMais") or not lote:
            break
    return mapa


def _subpasta_relativa(caminho: str | None, raiz: str | None) -> Path:
    """Caminho da pasta do documento em relação à pasta escolhida (vazio = a própria)."""
    if not caminho or raiz is None:
        return Path()
    if caminho == raiz:
        return Path()
    prefixo = raiz.rstrip("/") + "/"
    if not caminho.startswith(prefixo):
        return Path()
    partes = [p for p in caminho[len(prefixo) :].split("/") if p]
    return Path(*[nome_seguro(p, "pasta") for p in partes])


def _raiz_comum(caminhos: Any) -> str:
    """Caminho da raiz (diretorioId 0): o primeiro nível comum, ex.: /Meus documentos."""
    primeiros = {c.split("/")[1] for c in caminhos if c.startswith("/") and c.count("/") >= 1}
    return "/" + primeiros.pop() if len(primeiros) == 1 else ""


def _destino_do_arquivo(pasta: Path, nome: str, doc_id: Any, conflito: str) -> Path | None:
    """Onde gravar; None = pular (já existe e a regra é pular)."""
    alvo = pasta / nome
    if not alvo.exists():
        return alvo
    if conflito == "pular":
        return None
    raiz, ext = os.path.splitext(nome)
    for n in range(1, 1000):
        sufixo = f" ({doc_id})" if n == 1 else f" ({doc_id}-{n})"
        alvo = pasta / f"{raiz}{sufixo}{ext}"
        if not alvo.exists():
            return alvo
    return None


def _baixar_arquivo(cliente: httpx.Client, url: str, alvo: Path) -> int:
    """Baixa para um .part e troca de nome no fim. Sem o token do perfil (link temporário)."""
    parcial = alvo.with_name(alvo.name + ".part")
    tamanho = 0
    try:
        with cliente.stream("GET", url, timeout=TIMEOUT_ARQUIVO, follow_redirects=True) as resp:
            if resp.status_code != 200:
                raise ErroDownload(f"o link de download respondeu HTTP {resp.status_code}")
            with parcial.open("wb") as f:
                for pedaco in resp.iter_bytes(65536):
                    f.write(pedaco)
                    tamanho += len(pedaco)
        os.replace(parcial, alvo)
    finally:
        if parcial.exists():
            parcial.unlink(missing_ok=True)
    return tamanho


def _link(
    cliente: httpx.Client, base: str, auth: dict[str, str], instancia: str, doc_id: Any
) -> str:
    r = cliente.get(
        f"{base}/api/documento/{doc_id}/download",
        params={"instancia": instancia},
        headers=auth,
        timeout=30,
    )
    if r.status_code != 200:
        raise ErroDownload(f"HTTP {r.status_code} ao gerar o link. {_mensagem(r)}".strip())
    try:
        url = r.json().get("urlDownload")
    except (ValueError, AttributeError):
        url = None
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        raise ErroDownload("a API não devolveu um link de download válido")
    return url


def _falha(t: Trabalho, doc: dict[str, Any], erro: str) -> None:
    with t.lock:
        t.falhas.append(
            {"documentoId": doc.get("documentoId"), "nome": doc.get("nomeArquivo"), "erro": erro}
        )
        t.linhas.append([doc.get("documentoId"), doc.get("nomeArquivo"), "", "falhou", erro])


def executar(t: Trabalho, cliente: httpx.Client, perfil: PerfilConfig) -> None:
    base = perfil.baseUrl.rstrip("/")
    auth = {"Authorization": f"Bearer {perfil.token}"}
    instancia = perfil.instancia.lower()
    try:
        docs = listar_documentos(t, cliente, base, auth, instancia)
        with t.lock:
            t.total = len(docs)
            t.pastas = len({d.get("diretorioId") for d in docs})
            t.amostra = [str(d.get("nomeArquivo")) for d in docs[:8]]
        if t.cancelar.is_set():
            return _terminar(t, "cancelado")
        if t.ensaio or not docs:
            return _terminar(t, "concluido")

        nomes_pastas: dict[int, str] = {}
        if t.estrutura and t.subpastas:
            try:
                nomes_pastas = mapa_pastas(cliente, base, auth, instancia)
            except (ErroDownload, httpx.HTTPError) as exc:
                with t.lock:
                    t.aviso = (
                        "Não consegui ler a árvore de pastas ("
                        f"{exc}); os arquivos foram salvos todos na mesma pasta."
                    )
        raiz = nomes_pastas.get(t.diretorio_id)
        if raiz is None and t.diretorio_id == 0 and nomes_pastas:
            raiz = _raiz_comum(nomes_pastas.values())  # 0 = raiz de "Meus documentos"
        with t.lock:
            t.estado = "baixando"

        for doc in docs:
            if t.cancelar.is_set():
                return _terminar(t, "cancelado")
            _baixar_um(t, cliente, base, auth, instancia, doc, nomes_pastas, raiz)
        _terminar(t, "concluido")
    except ErroDownload as exc:
        with t.lock:
            t.erro = str(exc)
        _terminar(t, "erro")
    except httpx.HTTPError as exc:
        with t.lock:
            t.erro = f"Falha de conexão com a API: {exc}"
        _terminar(t, "erro")
    except Exception as exc:  # noqa: BLE001 - o trabalho nunca pode morrer calado
        with t.lock:
            t.erro = f"Erro inesperado: {exc}"
        _terminar(t, "erro")


def _baixar_um(
    t: Trabalho,
    cliente: httpx.Client,
    base: str,
    auth: dict[str, str],
    instancia: str,
    doc: dict[str, Any],
    nomes_pastas: dict[int, str],
    raiz: str | None,
) -> None:
    doc_id = doc.get("documentoId")
    nome = nome_seguro(str(doc.get("nomeArquivo") or ""), f"documento-{doc_id}")
    with t.lock:
        t.atual = nome
    try:
        sub = _subpasta_relativa(nomes_pastas.get(doc.get("diretorioId", -1)), raiz)
        pasta = t.destino / sub
        pasta.mkdir(parents=True, exist_ok=True)
        if not pasta.resolve().is_relative_to(t.destino.resolve()):
            raise ErroDownload("caminho fora da pasta de destino")
        alvo = _destino_do_arquivo(pasta, nome, doc_id, t.conflito)
        if alvo is None:
            with t.lock:
                t.pulados += 1
                t.linhas.append([doc_id, doc.get("nomeArquivo"), str(pasta / nome), "pulado", ""])
            return
        tentativa = 0
        while True:
            try:
                url = _link(cliente, base, auth, instancia, doc_id)
                tamanho = _baixar_arquivo(cliente, url, alvo)
                break
            except httpx.HTTPError as exc:
                tentativa += 1
                if tentativa >= 2:
                    raise ErroDownload(f"falha de conexão: {exc}") from exc
        with t.lock:
            t.baixados += 1
            t.bytes += tamanho
            t.linhas.append([doc_id, doc.get("nomeArquivo"), str(alvo), "baixado", ""])
    except ErroDownload as exc:
        _falha(t, doc, str(exc))
    except OSError as exc:
        _falha(t, doc, f"erro ao gravar: {exc.strerror or exc}")


def _terminar(t: Trabalho, estado: str) -> None:
    if not t.ensaio and t.linhas:
        arquivo = t.destino / f"relatorio-download-{time.strftime('%Y%m%d-%H%M%S')}.csv"
        try:
            with arquivo.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["documentoId", "nome", "arquivo_local", "situacao", "erro"])
                w.writerows(t.linhas)
            t.relatorio = str(arquivo)
        except OSError:
            pass
    with t.lock:
        t.estado = estado
        t.atual = ""
        t.terminou_em = time.time()
