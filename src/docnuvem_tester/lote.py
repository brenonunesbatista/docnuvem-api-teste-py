"""Lote para escolas: CSV de alunos -> solicitação de envio de documentos (ou consulta de status).

A solicitação ENVIA E-MAIL DE VERDADE a cada aluno, por isso o fluxo é: validar o CSV (sem
rede) -> confirmar -> enviar com intervalo entre as chamadas, com parada automática se várias
falharem em seguida. Uma chamada que falha nunca é repetida (poderia duplicar o e-mail).
"""

from __future__ import annotations

import csv
import io
import re
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.download import _mensagem

MAX_LINHAS = 5000
MAX_FALHAS_SEGUIDAS = 5  # no envio: para tudo depois de tantas falhas em seguida
MAX_FALHAS_EXIBIDAS = 50
INTERVALO_MAX_MS = 10_000
ALIAS = {
    "codigomatricula": "codigoMatricula",
    "matricula": "codigoMatricula",
    "nome": "nome",
    "cpf": "cpf",
    "email": "email",
    "telefone": "telefone",
    "tiposolicitacao": "tipoSolicitacao",
    "tipo": "tipoSolicitacao",
    "emailresponsavel": "emailResponsavel",
}
OBRIGATORIAS = ("codigoMatricula", "nome", "cpf", "email")
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ErroLote(Exception):
    """Pedido de lote inválido (a mensagem pode ser mostrada ao usuário)."""


def cpf_valido(texto: str) -> bool:
    d = re.sub(r"\D", "", texto)
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        soma = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if (soma * 10 % 11) % 10 != int(d[n]):
            return False
    return True


def _chave(cabecalho: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", cabecalho).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", sem_acento.lower())


def ler_csv(texto: str) -> list[dict[str, str]]:
    """Lê o CSV (vírgula ou ponto e vírgula, com ou sem BOM). Cada linha traz o número da linha."""
    texto = texto.lstrip("﻿")
    primeira = texto.splitlines()[0] if texto.strip() else ""
    delimitador = ";" if primeira.count(";") >= primeira.count(",") else ","
    leitor = csv.reader(io.StringIO(texto), delimiter=delimitador)
    linhas = list(leitor)
    if not linhas or not any(c.strip() for c in linhas[0]):
        raise ErroLote("O arquivo está vazio.")
    colunas = [ALIAS.get(_chave(c), "") for c in linhas[0]]
    faltam = [c for c in OBRIGATORIAS if c not in colunas]
    if faltam:
        raise ErroLote(
            "Faltam colunas no cabeçalho: "
            + ", ".join(faltam)
            + ". Use: codigoMatricula, nome, cpf, email (e, se quiser, telefone, "
            "tipoSolicitacao, emailResponsavel)."
        )
    saida: list[dict[str, str]] = []
    for numero, campos in enumerate(linhas[1:], start=2):
        if not any(c.strip() for c in campos):
            continue
        linha = {"linha": str(numero)}
        for coluna, valor in zip(colunas, campos, strict=False):
            if coluna:
                linha[coluna] = valor.strip()
        saida.append(linha)
    if not saida:
        raise ErroLote("O arquivo só tem o cabeçalho: não há alunos.")
    if len(saida) > MAX_LINHAS:
        raise ErroLote(f"O lote tem {len(saida)} linhas; o máximo é {MAX_LINHAS}.")
    return saida


def problemas(linha: dict[str, str], vistas: set[str]) -> list[str]:
    erros: list[str] = []
    matricula = linha.get("codigoMatricula", "")
    if not matricula:
        erros.append("matrícula vazia")
    elif matricula in vistas:
        erros.append("matrícula repetida no arquivo")
    if not linha.get("nome"):
        erros.append("nome vazio")
    if not cpf_valido(linha.get("cpf", "")):
        erros.append("CPF inválido")
    email = linha.get("email", "")
    if not _EMAIL.match(email):
        erros.append("e-mail inválido")
    telefone = re.sub(r"\D", "", linha.get("telefone", ""))
    if telefone and not 10 <= len(telefone) <= 13:
        erros.append("telefone deve ter DDD (10 a 13 dígitos)")
    if linha.get("tipoSolicitacao", "1") not in ("1", "2", ""):
        erros.append("tipoSolicitacao deve ser 1 ou 2")
    responsavel = linha.get("emailResponsavel", "")
    if responsavel and not _EMAIL.match(responsavel):
        erros.append("e-mail do responsável inválido")
    return erros


