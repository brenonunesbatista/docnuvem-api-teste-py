"""Excluir documentos de uma pasta em massa, cancelando antes os fluxos de assinatura ativos.

DESTRUTIVO. Por isso o fluxo é sempre: ver o que será excluído (sem alterar nada) -> confirmar
digitando a quantidade exata -> executar com intervalo, parada automática e relatório.

- Excluir um documento o ENVIA PARA A LIXEIRA da plataforma (endpoint do sincronizador
  `excluirDocumento`); não é exclusão definitiva.
- Cancelar um fluxo de assinatura NÃO TEM VOLTA: os links param de funcionar e, se alguém já
  assinou, o arquivo volta à versão anterior às assinaturas. Só são cancelados fluxos ATIVOS
  (status `pendente`), e só se a pessoa pediu.
- Nunca age na raiz ("Meus documentos"). Uma chamada que falha não é repetida.
- Não há limite de quantidade: a lista inteira é lida ANTES de qualquer exclusão (apagar enquanto
  se pagina deslocaria as páginas) e, se o total mudar durante a leitura, nada é excluído.
"""

from __future__ import annotations

import csv
import io
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.download import (
    TAMANHO_PAGINA,
    ErroDownload,
    _mensagem,
    _pagina,
    validar_filtros,
)

PAGINAS_FOLGA = 5  # páginas além do esperado antes de desconfiar de uma API que não termina
MAX_FALHAS_SEGUIDAS = 5
MAX_FALHAS_EXIBIDAS = 50
INTERVALO_MAX_MS = 10_000
MOTIVO = "Exclusão em massa (Docnuvem API Tester)"


class ErroExclusao(Exception):
    """Pedido inválido ou recusado (a mensagem pode ser mostrada ao usuário)."""


def frase_de_confirmacao(n: int) -> str:
    return f"EXCLUIR {n}"


def credencial(perfil: PerfilConfig) -> str:
    """Token para o endpoint do sincronizador: o do usuário (se entrou) ou o da instância."""
    return perfil.tokenUsuario or perfil.token


def validar_pedido(diretorio_id: int, status: str, data_inicio: str, data_fim: str) -> None:
    if diretorio_id <= 0:
        raise ErroExclusao(
            "Escolha uma pasta: a raiz (Meus documentos) não pode ser esvaziada aqui."
        )
    try:
        validar_filtros(status, data_inicio, data_fim)
    except ErroDownload as exc:
        raise ErroExclusao(str(exc)) from exc


