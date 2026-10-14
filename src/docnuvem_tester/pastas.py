"""Estrutura de pastas da plataforma, para escolher uma pasta em vez de digitar o caminho.

Carrega a árvore sob demanda, um nível por vez (as instâncias podem ter milhares de pastas).

Fontes, nesta ordem:

1. `GET /api/diretorios?diretorioPaiId=` (o endpoint oficial de pastas);
2. `POST /api/sync/tree`: a árvore INTEIRA numa chamada só (milhares de pastas em ~1 s). É lida uma
   vez, guardada por pouco tempo e usada para navegar sem outras chamadas; traz também quantos
   documentos há em cada pasta. Só leitura;
3. se a API recusar (hoje devolve 401 em várias instâncias), o endpoint do sincronizador
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
TTL_ARVORE = 120.0  # segundos que a árvore lida fica na memória
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
        self._arvores: dict[str, tuple[float, _Arvore]] = {}
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
                self._arvores.clear()
            else:
                self._lembrado.pop(perfil, None)
                self._arvores.pop(perfil, None)

    def invalidar(self, perfil: str) -> None:
        """Descarta a árvore guardada (depois de criar ou excluir pastas)."""
        with self._lock:
            self._arvores.pop(perfil, None)

    def _arvore(self, cliente: httpx.Client, nome: str, p: PerfilConfig, token: str) -> _Arvore:
        with self._lock:
            item = self._arvores.get(nome)
        if item and time.monotonic() - item[0] < TTL_ARVORE:
            return item[1]
        r = cliente.post(
            p.baseUrl.rstrip("/") + "/api/sync/tree",
            params={"instancia": p.instancia.lower()},
            headers={"Authorization": f"Bearer {token}"},
            timeout=120,
        )
        if r.status_code != 200:
            raise _Falha(r.status_code, _mensagem(r))
        dado = _json(r)
        if not isinstance(dado.get("diretorios"), list):
            raise _Falha(None, "resposta da árvore sem a lista de pastas")
        arvore = _Arvore(dado)
        with self._lock:
            self._arvores[nome] = (time.monotonic(), arvore)
        return arvore

    def contar_subpastas(
        self, cliente: httpx.Client, nome: str, perfil: PerfilConfig, pasta: int
    ) -> int | None:
        """Quantas pastas há dentro de `pasta` (todos os níveis), ou None se a árvore não vier."""
        credenciais = [perfil.tokenUsuario, perfil.token]
        for token in [t for t in credenciais if t]:
            try:
                return self._arvore(cliente, nome, perfil, token).subpastas_de(pasta)
            except (_Falha, httpx.HTTPError):
                continue
        return None

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
        return [(f, c, t) for c, t in credenciais for f in ("api", "arvore", "sincronizador")]

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
                if fonte == "arvore":
                    pastas = self._arvore(cliente, nome, perfil, token).filhos(pai)
                    self._lembrar(nome, f"{fonte}/{de_quem}")
                    return {"fonte": fonte, "credencial": de_quem, "pai": pai, "pastas": pastas}
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

    def criar(
        self, cliente: httpx.Client, nome: str, perfil: PerfilConfig, pai: int, nome_pasta: str
    ) -> dict[str, Any]:
        """Cria uma pasta dentro de `pai` (sincronizador). ALTERA dados: o chamador confere a
        proteção do perfil antes."""
        try:
            r = cliente.post(
                perfil.baseUrl.rstrip("/") + "/api/sync/criarDiretorio",
                params={
                    "instancia": perfil.instancia.lower(),
                    "diretorioPaiId": pai,
                    "nome": nome_pasta,
                },
                headers={"Authorization": f"Bearer {perfil.tokenUsuario or perfil.token}"},
                timeout=60,
            )
        except httpx.HTTPError as exc:
            raise ErroPastas(f"Falha de conexão com a API: {exc}") from exc
        self.invalidar(nome)
        if r.status_code != 200:
            msg = _mensagem(r) or "sem mensagem"
            raise ErroPastas(
                f"A API recusou criar a pasta (HTTP {r.status_code}): {msg}",
                r.status_code in (401, 403),
            )
        return _json(r)


class _Arvore:
    """A árvore de pastas lida de uma vez (`/api/sync/tree`), com a contagem de documentos."""

    def __init__(self, dado: dict[str, Any]) -> None:
        self.filhas: dict[int, list[dict[str, Any]]] = {}
        pastas = [d for d in dado.get("diretorios") or [] if isinstance(d, dict)]
        diretos: dict[int, int] = {}
        for doc in dado.get("documentos") or []:
            if not isinstance(doc, dict) or doc.get("lixeira"):
                continue
            pasta = doc.get("diretorioId")
            if isinstance(pasta, int):
                diretos[pasta] = diretos.get(pasta, 0) + 1
        por_id = {d["id"]: d for d in pastas if isinstance(d.get("id"), int)}
        total = dict(diretos)
        for id_, n in diretos.items():  # soma cada contagem em todos os ancestrais
            atual: dict[str, Any] | None = por_id.get(id_)
            passos = 0
            while atual is not None and passos < 200:
                pai = atual.get("diretorioPaiId")
                if not isinstance(pai, int) or pai == id_:
                    break
                total[pai] = total.get(pai, 0) + n
                atual = por_id.get(pai)
                passos += 1
        for d in pastas:
            pid = d.get("id")
            if not isinstance(pid, int) or pid < 0 or d.get("lixeira"):
                continue  # a raiz especial "Envio inteligente" (-1) não é pasta de trabalho
            pai = d.get("diretorioPaiId")
            self.filhas.setdefault(pai if isinstance(pai, int) else 0, []).append(
                {
                    "id": pid,
                    "nome": str(d.get("nome") or ""),
                    "caminho": str(d.get("caminhoNomesPais") or ""),
                    "docs": diretos.get(pid, 0),
                    "docsTotal": total.get(pid, 0),
                }
            )
        for lista in self.filhas.values():
            lista.sort(key=lambda x: x["nome"].casefold())

    def filhos(self, pai: int) -> list[dict[str, Any]]:
        return [dict(x) for x in self.filhas.get(pai, [])]

    def subpastas_de(self, pai: int) -> int:
        """Quantas pastas há dentro de `pai`, em todos os níveis."""
        n, fila = 0, [pai]
        while fila:
            for f in self.filhas.get(fila.pop(), []):
                n += 1
                fila.append(f["id"])
        return n


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        dado = resp.json()
    except ValueError:
        return {}
    return dado if isinstance(dado, dict) else {}
