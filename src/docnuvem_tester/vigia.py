"""Vigia de assinaturas pendentes: avisa quando mudam de status ou estão perto de expirar.

Roda em segundo plano no servidor local, só com leituras: de tempos em tempos lista os
documentos com assinatura pendente do perfil, consulta o status de cada um e compara com a
verificação anterior. As mudanças viram eventos que a página mostra (e pode notificar).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from docnuvem_tester.config import PerfilConfig

MAX_MONITORADOS = 100  # documentos acompanhados por vez (cada um custa uma consulta por ciclo)
MAX_EVENTOS = 200
INTERVALO_MIN_MIN = 5  # minutos: não sobrecarrega a API
INTERVALO_MAX_MIN = 24 * 60
ALERTA_MIN_H = 1
ALERTA_MAX_H = 24 * 30
FINAIS = {"assinado", "expirado", "cancelado"}


class ErroVigia(Exception):
    """Pedido inválido (a mensagem pode ser mostrada ao usuário)."""


def _data(texto: Any) -> datetime | None:
    if not isinstance(texto, str):
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _texto_prazo(delta_s: float) -> str:
    horas = int(delta_s // 3600)
    if horas >= 48:
        return f"{horas // 24} dia(s)"
    if horas >= 1:
        return f"{horas} hora(s)"
    return f"{max(0, int(delta_s // 60))} minuto(s)"


@dataclass
class Vigia:
    perfil_nome: str
    perfil: PerfilConfig
    cliente: httpx.Client
    intervalo_min: int
    alerta_h: int
    ids_manuais: set[int] = field(default_factory=set)
    ativo: bool = True
    ciclos: int = 0
    erro: str = ""
    truncado: bool = False
    ultima: float | None = None
    proxima: float | None = None
    falhas_consulta: int = 0
    monitorados: dict[int, dict[str, Any]] = field(default_factory=dict)
    eventos: list[dict[str, Any]] = field(default_factory=list)
    _alertados: set[int] = field(default_factory=set)
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _acordar: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    # --- ciclo de vida ----------------------------------------------------
    def iniciar(self) -> None:
        self._thread = threading.Thread(target=self._laco, daemon=True)
        self._thread.start()

    def parar(self) -> None:
        self.ativo = False
        self._acordar.set()

    def agora(self) -> None:
        self._acordar.set()

    def _laco(self) -> None:
        while self.ativo:
            try:
                self.ciclo()
            except Exception as exc:  # noqa: BLE001 - o vigia nunca pode morrer calado
                with self._lock:
                    self.erro = f"Erro inesperado: {exc}"
            with self._lock:
                self.proxima = time.time() + self.intervalo_min * 60
            self._acordar.wait(self.intervalo_min * 60)
            self._acordar.clear()

    # --- um ciclo de verificação ------------------------------------------
    def _get(self, caminho: str, **params: Any) -> dict[str, Any]:
        r = self.cliente.get(
            self.perfil.baseUrl.rstrip("/") + caminho,
            params={"instancia": self.perfil.instancia.lower(), **params},
            headers={"Authorization": f"Bearer {self.perfil.token}"},
            timeout=30,
        )
        if r.status_code != 200:
            raise ErroVigia(f"A API respondeu HTTP {r.status_code} em {caminho}.")
        try:
            dado = r.json()
        except ValueError as exc:
            raise ErroVigia(f"Resposta inesperada da API em {caminho}.") from exc
        return dado if isinstance(dado, dict) else {}

    def ciclo(self) -> None:
        """Lista os pendentes, consulta cada um e gera eventos pelas diferenças."""
        try:
            listagem = self._get(
                "/api/documentos", status="pendente", pagina=0, tamanho=MAX_MONITORADOS
            )
        except (ErroVigia, httpx.HTTPError) as exc:
            with self._lock:
                self.erro = f"Não consegui listar os pendentes: {exc}"
                self.ultima = time.time()
            return
        docs = [d for d in listagem.get("documentos") or [] if isinstance(d, dict)]
        total = listagem.get("total")
        alvo: dict[int, str] = {}
        for d in docs:
            if isinstance(d.get("documentoId"), int):
                alvo[d["documentoId"]] = str(d.get("nomeArquivo") or "")
        for id_ in self.ids_manuais | set(self.monitorados):
            alvo.setdefault(id_, self.monitorados.get(id_, {}).get("nome", ""))
        alvo = dict(list(alvo.items())[: MAX_MONITORADOS + len(self.ids_manuais)])
        falhas = 0
        novos: dict[int, dict[str, Any]] = {}
        for id_, nome in alvo.items():
            try:
                novos[id_] = self._consultar(id_, nome)
            except (ErroVigia, httpx.HTTPError):
                falhas += 1
                if id_ in self.monitorados:  # mantém o último estado conhecido
                    novos[id_] = self.monitorados[id_]
        base = self.ciclos > 0
        with self._lock:
            for id_, atual in novos.items():
                antes = self.monitorados.get(id_)
                if base and antes is not None and antes is not atual:
                    self._comparar(id_, antes, atual)
                self._vencimento(id_, atual)
            # o que chegou a um estado final sai da lista (e dos ids manuais)
            for id_ in [i for i, d in novos.items() if d["status"] in FINAIS]:
                novos.pop(id_)
                self.ids_manuais.discard(id_)
            self.monitorados = novos
            self.truncado = isinstance(total, int) and total > len(docs)
            self.falhas_consulta = falhas
            self.erro = ""
            self.ciclos += 1
            self.ultima = time.time()

    def _consultar(self, id_: int, nome: str) -> dict[str, Any]:
        d = self._get(f"/api/documento/{id_}/status")
        sig = [
            {
                "nome": str(s.get("nome") or s.get("email") or "?"),
                "email": str(s.get("email") or ""),
                "status": str(s.get("status") or "").lower(),
                "visualizou": bool(s.get("visualizou")),
            }
            for s in d.get("signatarios") or []
            if isinstance(s, dict)
        ]
        return {
            "documentoId": id_,
            "nome": str(d.get("nomeArquivo") or nome or f"documento {id_}"),
            "status": str(d.get("status") or "").lower(),
            "validade": d.get("dataValidade") if isinstance(d.get("dataValidade"), str) else "",
            "signatarios": sig,
        }

    # --- eventos ------------------------------------------------------------
    def _evento(self, tipo: str, doc: dict[str, Any], detalhe: str) -> None:
        self._seq += 1
        self.eventos.append(
            {
                "id": self._seq,
                "ts": int(time.time() * 1000),
                "tipo": tipo,
                "documentoId": doc["documentoId"],
                "nome": doc["nome"],
                "detalhe": detalhe,
            }
        )
        del self.eventos[:-MAX_EVENTOS]

    def _comparar(self, id_: int, antes: dict[str, Any], atual: dict[str, Any]) -> None:
        por_chave = {s["email"] or s["nome"]: s for s in antes["signatarios"]}
        for s in atual["signatarios"]:
            ant = por_chave.get(s["email"] or s["nome"])
            if ant is None:
                continue
            if s["status"] == "assinado" and ant["status"] != "assinado":
                self._evento("signatario_assinou", atual, f"{s['nome']} assinou.")
            elif s["visualizou"] and not ant["visualizou"]:
                self._evento("signatario_visualizou", atual, f"{s['nome']} visualizou.")
        if atual["status"] != antes["status"]:
            novo = atual["status"] or "?"
            tipo = novo if novo in FINAIS else "status"
            self._evento(tipo, atual, f"Status: {antes['status'] or '?'} → {novo}.")

    def _vencimento(self, id_: int, doc: dict[str, Any]) -> None:
        validade = _data(doc["validade"])
        if validade is None or doc["status"] in FINAIS or id_ in self._alertados:
            return
        falta = (validade - datetime.now()).total_seconds()
        if falta <= self.alerta_h * 3600:
            self._alertados.add(id_)
            if falta <= 0:
                self._evento("perto_de_expirar", doc, "O prazo de validade já passou.")
            else:
                texto = f"Expira em {_texto_prazo(falta)} ({validade:%d/%m/%Y %H:%M})."
                self._evento("perto_de_expirar", doc, texto)

    # --- visão para a página -------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "perfil": self.perfil_nome,
                "ativo": self.ativo,
                "intervaloMin": self.intervalo_min,
                "alertaHoras": self.alerta_h,
                "ciclos": self.ciclos,
                "erro": self.erro,
                "truncado": self.truncado,
                "falhasConsulta": self.falhas_consulta,
                "ultima": int(self.ultima * 1000) if self.ultima else None,
                "proxima": int(self.proxima * 1000) if self.proxima else None,
                "monitorados": sorted(
                    self.monitorados.values(), key=lambda d: d["validade"] or "9999"
                ),
                "eventos": self.eventos[-100:][::-1],
                "ultimoEvento": self._seq,
            }


class GerenciadorVigia:
    """Um vigia por perfil."""

    def __init__(self) -> None:
        self._vigias: dict[str, Vigia] = {}
        self._lock = threading.Lock()

    def obter(self, perfil: str) -> Vigia | None:
        with self._lock:
            return self._vigias.get(perfil)

    def todos(self) -> list[Vigia]:
        with self._lock:
            return list(self._vigias.values())

    def iniciar(
        self,
        cliente: httpx.Client,
        nome: str,
        perfil: PerfilConfig,
        *,
        intervalo_min: int,
        alerta_h: int,
        ids: set[int],
    ) -> Vigia:
        if not INTERVALO_MIN_MIN <= intervalo_min <= INTERVALO_MAX_MIN:
            raise ErroVigia(
                f"O intervalo deve ficar entre {INTERVALO_MIN_MIN} e {INTERVALO_MAX_MIN} minutos."
            )
        if not ALERTA_MIN_H <= alerta_h <= ALERTA_MAX_H:
            raise ErroVigia(
                f"O aviso de expiração deve ficar entre {ALERTA_MIN_H} e {ALERTA_MAX_H} horas."
            )
        with self._lock:
            antigo = self._vigias.get(nome)
            if antigo is not None:
                antigo.parar()
            v = Vigia(nome, perfil, cliente, intervalo_min, alerta_h, set(ids))
            if antigo is not None:  # reiniciar não perde o histórico de eventos
                v.eventos = antigo.eventos
                v._seq = antigo._seq
            self._vigias[nome] = v
        v.iniciar()
        return v

    def parar_todos(self) -> None:
        for v in self.todos():
            v.parar()
