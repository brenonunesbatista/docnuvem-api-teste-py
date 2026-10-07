"""Carregamento do config.json (perfis de instância e token)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PerfilConfig:
    instancia: str
    baseUrl: str
    token: str
    # "" (livre), "confirmar" (pede confirmação ao alterar dados) ou "bloquear" (só leituras).
    protecao: str = ""


@dataclass(frozen=True)
class AppConfig:
    perfis: dict[str, PerfilConfig]
    perfilPadrao: str


class ConfigError(Exception):
    """Erro ao localizar ou validar o config.json."""


class PerfilNaoEncontrado(ConfigError):
    """O perfil pedido não existe no config.json."""


class PerfilJaExiste(ConfigError):
    """Já existe um perfil com esse nome."""


def config_path() -> Path:
    """Resolve o caminho do config.json.

    Por padrão procura na pasta atual e, no executável (.exe), também ao lado dele. Pode ser
    sobrescrito com a variável de ambiente DOCNUVEM_TESTER_CONFIG.
    """
    env = os.environ.get("DOCNUVEM_TESTER_CONFIG")
    if env:
        return Path(env)
    atual = Path.cwd() / "config.json"
    if not atual.exists() and getattr(sys, "frozen", False):
        ao_lado = Path(sys.executable).parent / "config.json"  # atalho com outra pasta de partida
        if ao_lado.exists():
            return ao_lado
    return atual


def _texto(valor: Any, onde: str) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise ConfigError(f"config.json inválido: {onde} deve ser um texto não vazio.")
    return valor.strip()


def _protecao(valor: Any, onde: str) -> str:
    """`protegido`: ausente/false = livre; true = confirmar; "confirmar" ou "bloquear"."""
    if valor is None or valor is False:
        return ""
    if valor is True:
        return "confirmar"
    if valor in ("confirmar", "bloquear"):
        return str(valor)
    raise ConfigError(
        f'config.json inválido: {onde} deve ser true, false, "confirmar" ou "bloquear".'
    )


def parse_config(dados: Any) -> AppConfig:
    """Valida o conteúdo já lido do config.json."""
    if not isinstance(dados, dict):
        raise ConfigError("config.json inválido: a raiz deve ser um objeto JSON.")
    bruto = dados.get("perfis")
    if not isinstance(bruto, dict) or not bruto:
        raise ConfigError('config.json inválido: "perfis" deve ter ao menos um perfil.')
    perfis: dict[str, PerfilConfig] = {}
    for nome, p in bruto.items():
        if not isinstance(p, dict):
            raise ConfigError(f'config.json inválido: o perfil "{nome}" deve ser um objeto.')
        perfis[nome] = PerfilConfig(
            instancia=_texto(p.get("instancia"), f"perfis.{nome}.instancia"),
            baseUrl=_texto(p.get("baseUrl"), f"perfis.{nome}.baseUrl"),
            # Espaço ou quebra de linha colados junto do token quebram o cabeçalho.
            token=_texto(p.get("token"), f"perfis.{nome}.token"),
            protecao=_protecao(p.get("protegido"), f"perfis.{nome}.protegido"),
        )
    padrao = _texto(dados.get("perfilPadrao"), "perfilPadrao")
    if padrao not in perfis:
        raise ConfigError(
            f'config.json inválido: perfilPadrao "{padrao}" não existe em "perfis" '
            f"({', '.join(perfis)})."
        )
    return AppConfig(perfis=perfis, perfilPadrao=padrao)


def load_config(path: Path | None = None) -> AppConfig:
    p = path or config_path()
    if not p.exists():
        raise ConfigError(
            f"Arquivo de configuração não encontrado: {p}\n"
            "Copie config.example.json para config.json e preencha os tokens."
        )
    try:
        # utf-8-sig: o Bloco de Notas do Windows pode salvar o arquivo com BOM.
        dados = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json inválido (JSON malformado): {exc}") from exc
    return parse_config(dados)


# --------------------------------------------------------------------------
# Edição dos perfis pela interface (sempre no config.json, que fica fora do git)
# --------------------------------------------------------------------------

# O nome vai no endereço local (/_proxy/<nome>/...): letras, números, espaço, _ . -
NOME_PERFIL = re.compile(r"^\w[\w .-]{0,39}$")
URL_BASE = re.compile(r"^https?://[^\s/?#]+[^\s]*$")


def _ler_bruto(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Arquivo de configuração não encontrado: {path}")
    try:
        dados = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json inválido (JSON malformado): {exc}") from exc
    if not isinstance(dados, dict) or not isinstance(dados.get("perfis"), dict):
        raise ConfigError('config.json inválido: "perfis" deve ser um objeto.')
    return dados


def _gravar_bruto(path: Path, dados: dict[str, Any]) -> AppConfig:
    """Valida, guarda uma cópia (config.json.bak) e troca o arquivo de uma vez."""
    cfg = parse_config(dados)  # nunca grava um arquivo que a própria ferramenta recusaria
    texto = json.dumps(dados, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    shutil.copyfile(path, path.with_name(path.name + ".bak"))
    os.replace(tmp, path)
    return cfg


def salvar_perfil(
    path: Path,
    *,
    original: str | None,
    nome: str,
    instancia: str,
    baseUrl: str,
    token: str,
    protecao: str,
) -> AppConfig:
    """Cria (original=None) ou altera um perfil. Token vazio, na edição, mantém o atual."""
    nome, instancia, baseUrl, token = (v.strip() for v in (nome, instancia, baseUrl, token))
    if not NOME_PERFIL.match(nome):
        raise ConfigError(
            "Nome inválido: use até 40 letras, números, espaço, ponto, hífen ou sublinhado."
        )
    if not instancia:
        raise ConfigError("Informe a instância.")
    if not URL_BASE.match(baseUrl):
        raise ConfigError("O endereço da API deve começar com http:// ou https://.")
    if protecao not in ("", "confirmar", "bloquear"):
        raise ConfigError('A proteção deve ser "", "confirmar" ou "bloquear".')
    bruto = _ler_bruto(path)
    perfis: dict[str, Any] = bruto["perfis"]
    if original is not None and original not in perfis:
        raise PerfilNaoEncontrado(f'O perfil "{original}" não existe.')
    if nome != original and nome in perfis:
        raise PerfilJaExiste(f'Já existe um perfil chamado "{nome}".')
    antigo = perfis.get(original) if original is not None else None
    if not token:
        if not isinstance(antigo, dict) or not antigo.get("token"):
            raise ConfigError("Informe o token.")
        token = str(antigo["token"])
    novo: dict[str, Any] = dict(antigo) if isinstance(antigo, dict) else {}
    novo.update(instancia=instancia, baseUrl=baseUrl, token=token)
    if protecao:
        novo["protegido"] = True if protecao == "confirmar" else "bloquear"
    else:
        novo.pop("protegido", None)
    refeito: dict[str, Any] = {}
    for chave, valor in perfis.items():
        if chave == original:
            refeito[nome] = novo
        else:
            refeito[chave] = valor
    if original is None:
        refeito[nome] = novo
    elif bruto.get("perfilPadrao") == original:
        bruto["perfilPadrao"] = nome
    bruto["perfis"] = refeito
    return _gravar_bruto(path, bruto)


def remover_perfil(path: Path, nome: str) -> AppConfig:
    bruto = _ler_bruto(path)
    perfis: dict[str, Any] = bruto["perfis"]
    if nome not in perfis:
        raise PerfilNaoEncontrado(f'O perfil "{nome}" não existe.')
    if len(perfis) == 1:
        raise ConfigError("Não dá para remover o último perfil.")
    del perfis[nome]
    if bruto.get("perfilPadrao") == nome:
        bruto["perfilPadrao"] = next(iter(perfis))
    return _gravar_bruto(path, bruto)


def definir_padrao(path: Path, nome: str) -> AppConfig:
    bruto = _ler_bruto(path)
    if nome not in bruto["perfis"]:
        raise PerfilNaoEncontrado(f'O perfil "{nome}" não existe.')
    bruto["perfilPadrao"] = nome
    return _gravar_bruto(path, bruto)