def preparar(texto: str) -> dict[str, Any]:
    """Valida o CSV inteiro sem usar a rede."""
    linhas = ler_csv(texto)
    vistas: set[str] = set()
    validas: list[dict[str, str]] = []
    invalidas: list[dict[str, Any]] = []
    for linha in linhas:
        erros = problemas(linha, vistas)
        if linha.get("codigoMatricula"):
            vistas.add(linha["codigoMatricula"])
        if erros:
            invalidas.append(
                {
                    "linha": int(linha["linha"]),
                    "codigoMatricula": linha.get("codigoMatricula", ""),
                    "problemas": erros,
                }
            )
        else:
            validas.append(linha)
    return {"linhas": linhas, "validas": validas, "invalidas": invalidas}


def corpo_solicitacao(linha: dict[str, str]) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "codigoMatricula": linha["codigoMatricula"],
        "nome": linha["nome"],
        "cpf": re.sub(r"\D", "", linha["cpf"]),
        "email": linha["email"],
        "tipoSolicitacao": int(linha.get("tipoSolicitacao") or 1),
    }
    telefone = re.sub(r"\D", "", linha.get("telefone", ""))
    if telefone:
        corpo["telefone"] = telefone
    if linha.get("emailResponsavel"):
        corpo["emailResponsavel"] = linha["emailResponsavel"]
    return corpo


# --------------------------------------------------------------------------
# Execução em segundo plano
# --------------------------------------------------------------------------


@dataclass
class Lote:
    id: str
    perfil: str
    modo: str  # "enviar" | "consultar"
    validas: list[dict[str, str]]
    invalidas: list[dict[str, Any]]
    intervalo_ms: int
    estado: str = "executando"  # executando | concluido | cancelado | erro
    feitos: int = 0
    ok: int = 0
    pendentes_itens: int = 0
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
                "modo": self.modo,
                "estado": self.estado,
                "total": len(self.validas),
                "feitos": self.feitos,
                "ok": self.ok,
                "nFalhas": len(self.falhas),
                "falhas": self.falhas[-MAX_FALHAS_EXIBIDAS:],
                "invalidas": len(self.invalidas),
                "pendentesItens": self.pendentes_itens,
                "atual": self.atual,
                "erro": self.erro,
                "iniciadoEm": int(self.iniciado_em * 1000),
                "terminouEm": int(self.terminou_em * 1000) if self.terminou_em else None,
            }

    def relatorio_csv(self) -> bytes:
        saida = io.StringIO()
        w = csv.writer(saida, delimiter=";")
        w.writerow(["linha", "codigoMatricula", "nome", "situacao", "http", "detalhe"])
        w.writerows(self.linhas)
        for inv in self.invalidas:
            w.writerow(
                [
                    inv["linha"],
                    inv["codigoMatricula"],
                    "",
                    "ignorada",
                    "",
                    "; ".join(inv["problemas"]),
                ]
            )
        return saida.getvalue().encode("utf-8-sig")


class GerenciadorLote:
    def __init__(self) -> None:
        self._lotes: dict[str, Lote] = {}
        self._lock = threading.Lock()

    def obter(self, id_: str) -> Lote | None:
        with self._lock:
            return self._lotes.get(id_)

    def iniciar(
        self,
        cliente: httpx.Client,
        perfil_nome: str,
        perfil: PerfilConfig,
        *,
        texto: str,
        modo: str,
        intervalo_ms: int,
    ) -> Lote:
        preparado = preparar(texto)
        if not preparado["validas"]:
            raise ErroLote("Nenhuma linha válida: corrija o arquivo e tente de novo.")
        intervalo_ms = max(0, min(INTERVALO_MAX_MS, intervalo_ms))
        with self._lock:
            if any(item.ativo for item in self._lotes.values()):
                raise ErroLote("Já existe um lote em andamento. Aguarde ou cancele.")
            lote = Lote(
                id=uuid.uuid4().hex[:12],
                perfil=perfil_nome,
                modo=modo,
                validas=preparado["validas"],
                invalidas=preparado["invalidas"],
                intervalo_ms=intervalo_ms,
            )
            self._lotes[lote.id] = lote
        threading.Thread(target=executar, args=(lote, cliente, perfil), daemon=True).start()
        return lote


