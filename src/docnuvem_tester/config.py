"""Carregamento do config.json (perfis de instância/token)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ValidationError


class PerfilConfig(BaseModel):
    instancia: str
    baseUrl: str
    token: str


class AppConfig(BaseModel):
    perfis: dict[str, PerfilConfig]
    perfilPadrao: str

    def perfil_ativo_valido(self, nome: str) -> bool:
        return nome in self.perfis


class ConfigError(Exception):
    """Erro ao localizar ou validar o config.json."""


def config_path() -> Path:
    """Resolve o caminho do config.json.

    Por padrão procura na pasta atual (mesmo esquema da versão Node.js
    anterior). Pode ser sobrescrito com a variável de ambiente
    DOCNUVEM_TESTER_CONFIG.
    """
    env = os.environ.get("DOCNUVEM_TESTER_CONFIG")
    if env:
        return Path(env)
    return Path.cwd() / "config.json"


def load_config(path: Path | None = None) -> AppConfig:
    p = path or config_path()
    if not p.exists():
        raise ConfigError(
            f"Arquivo de configuração não encontrado: {p}\n"
            "Copie config.example.json para config.json e preencha os tokens."
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json inválido (JSON malformado): {exc}") from exc
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"config.json inválido:\n{exc}") from exc
