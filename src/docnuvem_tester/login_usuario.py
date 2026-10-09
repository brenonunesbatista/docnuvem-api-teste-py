"""Login de usuário por instância (endpoint de login do sincronizador).

Algumas instâncias só deixam listar pastas com o token de um USUÁRIO (o token fixo da instância
recebe 401). O login troca usuário e senha por um token de dispositivo, que carrega a identidade
do usuário. Cada instância tem os seus próprios usuários.

Segurança, em ordem de importância:

- A senha só existe em memória, entre a tela e a API. Nunca é gravada, registrada no histórico
  nem devolvida à página; só o token fica no config.json (fora do git).
- A API recebe a senha na URL (query). Por isso qualquer texto de erro passa por `sem_segredos`
  antes de sair daqui.
- O token de usuário nunca vai para o navegador.
"""

from __future__ import annotations

import getpass
import platform
import re
import uuid
from typing import Any

import httpx

from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.download import _mensagem

_JWT = re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$")
_CHAVES_TOKEN = ("token", "tokenDispositivo", "accessToken", "access_token", "jwt", "authorization")


class ErroLogin(Exception):
    """Falha no login (a mensagem é segura para mostrar: não contém a senha)."""

    def __init__(self, mensagem: str, status: int = 400) -> None:
        super().__init__(mensagem)
        self.status = status


def sem_segredos(texto: str, *segredos: str) -> str:
    """Tira de um texto a senha (e qualquer outro segredo) e o parâmetro `senha=` de URLs."""
    texto = re.sub(r"(senha=)[^&\s'\"]*", r"\1***", texto, flags=re.IGNORECASE)
    for segredo in segredos:
        if segredo:
            texto = texto.replace(segredo, "***")
    return texto


def dispositivo_padrao() -> str:
    """Identificador estável desta máquina e deste usuário do Windows (não cria um dispositivo
    novo a cada login)."""
    base = f"{platform.node()}|{getpass.getuser()}"
    return "docnuvem-api-tester-" + uuid.uuid5(uuid.NAMESPACE_DNS, base).hex[:16]


def extrair_token(dado: Any) -> str | None:
    """Acha o token na resposta do login, sem depender de um nome de campo único."""
    if isinstance(dado, str):
        texto = dado.strip().removeprefix("Bearer ").strip()
        return texto if _JWT.match(texto) else None
    if isinstance(dado, dict):
        for chave in _CHAVES_TOKEN:  # primeiro os nomes mais prováveis
            valor = dado.get(chave)
            if isinstance(valor, str) and valor.strip():
                return valor.strip().removeprefix("Bearer ").strip()
        for valor in dado.values():  # depois qualquer coisa com cara de JWT, até aninhada
            achado = extrair_token(valor)
            if achado:
                return achado
    return None


def entrar(
    cliente: httpx.Client, perfil: PerfilConfig, login: str, senha: str, dispositivo: str
) -> str:
    """Autentica o usuário na instância do perfil e devolve o token do dispositivo."""
    try:
        r = cliente.post(
            perfil.baseUrl.rstrip("/") + "/api/sync/login",
            params={
                "instancia": perfil.instancia.lower(),
                "login": login,
                "senha": senha,
                "identificadorDispositivo": dispositivo,
                "nomeMaquina": platform.node() or "docnuvem-api-tester",
            },
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise ErroLogin(
            "Falha de conexão com a API: " + sem_segredos(str(exc), senha), 502
        ) from exc
    if r.status_code == 401:
        raise ErroLogin("Usuário ou senha inválidos para esta instância.", 401)
    if r.status_code == 403:
        raise ErroLogin(
            "Este usuário exige verificação em duas etapas, que a ferramenta não consegue fazer. "
            "Use um usuário sem essa exigência.",
            403,
        )
    if r.status_code != 200:
        msg = sem_segredos(_mensagem(r), senha) or "sem mensagem"
        raise ErroLogin(f"A API recusou o login (HTTP {r.status_code}): {msg}", r.status_code)
    try:
        dado = r.json()
    except ValueError:
        dado = r.text
    token = extrair_token(dado)
    if not token:
        campos = ", ".join(sorted(dado)) if isinstance(dado, dict) else type(dado).__name__
        raise ErroLogin(
            "O login foi aceito, mas não reconheci o token na resposta da API "
            f"(campos: {campos}). Avise quem mantém a ferramenta.",
            502,
        )
    return token
