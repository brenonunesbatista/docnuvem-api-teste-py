"""Fixtures: uma API falsa (upstream) e o servidor local rodando contra ela."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from docnuvem_tester.config import AppConfig, PerfilConfig, load_config
from docnuvem_tester.web import Historico, Servidor

TOKEN_OK = "tok-ok-1234"


@dataclass
class Upstream:
    base: str
    recebidas: list[dict[str, Any]] = field(default_factory=list)
    respostas: dict[tuple[str, str], tuple[int, str]] = field(default_factory=dict)
    # Resposta que depende de QUEM chama: (método, rota, valor do cabeçalho Authorization).
    por_token: dict[tuple[str, str, str], tuple[int, str]] = field(default_factory=dict)
    # Respostas calculadas a partir dos parâmetros da consulta (ex.: paginação).
    dinamicas: dict[tuple[str, str], Callable[[dict[str, list[str]]], tuple[int, str]]] = field(
        default_factory=dict
    )


@pytest.fixture
def upstream() -> Iterator[Upstream]:
    estado = Upstream(base="")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _responder(self) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            corpo = self.rfile.read(n)
            rota = urlsplit(self.path).path
            estado.recebidas.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "rota": rota,
                    "auth": self.headers.get("Authorization"),
                    "ct": self.headers.get("Content-Type"),
                    "corpo": corpo,
                }
            )
            auth = self.headers.get("Authorization") or ""
            if (self.command, rota, auth) in estado.por_token:
                status, texto = estado.por_token[(self.command, rota, auth)]
            elif (self.command, rota) in estado.dinamicas:
                status, texto = estado.dinamicas[(self.command, rota)](
                    parse_qs(urlsplit(self.path).query)
                )
            elif (self.command, rota) in estado.respostas:
                status, texto = estado.respostas[(self.command, rota)]
            elif rota == "/v3/api-docs/swagger-config":
                status, texto = 200, "{}"
            elif rota == "/api/modelos":
                ok = self.headers.get("Authorization") == f"Bearer {TOKEN_OK}"
                status, texto = (
                    (200, '{"modelos":[]}') if ok else (401, '{"retorno":"Token inválido"}')
                )
            else:
                status, texto = 200, '{"retorno":"ok"}'
            dados = texto.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        do_GET = do_POST = do_DELETE = _responder

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    estado.base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    yield estado
    srv.shutdown()
    srv.server_close()


@dataclass
class App:
    base: str
    http: httpx.Client
    servidor: Servidor
    historico: Historico | None


def _subir(
    cfg: AppConfig, historico: Historico | None, arquivo_config: Path | None = None
) -> Iterator[App]:
    servidor = Servidor(("127.0.0.1", 0), cfg, historico, arquivo_config=arquivo_config)
    threading.Thread(
        target=servidor.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    ).start()
    base = f"http://127.0.0.1:{servidor.server_address[1]}"
    with httpx.Client(base_url=base, timeout=15) as http:
        yield App(base, http, servidor, historico)
    servidor.shutdown()
    servidor.server_close()


@pytest.fixture
def app(upstream: Upstream, tmp_path: Path) -> Iterator[App]:
    cfg = AppConfig(
        perfis={
            "cliente1": PerfilConfig("CLIENTE1", upstream.base, TOKEN_OK),
            "ruim": PerfilConfig("ruim", upstream.base, "token-errado"),
            "fora": PerfilConfig("fora", "http://127.0.0.1:9", "x"),
            "prod": PerfilConfig("prod", upstream.base, TOKEN_OK, "confirmar"),
            "leitura": PerfilConfig("leitura", upstream.base, TOKEN_OK, "bloquear"),
        },
        perfilPadrao="cliente1",
    )
    yield from _subir(cfg, Historico(tmp_path / "historico.jsonl"))


@pytest.fixture
def app_sem_historico(upstream: Upstream) -> Iterator[App]:
    cfg = AppConfig(
        perfis={"cliente1": PerfilConfig("cliente1", upstream.base, TOKEN_OK)},
        perfilPadrao="cliente1",
    )
    yield from _subir(cfg, None)


@pytest.fixture
def app_cfg(upstream: Upstream, tmp_path: Path) -> Iterator[App]:
    """Servidor cujos perfis vêm de um config.json de verdade (para testar a edição)."""
    arquivo = tmp_path / "config.json"
    arquivo.write_text(
        json.dumps(
            {
                "perfis": {
                    "a": {"instancia": "A", "baseUrl": upstream.base, "token": TOKEN_OK},
                    "b": {"instancia": "b", "baseUrl": upstream.base, "token": "token-b-9999"},
                },
                "perfilPadrao": "a",
                "extra": {"mantido": True},
            }
        ),
        encoding="utf-8",
    )
    yield from _subir(load_config(arquivo), Historico(tmp_path / "historico.jsonl"), arquivo)