def _registrar(lote: Lote, linha: dict[str, str], situacao: str, http: Any, detalhe: str) -> None:
    with lote.lock:
        lote.feitos += 1
        lote.linhas.append(
            [linha["linha"], linha["codigoMatricula"], linha["nome"], situacao, http, detalhe]
        )
        if situacao in ("enviada", "consultada"):
            lote.ok += 1
            lote.seguidas = 0
        else:
            lote.seguidas += 1
            lote.falhas.append(
                {
                    "linha": linha["linha"],
                    "codigoMatricula": linha["codigoMatricula"],
                    "erro": detalhe,
                }
            )


def _consultar(lote: Lote, resp: httpx.Response) -> str:
    try:
        dado = resp.json()
    except ValueError:
        return "resposta sem JSON"
    solicitacoes = [s for s in (dado.get("solicitacoes") or []) if isinstance(s, dict)]
    pendentes = sum(int(s.get("quantidadeItensPendentes") or 0) for s in solicitacoes)
    with lote.lock:
        lote.pendentes_itens += pendentes
    return f"{len(solicitacoes)} solicitação(ões), {pendentes} item(ns) pendente(s)"


def executar(lote: Lote, cliente: httpx.Client, perfil: PerfilConfig) -> None:
    base = perfil.baseUrl.rstrip("/")
    auth = {"Authorization": f"Bearer {perfil.token}"}
    instancia = perfil.instancia.lower()
    try:
        for indice, linha in enumerate(lote.validas):
            if lote.cancelar.is_set():
                return _terminar(lote, "cancelado")
            with lote.lock:
                lote.atual = f"{linha['codigoMatricula']} · {linha['nome']}"
            try:
                if lote.modo == "enviar":
                    resp = cliente.post(
                        base + "/api/solicitacaoAluno/solicitarEnvioDocumentos",
                        params={"instancia": instancia},
                        json=corpo_solicitacao(linha),
                        headers=auth,
                        timeout=60,
                    )
                else:
                    resp = cliente.get(
                        base + "/api/solicitacaoAluno/consultarStatus",
                        params={
                            "instancia": instancia,
                            "codigoMatricula": linha["codigoMatricula"],
                        },
                        headers=auth,
                        timeout=60,
                    )
            except httpx.HTTPError as exc:
                aviso = (
                    " Não sei se o e-mail saiu: confira antes de repetir."
                    if lote.modo == "enviar"
                    else ""
                )
                _registrar(lote, linha, "falhou", "", f"falha de conexão: {exc}.{aviso}")
            else:
                if resp.status_code in (200, 201):
                    if lote.modo == "enviar":
                        detalhe = _detalhe_envio(resp)
                        _registrar(lote, linha, "enviada", resp.status_code, detalhe)
                    else:
                        _registrar(
                            lote, linha, "consultada", resp.status_code, _consultar(lote, resp)
                        )
                else:
                    msg = _mensagem(resp) or "sem mensagem"
                    _registrar(
                        lote, linha, "falhou", resp.status_code, f"HTTP {resp.status_code}: {msg}"
                    )
            if lote.modo == "enviar" and lote.seguidas >= MAX_FALHAS_SEGUIDAS:
                with lote.lock:
                    lote.erro = (
                        f"Parei depois de {MAX_FALHAS_SEGUIDAS} falhas seguidas, para não "
                        "insistir num problema. Veja o motivo na lista e no relatório."
                    )
                return _terminar(lote, "erro")
            if lote.intervalo_ms and indice < len(lote.validas) - 1:
                lote.cancelar.wait(lote.intervalo_ms / 1000)
        _terminar(lote, "concluido")
    except Exception as exc:  # noqa: BLE001 - o lote nunca pode morrer calado
        with lote.lock:
            lote.erro = f"Erro inesperado: {exc}"
        _terminar(lote, "erro")


def _detalhe_envio(resp: httpx.Response) -> str:
    try:
        dado = resp.json()
    except ValueError:
        return ""
    if not isinstance(dado, dict):
        return ""
    enviado = "sim" if dado.get("emailEnviado") else "não"
    return f"e-mail enviado: {enviado}; link: {dado.get('linkEnvio') or '—'}"


def _terminar(lote: Lote, estado: str) -> None:
    with lote.lock:
        lote.estado = estado
        lote.atual = ""
        lote.terminou_em = time.time()
