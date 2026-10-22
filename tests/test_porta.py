"""Porta bloqueada ou ocupada: o erro de porta tem de aparecer, para o programa tentar outra."""

from __future__ import annotations

import http.server
import socket
import threading

import pytest

from docnuvem_tester import web
from docnuvem_tester.config import AppConfig, PerfilConfig


def _cfg() -> AppConfig:
    return AppConfig(perfis={"a": PerfilConfig("a", "http://127.0.0.1:9", "x")}, perfilPadrao="a")


def test_porta_bloqueada_levanta_o_erro_de_porta_e_nao_attributeerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def bloqueada(self: http.server.HTTPServer) -> None:
        raise PermissionError(10013, "acesso ao soquete proibido")

    monkeypatch.setattr(http.server.HTTPServer, "server_bind", bloqueada)
    with pytest.raises(PermissionError):
        web.Servidor(("127.0.0.1", 0), _cfg(), None)


def test_porta_ocupada_levanta_oserror() -> None:
    ocupante = socket.socket()
    ocupante.bind(("127.0.0.1", 0))
    ocupante.listen()
    porta = ocupante.getsockname()[1]
    try:
        with pytest.raises(OSError):
            web.Servidor(("127.0.0.1", porta), _cfg(), None)
    finally:
        ocupante.close()


def test_main_cai_para_uma_porta_livre_quando_a_padrao_esta_bloqueada(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """O caso do Windows: a 8765 cai em faixa reservada (WinError 10013)."""
    bind_original = http.server.HTTPServer.server_bind
    portas: list[int] = []

    def bind(self: http.server.HTTPServer) -> None:
        portas.append(self.server_address[1])
        if self.server_address[1] == web.PORTA_PADRAO:
            raise PermissionError(10013, "acesso ao soquete proibido")
        bind_original(self)

    iniciou = threading.Event()

    def sem_servir(self: web.Servidor, *a: object, **k: object) -> None:
        iniciou.set()
        raise KeyboardInterrupt

    monkeypatch.setattr(http.server.HTTPServer, "server_bind", bind)
    monkeypatch.setattr(web.Servidor, "serve_forever", sem_servir)
    monkeypatch.setattr(web, "load_config", _cfg)
    web.main(["--nao-abrir", "--sem-historico"])
    assert iniciou.is_set()
    assert portas[:2] == [web.PORTA_PADRAO, 0]
    assert "tentando uma porta livre" in capsys.readouterr().err
