"""Estrutura de pastas da plataforma, para escolher uma pasta em vez de digitar o caminho.

Carrega a árvore sob demanda, um nível por vez (as instâncias podem ter milhares de pastas).

Fontes, nesta ordem:

1. `GET /api/diretorios?diretorioPaiId=` (o endpoint oficial de pastas);
2. se a API o recusar (hoje devolve 401 em várias instâncias), o endpoint do sincronizador
   `POST /api/sync/listarDiretoriosFilhos`, que só LISTA as subpastas imediatas.

Credenciais, nesta ordem: o token do USUÁRIO do perfil (se a pessoa já entrou, veja
`login_usuario.py`) e, por último, o token fixo da instância. Nada é alterado em nenhum deles.
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
TTL_FONTE = 600.0  # segundos que o caminho que funcionou fica lembrado (evita bater no 401 sempre)


class ErroPastas(Exception):
    """Não foi possível listar as pastas (a mensagem pode ser mostrada ao usuário)."""

    def __init__(self, mensagem: str, precisa_login: bool = False) -> None:
        super().__init__(mensagem)
        self.precisa_login = precisa_login


class _Falha(Exception):
    def __init__(self, status: int | None, texto: str) -> None:
        super().__init__(texto)
        self.status = status


class Pastas:
    """Lista as subpastas imediatas de uma pasta, lembrando o que funciona por perfil."""

    def __init__(self) -> None:
        self._lembrado: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def _lembrada(self, perfil: str) -> str | None:
        with self._lock:
            item = self._lembrado.get(perfil)
        if item and time.monotonic() - item[1] < TTL_FONTE:
            return item[0]
        return None

    def _lembrar(self, perfil: str, tentativa: str) -> None:
        with self._lock:
            self._lembrado[perfil] = (tentativa, time.monotonic())

    def esquecer(self, perfil: str | None = None) -> None:
        with self._lock:
            if perfil is None:
                self._lembrado.clear()
            else:
                self._lembrado.pop(perfil, None)

    # --- fontes ---------------------------------------------------------------
    @staticmethod
    def _oficial(
        cliente: httpx.Client, p: PerfilConfig, token: str, pai: int
    ) -> list[dict[str, Any]]:
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
                headers={"Authorization": f"Bearer {token}"},
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
    def _sincronizador(
        cliente: httpx.Client, p: PerfilConfig, token: str, pai: int
    ) -> list[dict[str, Any]]:
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
                headers={"Authorization": f"Bearer {token}"},
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
    @staticmethod
    def _tentativas(perfil: PerfilConfig) -> list[tuple[str, str, str]]:
        """(fonte, de quem é o token, token), da mais provável à menos provável."""
        credenciais = [("usuario", perfil.tokenUsuario)] if perfil.tokenUsuario else []
        credenciais.append(("instancia", perfil.token))
        return [(f, c, t) for c, t in credenciais for f in ("api", "sincronizador")]

    def filhos(
        self, cliente: httpx.Client, nome: str, perfil: PerfilConfig, pai: int
    ) -> dict[str, Any]:
        """Subpastas imediatas de `pai` (0 = raiz de Meus documentos), ordenadas por nome."""
        tentativas = self._tentativas(perfil)
        lembrada = self._lembrada(nome)
        tentativas.sort(key=lambda t: f"{t[0]}/{t[1]}" != lembrada)  # a que já funcionou, primeiro
        falhas: list[str] = []
        precisa_login = False
        for fonte, de_quem, token in tentativas:
            try:
                bruto = (
                    self._oficial(cliente, perfil, token, pai)
                    if fonte == "api"
                    else self._sincronizador(cliente, perfil, token, pai)
                )
            except _Falha as exc:
                falhas.append(f"{fonte}/{de_quem}: HTTP {exc.status} {exc}".strip())
                if "login" in str(exc).lower() or (exc.status == 401 and de_quem == "usuario"):
                    precisa_login = True  # a API pediu login, ou o login guardado venceu
                continue
            except httpx.HTTPError as exc:
                raise ErroPastas(f"Falha de conexão com a API: {exc}") from exc
            self._lembrar(nome, f"{fonte}/{de_quem}")
            pastas = [
                {"id": d["id"], "nome": d["nome"], "caminho": d["caminho"]}
                for d in sorted(bruto, key=lambda d: d["nome"].casefold())
                if not d["lixeira"]
            ]
            return {"fonte": fonte, "credencial": de_quem, "pai": pai, "pastas": pastas}
        dica = (
            "Entre com o seu usuário nesta instância (Perfis > Entrar) ou digite o caminho."
            if precisa_login
            else "Digite o caminho no campo."
        )
        raise ErroPastas(
            "A API não deixou listar as pastas deste perfil (" + "; ".join(falhas) + "). " + dica,
            precisa_login,
        )


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        dado = resp.json()
    except ValueError:
        return {}
    return dado if isinstance(dado, dict) else {}
