"""Estrutura de pastas da plataforma, para escolher uma pasta em vez de digitar o caminho.

Carrega a árvore sob demanda, um nível por vez (as instâncias podem ter milhares de pastas).
Fontes, nesta ordem:

1. `GET /api/diretorios?diretorioPaiId=` (o endpoint oficial de pastas);
2. se a API o recusar (hoje devolve 401 em várias instâncias), o endpoint do sincronizador
   `POST /api/sync/listarDiretoriosFilhos`, que só LISTA as subpastas imediatas. Nada é
   alterado em nenhum dos dois.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.download import _mensagem

TAMANHO_PAGINA = 200  # máximo aceito pelas duas listagens
MAX_PAGINAS = 50
TTL_FONTE = 600.0  # segundos que a fonte que funcionou fica lembrada (evita bater no 401 sempre)


class ErroPastas(Exception):
    """Não foi possível listar as pastas (a mensagem pode ser mostrada ao usuário)."""


class _Falha(Exception):
    def __init__(self, status: int | None, texto: str) -> None:
        super().__init__(texto)
        self.status = status


class Pastas:
    """Lista as subpastas imediatas de uma pasta, lembrando qual fonte funciona por perfil."""

    def __init__(self) -> None:
        self._fontes: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def _lembrada(self, perfil: str) -> str | None:
        with self._lock:
            item = self._fontes.get(perfil)
        if item and time.monotonic() - item[1] < TTL_FONTE:
            return item[0]
        return None

    def _lembrar(self, perfil: str, fonte: str) -> None:
        with self._lock:
            self._fontes[perfil] = (fonte, time.monotonic())

    def esquecer(self, perfil: str | None = None) -> None:
        with self._lock:
            if perfil is None:
                self._fontes.clear()
            else:
                self._fontes.pop(perfil, None)

    # --- fontes ---------------------------------------------------------------
    @staticmethod
    def _oficial(cliente: httpx.Client, p: PerfilConfig, pai: int) -> list[dict[str, Any]]:
        saida: list[dict[str, Any]] = []
        for pagina in range(MAX_PAGINAS):
            r = cliente.get(
                p.baseUrl.rstrip("/") + "/api/diretorios",
                params={
                    "instancia": p.instancia.lower(),
                    "diretorioPaiId": pai,
                    "pagina": pagina,
                    "tamanho": TAMANHO_PAGINA,
                },
                headers={"Authorization": f"Bearer {p.token}"},
                timeout=30,
            )
            if r.status_code != 200:
                raise _Falha(r.status_code, _mensagem(r))
            dado = _json(r)
            lote = [d for d in dado.get("diretorios") or [] if isinstance(d, dict)]
            for d in lote:
                if isinstance(d.get("diretorioId"), int):
                    saida.append(
                        {
                            "id": d["diretorioId"],
                            "nome": str(d.get("nome") or ""),
                            "caminho": str(d.get("caminho") or ""),
                            "lixeira": bool(d.get("lixeira")),
                        }
                    )
            if not dado.get("temMais") or not lote:
                break
        return saida

    @staticmethod
    def _sincronizador(cliente: httpx.Client, p: PerfilConfig, pai: int) -> list[dict[str, Any]]:
        saida: list[dict[str, Any]] = []
        for pagina in range(MAX_PAGINAS):
            r = cliente.post(
                p.baseUrl.rstrip("/") + "/api/sync/listarDiretoriosFilhos",
                params={
                    "instancia": p.instancia.lower(),
                    "diretorioPaiId": pai,
                    "page": pagina,
                    "size": TAMANHO_PAGINA,
                },
                headers={"Authorization": f"Bearer {p.token}"},
                timeout=30,
            )
            if r.status_code != 200:
                raise _Falha(r.status_code, _mensagem(r))
            dado = _json(r)
            lote = [d for d in dado.get("diretorios") or [] if isinstance(d, dict)]
            for d in lote:
                if isinstance(d.get("id"), int):
                    saida.append(
                        {
                            "id": d["id"],
                            "nome": str(d.get("nome") or ""),
                            "caminho": str(d.get("caminhoNomesPais") or ""),
                            "lixeira": bool(d.get("lixeira")),
                        }
                    )
            if not dado.get("temMais") or not lote:
                break
        return saida

    # --- consulta ---------------------------------------------------------------
    def filhos(
        self, cliente: httpx.Client, nome: str, perfil: PerfilConfig, pai: int
    ) -> dict[str, Any]:
        """Subpastas imediatas de `pai` (0 = raiz de Meus documentos), ordenadas por nome."""
        ordem = ["sincronizador"] if self._lembrada(nome) == "sincronizador" else []
        ordem += [f for f in ("api", "sincronizador") if f not in ordem]
        falhas: list[str] = []
        for fonte in ordem:
            try:
                bruto = (
                    self._oficial(cliente, perfil, pai)
                    if fonte == "api"
                    else self._sincronizador(cliente, perfil, pai)
                )
            except _Falha as exc:
                falhas.append(f"{fonte}: HTTP {exc.status} {exc}".strip())
                continue
            except httpx.HTTPError as exc:
                raise ErroPastas(f"Falha de conexão com a API: {exc}") from exc
            self._lembrar(nome, fonte)
            pastas = [
                {"id": d["id"], "nome": d["nome"], "caminho": d["caminho"]}
                for d in sorted(bruto, key=lambda d: d["nome"].casefold())
                if not d["lixeira"]
            ]
            return {"fonte": fonte, "pai": pai, "pastas": pastas}
        raise ErroPastas(
            "A API não deixou listar as pastas deste perfil ("
            + "; ".join(falhas)
            + "). Digite o caminho no campo."
        )


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        dado = resp.json()
    except ValueError:
        return {}
    return dado if isinstance(dado, dict) else {}
