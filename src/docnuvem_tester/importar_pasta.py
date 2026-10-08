"""Importar uma pasta do computador para o Docnuvem, refazendo a árvore de subpastas.

Fluxo: ver o que será enviado (sem rede) -> confirmar -> enviar arquivo por arquivo, com
intervalo entre as chamadas. Cada arquivo enviado fica anotado no computador, para um segundo
envio da mesma pasta pular o que já foi (e para nunca duplicar documentos sem querer). Uma
chamada que falha nunca é repetida: poderia ter chegado e duplicar o documento.
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import os
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.download import _mensagem

# As mesmas que a tela de importar aceita.
EXTENSOES = (
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "png", "jpg", "jpeg", "txt", "csv",
    "xml", "zip",
)  # fmt: skip
MAX_ARQUIVOS = 20_000
MAX_BYTES_ARQUIVO = 50 * 1024 * 1024
MAX_FALHAS_SEGUIDAS = 5
MAX_FALHAS_EXIBIDAS = 50
INTERVALO_MAX_MS = 10_000
PAI_PADRAO = "/Meus documentos"  # raiz usada ao refazer subpastas sem "pasta pai" informada
_NOMES_IGNORADOS = {"thumbs.db", "desktop.ini", ".ds_store"}
_PASTAS_IGNORADAS = {"$recycle.bin", "system volume information"}


class ErroImportacao(Exception):
    """Pedido inválido (a mensagem pode ser mostrada ao usuário)."""


@dataclass(frozen=True)
class Arquivo:
    caminho: Path
    partes: tuple[str, ...]  # subpastas em relação à pasta escolhida
    nome: str
    tamanho: int
    mtime_ns: int


def validar_origem(texto: str) -> Path:
    texto = (texto or "").strip()
    if not texto:
        raise ErroImportacao("Escolha a pasta do computador que será enviada.")
    caminho = Path(texto).expanduser()
    if not caminho.is_absolute():
        raise ErroImportacao(
            "Use o caminho completo da pasta (por exemplo, C:\\Documentos\\Cliente)."
        )
    if not caminho.is_dir():
        raise ErroImportacao(f"A pasta não existe: {caminho}")
    return caminho


def varrer(origem: Path, subpastas: bool) -> tuple[list[Arquivo], list[dict[str, str]]]:
    """Lista os arquivos enviáveis e os ignorados (com o motivo). Ordem estável."""
    ok: list[Arquivo] = []
    ignorados: list[dict[str, str]] = []

    def ignorar(caminho: Path, motivo: str) -> None:
        ignorados.append({"arquivo": str(caminho.relative_to(origem)), "motivo": motivo})

    for raiz, pastas, nomes in os.walk(origem, followlinks=False):
        pastas[:] = sorted(
            p for p in pastas if not p.startswith(".") and p.lower() not in _PASTAS_IGNORADAS
        )
        if not subpastas:
            pastas.clear()
        atual = Path(raiz)
        partes = atual.relative_to(origem).parts
        for nome in sorted(nomes):
            caminho = atual / nome
            if nome.lower() in _NOMES_IGNORADOS or nome.startswith(("~$", ".")):
                continue  # lixo do sistema e temporários do Office: nem entram na conta
            ext = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
            if ext not in EXTENSOES:
                ignorar(caminho, f"extensão não aceita (.{ext})" if ext else "sem extensão")
                continue
            try:
                info = caminho.stat()
            except OSError as exc:
                ignorar(caminho, f"não consegui ler: {exc.strerror or exc}")
                continue
            if info.st_size == 0:
                ignorar(caminho, "arquivo vazio")
            elif info.st_size > MAX_BYTES_ARQUIVO:
                ignorar(caminho, f"maior que {MAX_BYTES_ARQUIVO // (1024 * 1024)} MB")
            elif len(ok) >= MAX_ARQUIVOS:
                raise ErroImportacao(f"A pasta tem mais de {MAX_ARQUIVOS} arquivos enviáveis.")
            else:
                ok.append(Arquivo(caminho, tuple(partes), nome, info.st_size, info.st_mtime_ns))
    return ok, ignorados


def destino_da_api(pasta_pai: str, pasta: str, partes: tuple[str, ...]) -> tuple[str, str]:
    """(nomePastaPai, nomePasta) que a API recebe para um arquivo dentro de `partes`.

    Na raiz da pasta escolhida vale o que o usuário digitou. Em subpasta, a última parte é
    a `nomePasta` e todo o resto vira a `nomePastaPai`.
    """
    pasta_pai, pasta = pasta_pai.strip(), pasta.strip().strip("/")
    if not partes:
        return pasta_pai, pasta
    base = pasta_pai or PAI_PADRAO
    caminho = "/".join([base.rstrip("/"), pasta, *partes[:-1]])
    return caminho, partes[-1]


class Registro:
    """Anotação em disco do que já foi enviado (JSON Lines), por perfil e destino."""

    def __init__(self, arquivo: Path) -> None:
        self.arquivo = arquivo
        self._lock = threading.Lock()
        self._enviados: dict[str, dict[str, Any]] | None = None

    @staticmethod
    def chave(perfil: str, a: Arquivo, pai: str, pasta: str) -> str:
        return f"{perfil}|{pai}|{pasta}|{a.caminho}|{a.tamanho}|{a.mtime_ns}"

    def _carregar(self) -> dict[str, dict[str, Any]]:
        if self._enviados is None:
            self._enviados = {}
            try:
                texto = self.arquivo.read_text(encoding="utf-8")
            except OSError:
                texto = ""
            for linha in texto.splitlines():
                try:
                    dado = json.loads(linha)
                except json.JSONDecodeError:
                    continue
                if isinstance(dado, dict) and isinstance(dado.get("k"), str):
                    self._enviados[dado["k"]] = dado
        return self._enviados

    def ja(self, chave: str) -> dict[str, Any] | None:
        with self._lock:
            return self._carregar().get(chave)

    def registrar(self, chave: str, documento_id: Any) -> None:
        item = {"k": chave, "documentoId": documento_id, "ts": int(time.time() * 1000)}
        with self._lock:
            self._carregar()[chave] = item
            try:
                self.arquivo.parent.mkdir(parents=True, exist_ok=True)
                with self.arquivo.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
            except OSError:
                pass  # sem anotação, só perde o "pular já enviados"


def ensaio(
    origem: Path,
    *,
    subpastas: bool,
    pasta_pai: str,
    pasta: str,
    perfil: str,
    registro: Registro | None,
    pular_enviados: bool,
) -> dict[str, Any]:
    """O que seria enviado, sem falar com a API."""
    if not pasta.strip():
        raise ErroImportacao("Informe a pasta de destino no Docnuvem.")
    arquivos, ignorados = varrer(origem, subpastas)
    a_enviar = []
    ja_enviados = 0
    for a in arquivos:
        pai, nome_pasta = destino_da_api(pasta_pai, pasta, a.partes)
        if pular_enviados and registro and registro.ja(Registro.chave(perfil, a, pai, nome_pasta)):
            ja_enviados += 1
        else:
            a_enviar.append((a, pai, nome_pasta))
    return {
        "arquivos": len(arquivos),
        "bytes": sum(a.tamanho for a in arquivos),
        "pastas": len({a.partes for a in arquivos}),
        "porExtensao": dict(Counter(a.nome.rsplit(".", 1)[-1].lower() for a in arquivos)),
        "ignorados": ignorados[:100],
        "nIgnorados": len(ignorados),
        "jaEnviados": ja_enviados,
        "aEnviar": len(a_enviar),
        "bytesAEnviar": sum(a.tamanho for a, _, _ in a_enviar),
        "amostra": [
            {"arquivo": str(a.caminho.relative_to(origem)), "pastaPai": pai or "(raiz)", "pasta": p}
            for a, pai, p in a_enviar[:6]
        ],
    }


# --------------------------------------------------------------------------
# Execução em segundo plano
# --------------------------------------------------------------------------


@dataclass
class Importacao:
    id: str
    perfil: str
    origem: Path
    itens: list[tuple[Arquivo, str, str]]  # (arquivo, nomePastaPai, nomePasta)
    tipo: str
    intervalo_ms: int
    pulados_antes: int = 0
    estado: str = "executando"  # executando | concluido | cancelado | erro
    feitos: int = 0
    enviados: int = 0
    bytes_enviados: int = 0
    atual: str = ""
    erro: str = ""
    falhas: list[dict[str, Any]] = field(default_factory=list)
    linhas: list[list[Any]] = field(default_factory=list)
    seguidas: int = 0
    iniciado_em: float = field(default_factory=time.time)
    terminou_em: float | None = None
    cancelar: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def ativo(self) -> bool:
        return self.estado == "executando"

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "perfil": self.perfil,
                "estado": self.estado,
                "total": len(self.itens),
                "feitos": self.feitos,
                "enviados": self.enviados,
                "nFalhas": len(self.falhas),
                "falhas": self.falhas[-MAX_FALHAS_EXIBIDAS:],
                "bytes": self.bytes_enviados,
                "puladosAntes": self.pulados_antes,
                "atual": self.atual,
                "erro": self.erro,
                "iniciadoEm": int(self.iniciado_em * 1000),
                "terminouEm": int(self.terminou_em * 1000) if self.terminou_em else None,
            }

    def relatorio_csv(self) -> bytes:
        saida = io.StringIO()
        w = csv.writer(saida, delimiter=";")
        w.writerow(["arquivo", "pastaPai", "pasta", "situacao", "http", "documentoId", "detalhe"])
        w.writerows(self.linhas)
        return saida.getvalue().encode("utf-8-sig")


class GerenciadorImportacao:
    def __init__(self, registro: Registro | None = None) -> None:
        self.registro = registro
        self._importacoes: dict[str, Importacao] = {}
        self._lock = threading.Lock()

    def obter(self, id_: str) -> Importacao | None:
        with self._lock:
            return self._importacoes.get(id_)

    def iniciar(
        self,
        cliente: httpx.Client,
        perfil_nome: str,
        perfil: PerfilConfig,
        *,
        origem: Path,
        subpastas: bool,
        pasta_pai: str,
        pasta: str,
        tipo: str,
        pular_enviados: bool,
        intervalo_ms: int,
    ) -> Importacao:
        if not pasta.strip():
            raise ErroImportacao("Informe a pasta de destino no Docnuvem.")
        arquivos, _ = varrer(origem, subpastas)
        itens: list[tuple[Arquivo, str, str]] = []
        pulados = 0
        for a in arquivos:
            pai, nome_pasta = destino_da_api(pasta_pai, pasta, a.partes)
            chave = Registro.chave(perfil_nome, a, pai, nome_pasta)
            if pular_enviados and self.registro and self.registro.ja(chave):
                pulados += 1
            else:
                itens.append((a, pai, nome_pasta))
        if not itens:
            raise ErroImportacao("Não há nada para enviar: todos os arquivos já foram enviados.")
        intervalo_ms = max(0, min(INTERVALO_MAX_MS, intervalo_ms))
        with self._lock:
            if any(i.ativo for i in self._importacoes.values()):
                raise ErroImportacao("Já existe uma importação em andamento. Aguarde ou cancele.")
            imp = Importacao(
                id=uuid.uuid4().hex[:12],
                perfil=perfil_nome,
                origem=origem,
                itens=itens,
                tipo=tipo.strip(),
                intervalo_ms=intervalo_ms,
                pulados_antes=pulados,
            )
            self._importacoes[imp.id] = imp
        args = (imp, cliente, perfil, self.registro)
        threading.Thread(target=executar, args=args, daemon=True).start()
        return imp


def _registrar(imp: Importacao, item: tuple[Arquivo, str, str], situacao: str, http: Any,
               doc: Any, detalhe: str) -> None:  # fmt: skip
    a, pai, pasta = item
    with imp.lock:
        imp.feitos += 1
        imp.linhas.append(
            [str(a.caminho.relative_to(imp.origem)), pai, pasta, situacao, http, doc, detalhe]
        )
        if situacao == "enviado":
            imp.enviados += 1
            imp.bytes_enviados += a.tamanho
            imp.seguidas = 0
        else:
            imp.seguidas += 1
            imp.falhas.append({"arquivo": str(a.caminho.relative_to(imp.origem)), "erro": detalhe})


def executar(
    imp: Importacao, cliente: httpx.Client, perfil: PerfilConfig, registro: Registro | None
) -> None:
    base = perfil.baseUrl.rstrip("/")
    auth = {"Authorization": f"Bearer {perfil.token}"}
    try:
        for indice, item in enumerate(imp.itens):
            if imp.cancelar.is_set():
                return _terminar(imp, "cancelado")
            a, pai, pasta = item
            with imp.lock:
                imp.atual = str(a.caminho.relative_to(imp.origem))
            params = {
                "instancia": perfil.instancia.lower(),
                "nomeArquivo": a.nome,
                "nomePasta": pasta,
            }
            if pai:
                params["nomePastaPai"] = pai
            if imp.tipo:
                params["tipo"] = imp.tipo
            try:
                with a.caminho.open("rb") as f:
                    tipo_mime = mimetypes.guess_type(a.nome)[0] or "application/octet-stream"
                    resp = cliente.post(
                        base + "/importar",
                        params=params,
                        files={"file": (a.nome, f, tipo_mime)},
                        headers=auth,
                        timeout=httpx.Timeout(300.0, connect=15.0),
                    )
            except OSError as exc:
                _registrar(
                    imp,
                    item,
                    "falhou",
                    "",
                    "",
                    f"não consegui ler o arquivo: {exc.strerror or exc}",
                )
            except httpx.HTTPError as exc:
                aviso = "Não sei se o arquivo chegou: confira antes de repetir."
                msg = f"falha de conexão: {exc}. {aviso}"
                _registrar(imp, item, "falhou", "", "", msg)
            else:
                doc = _documento(resp)
                if resp.status_code in (200, 201):
                    _registrar(imp, item, "enviado", resp.status_code, doc, "")
                    if registro:
                        registro.registrar(Registro.chave(imp.perfil, a, pai, pasta), doc)
                else:
                    msg = _mensagem(resp) or "sem mensagem"
                    _registrar(
                        imp, item, "falhou", resp.status_code, "", f"HTTP {resp.status_code}: {msg}"
                    )
            if imp.seguidas >= MAX_FALHAS_SEGUIDAS:
                with imp.lock:
                    imp.erro = (
                        f"Parei depois de {MAX_FALHAS_SEGUIDAS} falhas seguidas, para não insistir "
                        "num problema. Veja o motivo na lista e no relatório."
                    )
                return _terminar(imp, "erro")
            if imp.intervalo_ms and indice < len(imp.itens) - 1:
                imp.cancelar.wait(imp.intervalo_ms / 1000)
        _terminar(imp, "concluido")
    except Exception as exc:  # noqa: BLE001 - a importação nunca pode morrer calada
        with imp.lock:
            imp.erro = f"Erro inesperado: {exc}"
        _terminar(imp, "erro")


def _documento(resp: httpx.Response) -> Any:
    try:
        dado = resp.json()
    except ValueError:
        return ""
    return dado.get("documentoId", "") if isinstance(dado, dict) else ""


def _terminar(imp: Importacao, estado: str) -> None:
    with imp.lock:
        imp.estado = estado
        imp.atual = ""
        imp.terminou_em = time.time()
