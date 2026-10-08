"""Painel das instâncias e monitor de disponibilidade.

- Painel: foto de todos os perfis de uma vez (API, token, modelos, pendentes), só leituras.
- Monitor: em segundo plano, repete a verificação de saúde dos perfis escolhidos e registra
  quando algo muda: queda, erro, token recusado, lentidão e recuperação. Uma mudança só vira
  evento depois de confirmada em duas verificações seguidas, para não alarmar à toa.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import httpx

from docnuvem_tester.config import AppConfig, PerfilConfig

INTERVALO_MIN_MIN = 1
INTERVALO_MAX_MIN = 60
CONFIRMACOES = 2  # verificações seguidas com o mesmo resultado antes de declarar a mudança
HISTORICO_POR_PERFIL = 60
MAX_EVENTOS = 200
TTL_PAINEL = 30.0
PROBLEMAS = {"offline", "erro", "token", "lento"}
TIPOS = {"offline": "queda", "erro": "erro", "token": "token", "lento": "lenta"}


class ErroMonitor(Exception):
    """Pedido inválido (a mensagem pode ser mostrada ao usuário)."""


Verificador = Callable[[httpx.Client, PerfilConfig], dict[str, Any]]


def _pendentes(cliente: httpx.Client, perfil: PerfilConfig) -> int | None:
    """Total de documentos com assinatura pendente (None se a consulta não foi possível)."""
    try:
        r = cliente.get(
            perfil.baseUrl.rstrip("/") + "/api/documentos",
            params={
                "instancia": perfil.instancia.lower(),
                "status": "pendente",
                "pagina": 0,
                "tamanho": 1,
            },
            headers={"Authorization": f"Bearer {perfil.token}"},
            timeout=15,
        )
        total = r.json().get("total") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    return total if isinstance(total, int) else None


def painel_de(
    cliente: httpx.Client, nome: str, perfil: PerfilConfig, verificar: Verificador
) -> dict[str, Any]:
    """Situação de um perfil: o status de saúde mais modelos e pendentes (se a API respondeu)."""
    saude = verificar(cliente, perfil)
    pendentes = _pendentes(cliente, perfil) if saude["nivel"] in ("ok", "lento") else None
    return {
        "perfil": nome,
        "instancia": perfil.instancia,
        "baseUrl": perfil.baseUrl,
        "protegido": perfil.protecao,
        "nivel": saude["nivel"],
        "api": saude["api"],
        "token": saude["token"],
        "modelos": saude.get("modelos"),
        "pendentes": pendentes,
        "verificadoEm": saude["verificadoEm"],
    }


class Painel:
    """Foto de todos os perfis, em paralelo, com um cache curto."""

    def __init__(self) -> None:
        self._cache: tuple[float, list[dict[str, Any]]] | None = None
        self._lock = threading.Lock()

    def esquecer(self) -> None:
        with self._lock:
            self._cache = None

    def todos(
        self, cliente: httpx.Client, cfg: AppConfig, verificar: Verificador, forcar: bool = False
    ) -> list[dict[str, Any]]:
        with self._lock:
            if self._cache and not forcar and time.monotonic() - self._cache[0] < TTL_PAINEL:
                return self._cache[1]
        itens = list(cfg.perfis.items())
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(itens)))) as pool:
            res = list(pool.map(lambda i: painel_de(cliente, i[0], i[1], verificar), itens))
        with self._lock:
            self._cache = (time.monotonic(), res)
        return res


# --------------------------------------------------------------------------
# Monitor
# --------------------------------------------------------------------------


@dataclass
class Estado:
    nivel: str = ""  # o que já foi declarado
    desde: float = 0.0
    candidato: str = ""  # o que as últimas verificações vêm mostrando
    seguidas: int = 0


@dataclass
class Monitor:
    cliente: httpx.Client
    obter_cfg: Callable[[], AppConfig]
    verificar: Verificador
    perfis: set[str] = field(default_factory=set)
    intervalo_min: int = 5
    ativo: bool = False
    ciclos: int = 0
    ultima: float | None = None
    proxima: float | None = None
    historico: dict[str, deque[dict[str, Any]]] = field(default_factory=dict)
    estados: dict[str, Estado] = field(default_factory=dict)
    eventos: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _acordar: threading.Event = field(default_factory=threading.Event)
    _geracao: int = 0

    # --- ciclo de vida ----------------------------------------------------
    def iniciar(self, perfis: set[str], intervalo_min: int) -> None:
        cfg = self.obter_cfg()
        if not perfis:
            raise ErroMonitor("Escolha ao menos um perfil para monitorar.")
        desconhecidos = sorted(perfis - set(cfg.perfis))
        if desconhecidos:
            raise ErroMonitor("Perfil desconhecido: " + ", ".join(desconhecidos) + ".")
        if not INTERVALO_MIN_MIN <= intervalo_min <= INTERVALO_MAX_MIN:
            raise ErroMonitor(
                f"O intervalo deve ficar entre {INTERVALO_MIN_MIN} e {INTERVALO_MAX_MIN} minutos."
            )
        with self._lock:
            self._geracao += 1  # uma nova partida encerra o laço anterior
            geracao = self._geracao
            self.perfis = set(perfis)
            self.intervalo_min = intervalo_min
            self.ativo = True
            for p in self.perfis:
                self.historico.setdefault(p, deque(maxlen=HISTORICO_POR_PERFIL))
        self._acordar.set()
        threading.Thread(target=self._laco, args=(geracao,), daemon=True).start()

    def parar(self) -> None:
        with self._lock:
            self.ativo = False
            self._geracao += 1
            self.proxima = None
        self._acordar.set()

    def agora(self) -> None:
        self._acordar.set()

    def _laco(self, geracao: int) -> None:
        self._acordar.clear()
        while True:
            with self._lock:
                if not self.ativo or geracao != self._geracao:
                    return
            with contextlib.suppress(Exception):  # o monitor nunca pode morrer calado
                self.ciclo()
            with self._lock:
                if not self.ativo or geracao != self._geracao:
                    return
                self.proxima = time.time() + self.intervalo_min * 60
            self._acordar.wait(self.intervalo_min * 60)
            self._acordar.clear()

    # --- uma verificação -----------------------------------------------------
    def ciclo(self) -> None:
        cfg = self.obter_cfg()
        alvo = [(n, cfg.perfis[n]) for n in sorted(self.perfis) if n in cfg.perfis]
        if not alvo:
            return
        with ThreadPoolExecutor(max_workers=min(8, len(alvo))) as pool:
            resultados = list(pool.map(lambda i: (i[0], self.verificar(self.cliente, i[1])), alvo))
        agora = time.time()
        with self._lock:
            for nome, r in resultados:
                self._registrar(nome, r, agora)
            self.ciclos += 1
            self.ultima = agora

    def _registrar(self, nome: str, r: dict[str, Any], agora: float) -> None:
        nivel = r["nivel"]
        ms = (r["api"] or {}).get("ms")
        self.historico.setdefault(nome, deque(maxlen=HISTORICO_POR_PERFIL)).append(
            {"ts": int(agora * 1000), "nivel": nivel, "ms": ms}
        )
        est = self.estados.setdefault(nome, Estado())
        if not est.nivel:  # primeira observação: é a base
            est.nivel, est.desde, est.candidato, est.seguidas = nivel, agora, nivel, CONFIRMACOES
            if nivel in PROBLEMAS:
                self._evento(nome, TIPOS[nivel], nivel, self._detalhe(nivel, r, "já estava assim"))
            return
        if nivel == est.nivel:
            est.candidato, est.seguidas = nivel, 0
            return
        if nivel == est.candidato:
            est.seguidas += 1
        else:
            est.candidato, est.seguidas = nivel, 1
        if est.seguidas < CONFIRMACOES:
            return
        anterior, desde = est.nivel, est.desde
        est.nivel, est.desde, est.seguidas = nivel, agora, 0
        if nivel == "ok":
            tempo = _duracao(agora - desde)
            self._evento(nome, "recuperou", nivel, f"Voltou ao normal depois de {tempo}.")
        else:
            self._evento(nome, TIPOS[nivel], nivel, self._detalhe(nivel, r, f"antes: {anterior}"))

    @staticmethod
    def _detalhe(nivel: str, r: dict[str, Any], extra: str) -> str:
        if nivel == "offline":
            erro = (r["api"] or {}).get("erro") or "sem resposta"
            return f"API fora do ar ({erro}) ({extra})."
        if nivel == "token":
            return f"Token recusado (HTTP {(r['token'] or {}).get('status')}) ({extra})."
        if nivel == "lento":
            ms = max((r["api"] or {}).get("ms") or 0, (r["token"] or {}).get("ms") or 0)
            return f"API lenta: {ms} ms ({extra})."
        http_api, http_token = (r["api"] or {}).get("status"), (r["token"] or {}).get("status")
        return f"API com erro (HTTP {http_api}/{http_token}) ({extra})."

    def _evento(self, perfil: str, tipo: str, nivel: str, detalhe: str) -> None:
        self._seq += 1
        self.eventos.append(
            {
                "id": self._seq,
                "ts": int(time.time() * 1000),
                "perfil": perfil,
                "tipo": tipo,
                "nivel": nivel,
                "detalhe": detalhe,
            }
        )
        del self.eventos[:-MAX_EVENTOS]

    # --- visão para a página -----------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ativo": self.ativo,
                "perfis": sorted(self.perfis),
                "intervaloMin": self.intervalo_min,
                "ciclos": self.ciclos,
                "ultima": int(self.ultima * 1000) if self.ultima else None,
                "proxima": int(self.proxima * 1000) if self.proxima else None,
                "estado": {
                    n: {"nivel": e.nivel, "desde": int(e.desde * 1000)}
                    for n, e in self.estados.items()
                    if n in self.perfis
                },
                "historico": {n: list(h) for n, h in self.historico.items() if n in self.perfis},
                "eventos": self.eventos[-100:][::-1],
                "ultimoEvento": self._seq,
            }


def _duracao(segundos: float) -> str:
    minutos = int(segundos // 60)
    if minutos >= 120:
        return f"{minutos // 60} hora(s)"
    if minutos >= 1:
        return f"{minutos} minuto(s)"
    return f"{int(segundos)} segundo(s)"
