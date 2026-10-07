"""Carregamento do config.json (perfis de instância e token)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PerfilConfig:
    instancia: str
    baseUrl: str
    token: str


@dataclass(frozen=True)
class AppConfig:
    perfis: dict[str, PerfilConfig]
    perfilPadrao: str


class ConfigError(Exception):
    """Erro ao localizar ou validar o config.json."""


def config_path() -> Path:
    """Resolve o caminho do config.json.

    Por padrão procura na pasta atual. Pode ser sobrescrito com a variável de
    ambiente DOCNUVEM_TESTER_CONFIG.
    """
    env = os.environ.get("DOCNUVEM_TESTER_CONFIG")
    if env:
        return Path(env)
    return Path.cwd() / "config.json"


def _texto(valor: Any, onde: str) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise ConfigError(f"config.json inválido: {onde} deve ser um texto não vazio.")
    return valor.strip()


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
        dados = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json inválido (JSON malformado): {exc}") from exc
    return parse_config(dados)