def _consulta(
    cliente: httpx.Client,
    perfil: PerfilConfig,
    *,
    diretorio_id: int,
    subpastas: bool,
    status: str,
    data_inicio: str,
    data_fim: str,
    pagina: int,
    tamanho: int,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "instancia": perfil.instancia.lower(),
        "diretorioId": diretorio_id,
        "incluirSubpastas": "true" if subpastas else "false",
        "pagina": pagina,
        "tamanho": tamanho,
    }
    for chave, valor in (("status", status), ("dataInicio", data_inicio), ("dataFim", data_fim)):
        if valor:  # filtro vazio nunca é enviado (a API recusa "status=")
            params[chave] = valor
    try:
        return _pagina(
            cliente,
            perfil.baseUrl.rstrip("/"),
            "/api/documentos",
            {"Authorization": f"Bearer {perfil.token}"},
            params,
        )
    except ErroDownload as exc:
        raise ErroExclusao(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise ErroExclusao(f"Falha de conexão com a API: {exc}") from exc


def contar_rapido(cliente: httpx.Client, perfil: PerfilConfig, **filtros: Any) -> int | None:
    """Total da seleção segundo a API, com uma consulta só (None se ela não informar)."""
    validar_pedido(
        filtros["diretorio_id"], filtros["status"], filtros["data_inicio"], filtros["data_fim"]
    )
    total = _consulta(cliente, perfil, pagina=0, tamanho=1, **filtros).get("total")
    return total if isinstance(total, int) else None


def listar_alvos(
    cliente: httpx.Client,
    perfil: PerfilConfig,
    *,
    diretorio_id: int,
    subpastas: bool,
    status: str,
    data_inicio: str,
    data_fim: str,
    cancelar: threading.Event | None = None,
    ao_ler: Callable[[int], None] | None = None,
) -> list[dict[str, Any]]:
    """TODOS os documentos da seleção (sem limite), direto da API (sempre atual).

    Lê tudo antes de qualquer exclusão: apagar enquanto se pagina deslocaria as páginas.
    """
    validar_pedido(diretorio_id, status, data_inicio, data_fim)
    alvos: list[dict[str, Any]] = []
    vistos: set[int] = set()
    total: int | None = None
    pagina = 0
    while True:
        if cancelar is not None and cancelar.is_set():
            break
        dado = _consulta(
            cliente,
            perfil,
            diretorio_id=diretorio_id,
            subpastas=subpastas,
            status=status,
            data_inicio=data_inicio,
            data_fim=data_fim,
            pagina=pagina,
            tamanho=TAMANHO_PAGINA,
        )
        if total is None and isinstance(dado.get("total"), int):
            total = dado["total"]
        lote = [d for d in dado.get("documentos") or [] if isinstance(d, dict)]
        novos = 0
        for d in lote:
            id_ = d.get("documentoId")
            if isinstance(id_, int) and id_ not in vistos:
                vistos.add(id_)
                novos += 1
                alvos.append(
                    {
                        "documentoId": id_,
                        "nome": str(d.get("nomeArquivo") or f"documento {id_}"),
                        "status": str(d.get("status") or "sem_assinatura").lower(),
                        "assinaturaId": d.get("assinaturaId"),
                        "diretorioId": d.get("diretorioId"),
                    }
                )
        if ao_ler is not None:
            ao_ler(len(alvos))
        pagina += 1
        if not dado.get("temMais") or not lote or novos == 0:  # sem novidade: a API não avança
            break
        if total is not None and pagina > total // TAMANHO_PAGINA + PAGINAS_FOLGA:
            break
    return alvos


def _ativo(doc: dict[str, Any]) -> bool:
    """Tem fluxo de assinatura em andamento (o único caso em que o cancelamento se aplica)."""
    return doc["status"] == "pendente" and isinstance(doc["assinaturaId"], int)


def ensaio(alvos: list[dict[str, Any]], cancelar_fluxos: bool) -> dict[str, Any]:
    """Resumo do que seria feito, sem alterar nada."""
    ativos = [d for d in alvos if _ativo(d)]
    return {
        "total": len(alvos),
        "porStatus": dict(Counter(d["status"] for d in alvos)),
        "fluxosAtivos": len(ativos),
        "serao": {
            "cancelados": len(ativos) if cancelar_fluxos else 0,
            "excluidos": len(alvos) if cancelar_fluxos else len(alvos) - len(ativos),
            "pulados": 0 if cancelar_fluxos else len(ativos),
        },
        "amostra": [
            {"documentoId": d["documentoId"], "nome": d["nome"], "status": d["status"]}
            for d in alvos[:8]
        ],
        "confirmacao": frase_de_confirmacao(len(alvos)),
    }


# --------------------------------------------------------------------------
# Execução em segundo plano
# --------------------------------------------------------------------------


@dataclass
class Exclusao:
    id: str
    perfil: str
    diretorio_id: int
    alvos: list[dict[str, Any]]
    cancelar_fluxos: bool
    intervalo_ms: int
    subpastas: bool = False
    status: str = ""
    data_inicio: str = ""
    data_fim: str = ""
    confirmado: int = 0  # a quantidade que a pessoa confirmou
    total_previsto: int = 0
    lidos: int = 0
    estado: str = "executando"  # listando | executando | concluido | cancelado | erro
    feitos: int = 0
    excluidos: int = 0
    fluxos_cancelados: int = 0
    pulados: int = 0
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
        return self.estado in ("listando", "executando")

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "perfil": self.perfil,
                "diretorioId": self.diretorio_id,
                "estado": self.estado,
                "lidos": self.lidos,
                "total": len(self.alvos) or self.total_previsto,
                "feitos": self.feitos,
                "excluidos": self.excluidos,
                "fluxosCancelados": self.fluxos_cancelados,
                "pulados": self.pulados,
                "nFalhas": len(self.falhas),
                "falhas": self.falhas[-MAX_FALHAS_EXIBIDAS:],
                "atual": self.atual,
                "erro": self.erro,
                "iniciadoEm": int(self.iniciado_em * 1000),
                "terminouEm": int(self.terminou_em * 1000) if self.terminou_em else None,
            }

    def relatorio_csv(self) -> bytes:
        saida = io.StringIO()
        w = csv.writer(saida, delimiter=";")
        w.writerow(["documentoId", "nome", "status_antes", "fluxo", "situacao", "http", "detalhe"])
        w.writerows(self.linhas)
        return saida.getvalue().encode("utf-8-sig")


