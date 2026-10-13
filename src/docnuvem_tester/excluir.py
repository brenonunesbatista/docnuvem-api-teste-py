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
- Pastas grandes demoram para ler (uma instância real leva ~1,8 s por página de 200). Por isso a
  leitura usa várias consultas em paralelo, mostra progresso, pode ser cancelada e a lista lida na
  verificação é REAPROVEITADA na exclusão (se o total ainda bater), em vez de ler tudo de novo.
"""

from __future__ import annotations

import contextlib
import csv
import io
import math
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
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
PARALELISMO = 6  # páginas lidas ao mesmo tempo (só leituras)
TTL_VERIFICACAO = 20 * 60.0  # segundos em que a lista da verificação pode ser reaproveitada
MAX_VERIFICACOES = 3  # listas guardadas na memória (as mais antigas são descartadas)
MAX_FALHAS_SEGUIDAS = 5
MAX_PASTAS = 500  # pastas marcadas de uma vez
MAX_FALHAS_EXIBIDAS = 50
MAX_FALHAS_NO_LOG = 200  # falhas gravadas no histórico por exclusão (o CSV tem todas)
INTERVALO_MAX_MS = 10_000
MOTIVO = "Exclusão em massa (Docnuvem API Tester)"


class ErroExclusao(Exception):
    """Pedido inválido ou recusado (a mensagem pode ser mostrada ao usuário)."""


def frase_de_confirmacao(n: int) -> str:
    return f"EXCLUIR {n}"


def credencial(perfil: PerfilConfig) -> str:
    """Token para o endpoint do sincronizador: o do usuário (se entrou) ou o da instância."""
    return perfil.tokenUsuario or perfil.token


def validar_pedido(
    diretorio_ids: Sequence[int], status: str, data_inicio: str, data_fim: str
) -> None:
    if not diretorio_ids:
        raise ErroExclusao("Escolha pelo menos uma pasta.")
    if any(d <= 0 for d in diretorio_ids):
        raise ErroExclusao(
            "Escolha uma pasta: a raiz (Meus documentos) não pode ser esvaziada aqui."
        )
    if len(diretorio_ids) > MAX_PASTAS:
        raise ErroExclusao(f"Escolha no máximo {MAX_PASTAS} pastas por vez.")
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
    ids = filtros.pop("diretorio_ids")
    validar_pedido(ids, filtros["status"], filtros["data_inicio"], filtros["data_fim"])
    soma = 0
    for id_ in ids:
        dado = _consulta(cliente, perfil, diretorio_id=id_, pagina=0, tamanho=1, **filtros)
        total = dado.get("total")
        if not isinstance(total, int):
            return None
        soma += total
    return soma


def _acumular(dado: dict[str, Any], alvos: list[dict[str, Any]], vistos: set[int]) -> int:
    """Junta os documentos novos de uma página (sem repetir). Devolve quantos eram novos."""
    novos = 0
    for d in dado.get("documentos") or []:
        id_ = d.get("documentoId") if isinstance(d, dict) else None
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
    return novos


def listar_alvos(
    cliente: httpx.Client,
    perfil: PerfilConfig,
    *,
    diretorio_ids: Sequence[int],
    subpastas: bool,
    status: str,
    data_inicio: str,
    data_fim: str,
    cancelar: threading.Event | None = None,
    ao_ler: Callable[[int, int | None], None] | None = None,
) -> list[dict[str, Any]]:
    """TODOS os documentos da seleção (uma ou várias pastas), direto da API (sempre atual).

    Um documento que apareça em mais de uma pasta marcada entra uma vez só.
    """
    validar_pedido(diretorio_ids, status, data_inicio, data_fim)
    alvos: list[dict[str, Any]] = []
    vistos: set[int] = set()
    total_geral: int | None = None
    if len(diretorio_ids) > 1:  # o total de todas as pastas, para o progresso fazer sentido
        total_geral = contar_rapido(
            cliente,
            perfil,
            diretorio_ids=list(diretorio_ids),
            subpastas=subpastas,
            status=status,
            data_inicio=data_inicio,
            data_fim=data_fim,
        )
    for id_ in diretorio_ids:
        if cancelar is not None and cancelar.is_set():
            break
        _listar_pasta(
            cliente,
            perfil,
            id_,
            alvos,
            vistos,
            total_geral,
            subpastas=subpastas,
            status=status,
            data_inicio=data_inicio,
            data_fim=data_fim,
            cancelar=cancelar,
            ao_ler=ao_ler,
        )
    return alvos


def _listar_pasta(
    cliente: httpx.Client,
    perfil: PerfilConfig,
    diretorio_id: int,
    alvos: list[dict[str, Any]],
    vistos: set[int],
    total_fixo: int | None,
    *,
    subpastas: bool,
    status: str,
    data_inicio: str,
    data_fim: str,
    cancelar: threading.Event | None,
    ao_ler: Callable[[int, int | None], None] | None,
) -> None:
    """Lê uma pasta inteira e acrescenta em `alvos`.

    Lê tudo antes de qualquer exclusão: apagar enquanto se pagina deslocaria as páginas. A primeira
    página informa o total; as demais são lidas em paralelo (só leituras), na ordem da API.
    """

    def ler(n: int) -> dict[str, Any]:
        return _consulta(
            cliente,
            perfil,
            diretorio_id=diretorio_id,
            subpastas=subpastas,
            status=status,
            data_inicio=data_inicio,
            data_fim=data_fim,
            pagina=n,
            tamanho=TAMANHO_PAGINA,
        )

    def cancelado() -> bool:
        return cancelar is not None and cancelar.is_set()

    if cancelado():
        return
    dado = ler(0)
    total = dado["total"] if isinstance(dado.get("total"), int) else None
    novos = _acumular(dado, alvos, vistos)
    mostrado = total_fixo if total_fixo is not None else total
    if ao_ler is not None:
        ao_ler(len(alvos), mostrado)
    pagina = 1
    previstas = math.ceil(total / TAMANHO_PAGINA) if total else 1
    continuar = bool(dado.get("temMais")) and novos > 0
    if continuar and total is not None and previstas > 1:
        parar = threading.Event()

        def tarefa(n: int) -> dict[str, Any] | None:
            return None if parar.is_set() or cancelado() else ler(n)

        with ThreadPoolExecutor(max_workers=PARALELISMO) as pool:
            try:
                ultimo = dado
                for lote in pool.map(tarefa, range(1, previstas)):
                    if lote is None:
                        break
                    _acumular(lote, alvos, vistos)
                    ultimo = lote
                    if ao_ler is not None:
                        ao_ler(len(alvos), mostrado)
            except BaseException:
                parar.set()  # as páginas que faltam nem chegam a ser pedidas
                raise
        pagina = previstas
        continuar = bool(ultimo.get("temMais")) and not cancelado()
    # Em série: API sem total, ou com mais documentos do que o total anunciado.
    while continuar and not cancelado():
        if total is not None and pagina > previstas + PAGINAS_FOLGA:
            break
        dado = ler(pagina)
        novos = _acumular(dado, alvos, vistos)
        if ao_ler is not None:
            ao_ler(len(alvos), mostrado)
        pagina += 1
        continuar = bool(dado.get("temMais")) and novos > 0  # sem novidade: a API não avança


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
class Verificacao:
    """Leitura da pasta (só leituras) para mostrar o que seria excluído. Pode demorar minutos."""

    id: str
    perfil: str
    filtros: tuple[Any, ...]  # (diretorio_ids, subpastas, status, data_inicio, data_fim)
    cancelar_fluxos: bool
    estado: str = "lendo"  # lendo | pronta | erro | cancelada
    lidos: int = 0
    total: int = 0
    resultado: dict[str, Any] | None = None
    erro: str = ""
    alvos: list[dict[str, Any]] = field(default_factory=list)
    iniciado_em: float = field(default_factory=time.time)
    terminou_em: float | None = None
    cancelar: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            decorrido = (self.terminou_em or time.time()) - self.iniciado_em
            restante = None
            if self.estado == "lendo" and 0 < self.lidos < self.total:
                restante = round(decorrido * (self.total - self.lidos) / self.lidos)
            return {
                "id": self.id,
                "estado": self.estado,
                "lidos": self.lidos,
                "total": self.total,
                "decorrido": round(decorrido),
                "restante": restante,
                "erro": self.erro,
                "resultado": self.resultado if self.estado == "pronta" else None,
            }


@dataclass
class Exclusao:
    id: str
    perfil: str
    diretorio_ids: tuple[int, ...]
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
    url_base: str = ""
    instancia: str = ""
    # Grava no histórico da ferramenta (log). Recebe um dict com method/url/status/ms/body/resumo.
    registrar: Callable[[dict[str, Any]], None] | None = None
    no_log: int = 0
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
                "diretorioId": self.diretorio_ids[0],
                "diretorioIds": list(self.diretorio_ids),
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
        self._verificacoes: dict[str, Verificacao] = {}
        self._lock = threading.Lock()

    def obter(self, id_: str) -> Exclusao | None:
        with self._lock:
            return self._exclusoes.get(id_)

    def obter_verificacao(self, id_: str) -> Verificacao | None:
        with self._lock:
            return self._verificacoes.get(id_)

    def verificar(
        self,
        cliente: httpx.Client,
        perfil_nome: str,
        perfil: PerfilConfig,
        *,
        diretorio_ids: Sequence[int],
        subpastas: bool,
        status: str,
        data_inicio: str,
        data_fim: str,
        cancelar_fluxos: bool,
    ) -> Verificacao:
        """Começa a ler a pasta em segundo plano (só leituras). Valida o pedido na hora."""
        ids = tuple(dict.fromkeys(diretorio_ids))  # sem repetir, na ordem marcada
        validar_pedido(ids, status, data_inicio, data_fim)
        v = Verificacao(
            id=uuid.uuid4().hex[:12],
            perfil=perfil_nome,
            filtros=(ids, subpastas, status, data_inicio, data_fim),
            cancelar_fluxos=cancelar_fluxos,
        )
        with self._lock:
            self._verificacoes[v.id] = v
            for antiga in list(self._verificacoes)[:-MAX_VERIFICACOES]:
                self._verificacoes.pop(antiga).cancelar.set()  # libera a memória da mais velha
        threading.Thread(target=_rodar_verificacao, args=(v, cliente, perfil), daemon=True).start()
        return v

    def iniciar(
        self,
        cliente: httpx.Client,
        perfil_nome: str,
        perfil: PerfilConfig,
        *,
        diretorio_ids: Sequence[int],
        subpastas: bool,
        status: str,
        data_inicio: str,
        data_fim: str,
        cancelar_fluxos: bool,
        confirmacao: str,
        intervalo_ms: int,
        verificacao_id: str = "",
        registrar: Callable[[dict[str, Any]], None] | None = None,
    ) -> Exclusao:
        ids = tuple(dict.fromkeys(diretorio_ids))
        filtros: dict[str, Any] = {
            "diretorio_ids": ids,
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
        if not alvos and verificacao_id:  # reaproveita a leitura da verificação, se ainda valer
            v = self.obter_verificacao(verificacao_id)
            igual = v is not None and v.estado == "pronta" and v.terminou_em is not None
            if (
                v is not None
                and igual
                and v.perfil == perfil_nome
                and v.filtros == tuple(filtros.values())
                and time.time() - (v.terminou_em or 0) < TTL_VERIFICACAO
                and len(v.alvos) == total
            ):
                alvos = list(v.alvos)
        intervalo_ms = max(0, min(INTERVALO_MAX_MS, intervalo_ms))
        with self._lock:
            if any(e.ativo for e in self._exclusoes.values()):
                raise ErroExclusao("Já existe uma exclusão em andamento. Aguarde ou cancele.")
            ex = Exclusao(
                id=uuid.uuid4().hex[:12],
                perfil=perfil_nome,
                diretorio_ids=ids,
                alvos=alvos,
                cancelar_fluxos=cancelar_fluxos,
                intervalo_ms=intervalo_ms,
                subpastas=subpastas,
                status=status,
                data_inicio=data_inicio,
                data_fim=data_fim,
                confirmado=total,
                total_previsto=total,
                registrar=registrar,
                estado="executando" if alvos else "listando",
            )
            self._exclusoes[ex.id] = ex
        threading.Thread(target=executar, args=(ex, cliente, perfil), daemon=True).start()
        return ex


def _rodar_verificacao(v: Verificacao, cliente: httpx.Client, perfil: PerfilConfig) -> None:
    diretorio_ids, subpastas, status, data_inicio, data_fim = v.filtros

    def progresso(n: int, total: int | None) -> None:
        with v.lock:
            v.lidos = n
            if total is not None:
                v.total = total

    try:
        alvos = listar_alvos(
            cliente,
            perfil,
            diretorio_ids=diretorio_ids,
            subpastas=subpastas,
            status=status,
            data_inicio=data_inicio,
            data_fim=data_fim,
            cancelar=v.cancelar,
            ao_ler=progresso,
        )
    except ErroExclusao as exc:
        with v.lock:
            v.erro, v.estado, v.terminou_em = str(exc), "erro", time.time()
        return
    with v.lock:
        if v.cancelar.is_set():
            v.estado, v.terminou_em = "cancelada", time.time()
            return
        v.alvos = alvos
        v.total = v.lidos = len(alvos)
        v.resultado = ensaio(alvos, v.cancelar_fluxos)
        v.estado, v.terminou_em = "pronta", time.time()


def _gravar_log(ex: Exclusao, evento: dict[str, Any]) -> None:
    """Manda o evento ao log da ferramenta. Um problema ao gravar nunca atrapalha a exclusão."""
    if ex.registrar is None:
        return
    with contextlib.suppress(Exception):
        ex.registrar(evento)


def _log_falha(
    ex: Exclusao, doc: dict[str, Any], fluxo: str, http: Any, detalhe: str, ms: int
) -> None:
    if ex.no_log >= MAX_FALHAS_NO_LOG:
        return
    ex.no_log += 1
    cancelando = fluxo == "erro ao cancelar"
    if cancelando:
        metodo, caminho = "DELETE", f"/api/assinatura/{doc.get('assinaturaId')}"
    else:
        metodo, caminho = "POST", "/api/sync/excluirDocumento"
    chave = "motivo" if cancelando else "documentoId"
    valor = MOTIVO if cancelando else doc["documentoId"]
    extra = f"{ex.no_log} de {MAX_FALHAS_NO_LOG}" if ex.no_log == MAX_FALHAS_NO_LOG else ""
    _gravar_log(
        ex,
        {
            "method": metodo,
            "url": f"{ex.url_base}{caminho}?instancia={ex.instancia}&{chave}={valor}",
            "status": http if isinstance(http, int) else 0,
            "ms": ms,
            "resumo": f"Exclusão em massa · documento {doc['documentoId']} ({doc['nome']}): "
            f"{detalhe}"
            + (
                f" · limite de {MAX_FALHAS_NO_LOG} falhas no log atingido ({extra})"
                if extra
                else ""
            ),
        },
    )


def _linha(
    ex: Exclusao,
    doc: dict[str, Any],
    fluxo: str,
    situacao: str,
    http: Any,
    detalhe: str,
    ms: int = 0,
) -> None:
    if situacao == "falhou":
        _log_falha(ex, doc, fluxo, http, detalhe, ms)
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
    ex.url_base, ex.instancia = base, instancia
    filtros = [
        _descrever_pastas(ex.diretorio_ids),
        "com subpastas" if ex.subpastas else "sem subpastas",
    ]
    if ex.status:
        filtros.append(f"status {ex.status}")
    if ex.data_inicio or ex.data_fim:
        filtros.append(f"período {ex.data_inicio or '…'} a {ex.data_fim or '…'}")
    _gravar_log(
        ex,
        {
            "method": "POST",
            "url": f"{base}/api/sync/excluirDocumento?instancia={instancia}",
            "status": 200,
            "ms": 0,
            "resumo": (
                f"Exclusão em massa iniciada: {ex.confirmado} documento(s) confirmado(s) "
                f"({', '.join(filtros)}); cancelar fluxos ativos: "
                f"{'sim' if ex.cancelar_fluxos else 'não'}; intervalo {ex.intervalo_ms} ms."
            ),
        },
    )
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

    def progresso(n: int, total: int | None) -> None:
        with ex.lock:
            ex.lidos = n

    try:
        alvos = listar_alvos(
            cliente,
            perfil,
            diretorio_ids=ex.diretorio_ids,
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


def _descrever_pastas(ids: Sequence[int]) -> str:
    if len(ids) == 1:
        return f"pasta {ids[0]}"
    amostra = ", ".join(str(i) for i in ids[:10]) + (" …" if len(ids) > 10 else "")
    return f"{len(ids)} pastas ({amostra})"


def _ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)


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
        t0 = time.perf_counter()
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
                _ms(t0),
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
            _linha(
                ex,
                doc,
                "erro ao cancelar",
                "falhou",
                r.status_code,
                f"não consegui cancelar o fluxo (HTTP {r.status_code}): {msg}",
                _ms(t0),
            )
            return r.status_code in (401, 403)
    t0 = time.perf_counter()
    try:
        r = cliente.post(
            f"{base}/api/sync/excluirDocumento",
            params={"instancia": instancia, "documentoId": doc["documentoId"]},
            headers=auth_sync,
            timeout=60,
        )
    except httpx.HTTPError as exc:
        _linha(
            ex,
            doc,
            fluxo,
            "falhou",
            "",
            f"falha de conexão: {exc}. Confira antes de repetir.",
            _ms(t0),
        )
        return False
    if r.status_code == 200:
        _linha(ex, doc, fluxo, "excluido", 200, "enviado para a lixeira")
        return False
    msg = _mensagem(r) or "sem mensagem"
    _linha(ex, doc, fluxo, "falhou", r.status_code, f"HTTP {r.status_code}: {msg}", _ms(t0))
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
        falhas, erro = len(ex.falhas), ex.erro
        resumo = (
            f"Exclusão em massa de {_descrever_pastas(ex.diretorio_ids)} {estado}: "
            f"{ex.excluidos} excluído(s), "
            f"{ex.fluxos_cancelados} fluxo(s) cancelado(s), {ex.pulados} pulado(s), "
            f"{falhas} falha(s)" + (f". {erro}" if erro else "")
        )
    if (
        falhas or erro or estado == "cancelado"
    ):  # sem problemas, não há nada a registrar além do que a tela já mostra
        _gravar_log(
            ex,
            {
                "method": "POST",
                "url": f"{ex.url_base}/api/sync/excluirDocumento?instancia={ex.instancia}",
                "status": 0 if estado == "erro" else 200,
                "ms": round((ex.terminou_em or time.time()) - ex.iniciado_em) * 1000,
                "resumo": resumo,
            },
        )