class GerenciadorExclusao:
    def __init__(self) -> None:
        self._exclusoes: dict[str, Exclusao] = {}
        self._lock = threading.Lock()

    def obter(self, id_: str) -> Exclusao | None:
        with self._lock:
            return self._exclusoes.get(id_)

    def iniciar(
        self,
        cliente: httpx.Client,
        perfil_nome: str,
        perfil: PerfilConfig,
        *,
        diretorio_id: int,
        subpastas: bool,
        status: str,
        data_inicio: str,
        data_fim: str,
        cancelar_fluxos: bool,
        confirmacao: str,
        intervalo_ms: int,
    ) -> Exclusao:
        filtros: dict[str, Any] = {
            "diretorio_id": diretorio_id,
            "subpastas": subpastas,
            "status": status,
            "data_inicio": data_inicio,
            "data_fim": data_fim,
        }
        total = contar_rapido(cliente, perfil, **filtros)
        alvos: list[dict[str, Any]] = []
        if total is None:  # a API não informa o total: lê tudo agora, no próprio pedido
            alvos = listar_alvos(cliente, perfil, **filtros)
            total = len(alvos)
        if total == 0:
            raise ErroExclusao("Não há documentos nesta seleção.")
        esperado = frase_de_confirmacao(total)
        if " ".join(confirmacao.upper().split()) != esperado:
            raise ErroExclusao(
                f"A confirmação não confere. Agora há {total} documento(s) na seleção: "
                f"digite {esperado}. Se o número mudou, a pasta foi alterada desde a verificação: "
                "refaça-a."
            )
        intervalo_ms = max(0, min(INTERVALO_MAX_MS, intervalo_ms))
        with self._lock:
            if any(e.ativo for e in self._exclusoes.values()):
                raise ErroExclusao("Já existe uma exclusão em andamento. Aguarde ou cancele.")
            ex = Exclusao(
                id=uuid.uuid4().hex[:12],
                perfil=perfil_nome,
                diretorio_id=diretorio_id,
                alvos=alvos,
                cancelar_fluxos=cancelar_fluxos,
                intervalo_ms=intervalo_ms,
                subpastas=subpastas,
                status=status,
                data_inicio=data_inicio,
                data_fim=data_fim,
                confirmado=total,
                total_previsto=total,
                estado="executando" if alvos else "listando",
            )
            self._exclusoes[ex.id] = ex
        threading.Thread(target=executar, args=(ex, cliente, perfil), daemon=True).start()
        return ex


def _linha(
    ex: Exclusao, doc: dict[str, Any], fluxo: str, situacao: str, http: Any, detalhe: str
) -> None:
    with ex.lock:
        ex.feitos += 1
        ex.linhas.append(
            [doc["documentoId"], doc["nome"], doc["status"], fluxo, situacao, http, detalhe]
        )
        if situacao == "excluido":
            ex.excluidos += 1
            ex.seguidas = 0
        elif situacao == "pulado":
            ex.pulados += 1
        else:
            ex.seguidas += 1
            ex.falhas.append(
                {"documentoId": doc["documentoId"], "nome": doc["nome"], "erro": detalhe}
            )


def executar(ex: Exclusao, cliente: httpx.Client, perfil: PerfilConfig) -> None:
    base = perfil.baseUrl.rstrip("/")
    instancia = perfil.instancia.lower()
    auth_instancia = {"Authorization": f"Bearer {perfil.token}"}
    auth_sync = {"Authorization": f"Bearer {credencial(perfil)}"}
    try:
        if ex.estado == "listando" and not _ler_tudo(ex, cliente, perfil):
            return
        for indice, doc in enumerate(ex.alvos):
            if ex.cancelar.is_set():
                return _terminar(ex, "cancelado")
            with ex.lock:
                ex.atual = doc["nome"]
            parar = _processar(ex, cliente, base, instancia, auth_instancia, auth_sync, doc)
            if parar:
                return _terminar(ex, "erro")
            if ex.seguidas >= MAX_FALHAS_SEGUIDAS:
                with ex.lock:
                    ex.erro = (
                        f"Parei depois de {MAX_FALHAS_SEGUIDAS} falhas seguidas, para não insistir "
                        "num problema. Veja o motivo na lista e no relatório."
                    )
                return _terminar(ex, "erro")
            if ex.intervalo_ms and indice < len(ex.alvos) - 1:
                ex.cancelar.wait(ex.intervalo_ms / 1000)
        _terminar(ex, "concluido")
    except Exception as exc:  # noqa: BLE001 - a exclusão nunca pode morrer calada
        with ex.lock:
            ex.erro = f"Erro inesperado: {exc}"
        _terminar(ex, "erro")


def _ler_tudo(ex: Exclusao, cliente: httpx.Client, perfil: PerfilConfig) -> bool:
    """Lê a seleção inteira. Devolve False se terminou o trabalho (erro, cancelamento ou
    total diferente do confirmado): nesse caso NADA foi excluído."""

    def progresso(n: int) -> None:
        with ex.lock:
            ex.lidos = n

    try:
        alvos = listar_alvos(
            cliente,
            perfil,
            diretorio_id=ex.diretorio_id,
            subpastas=ex.subpastas,
            status=ex.status,
            data_inicio=ex.data_inicio,
            data_fim=ex.data_fim,
            cancelar=ex.cancelar,
            ao_ler=progresso,
        )
    except ErroExclusao as exc:
        with ex.lock:
            ex.erro = f"Não consegui ler a pasta: {exc}. Nada foi excluído."
        _terminar(ex, "erro")
        return False
    if ex.cancelar.is_set():
        _terminar(ex, "cancelado")
        return False
    if len(alvos) != ex.confirmado:
        with ex.lock:
            ex.erro = (
                f"A pasta mudou durante a leitura (você confirmou {ex.confirmado} documento(s) e "
                f"encontrei {len(alvos)}). Nada foi excluído: refaça a verificação."
            )
        _terminar(ex, "erro")
        return False
    with ex.lock:
        ex.alvos = alvos
        ex.estado = "executando"
    return True


def _processar(
    ex: Exclusao,
    cliente: httpx.Client,
    base: str,
    instancia: str,
    auth_instancia: dict[str, str],
    auth_sync: dict[str, str],
    doc: dict[str, Any],
) -> bool:
    """Cancela o fluxo (se for o caso) e exclui o documento. Devolve True para abortar tudo."""
    fluxo = "—"
    if _ativo(doc):
        if not ex.cancelar_fluxos:
            motivo = "tem fluxo ativo e o cancelamento está desligado"
            _linha(ex, doc, "ativo (não cancelado)", "pulado", "", motivo)
            return False
        try:
            r = cliente.delete(
                f"{base}/api/assinatura/{doc['assinaturaId']}",
                params={"instancia": instancia, "motivo": MOTIVO},
                headers=auth_instancia,
                timeout=60,
            )
        except httpx.HTTPError as exc:
            _linha(
                ex,
                doc,
                "erro ao cancelar",
                "falhou",
                "",
                f"falha de conexão ao cancelar o fluxo: {exc}",
            )
            return False
        if r.status_code == 200:
            fluxo = "cancelado"
            with ex.lock:
                ex.fluxos_cancelados += 1
        elif r.status_code in (404, 409):  # já não estava ativo: segue para a exclusão
            fluxo = f"já não estava ativo (HTTP {r.status_code})"
        else:
            msg = _mensagem(r) or "sem mensagem"
            _linha(ex, doc, "erro ao cancelar", "falhou", r.status_code,
                   f"não consegui cancelar o fluxo (HTTP {r.status_code}): {msg}")  # fmt: skip
            return r.status_code in (401, 403)
    try:
        r = cliente.post(
            f"{base}/api/sync/excluirDocumento",
            params={"instancia": instancia, "documentoId": doc["documentoId"]},
            headers=auth_sync,
            timeout=60,
        )
    except httpx.HTTPError as exc:
        _linha(ex, doc, fluxo, "falhou", "", f"falha de conexão: {exc}. Confira antes de repetir.")
        return False
    if r.status_code == 200:
        _linha(ex, doc, fluxo, "excluido", 200, "enviado para a lixeira")
        return False
    msg = _mensagem(r) or "sem mensagem"
    _linha(ex, doc, fluxo, "falhou", r.status_code, f"HTTP {r.status_code}: {msg}")
    if r.status_code in (401, 403):  # sem permissão: continuar só geraria mais falhas
        with ex.lock:
            ex.erro = (
                "A API recusou a exclusão (sem permissão, ou a instância exige login de usuário). "
                f"Detalhe: {msg}. Entre com o seu usuário em Perfis e tente de novo."
            )
        return True
    return False


def _terminar(ex: Exclusao, estado: str) -> None:
    with ex.lock:
        ex.estado = estado
        ex.atual = ""
        ex.terminou_em = time.time()
