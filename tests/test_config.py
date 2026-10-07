from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docnuvem_tester.config import ConfigError, config_path, load_config, parse_config


def valido() -> dict[str, Any]:
    return {
        "perfis": {
            "cliente1": {"instancia": "cliente1", "baseUrl": "http://x:8083", "token": "abc123"},
            "cliente2": {"instancia": "cliente2", "baseUrl": "http://x:8083", "token": "def456"},
        },
        "perfilPadrao": "cliente2",
    }


def test_config_valida() -> None:
    cfg = parse_config(valido())
    assert list(cfg.perfis) == ["cliente1", "cliente2"]
    assert cfg.perfilPadrao == "cliente2"
    assert cfg.perfis["cliente1"].baseUrl == "http://x:8083"


def test_token_perde_espacos_e_quebra_de_linha() -> None:
    dados = valido()
    dados["perfis"]["cliente1"]["token"] = "  abc123\n"
    assert parse_config(dados).perfis["cliente1"].token == "abc123"


@pytest.mark.parametrize(
    ("mexer", "trecho"),
    [
        (lambda d: d.pop("perfis"), "perfis"),
        (lambda d: d.update(perfis={}), "perfis"),
        (lambda d: d["perfis"]["cliente1"].pop("token"), "token"),
        (lambda d: d["perfis"]["cliente1"].update(token="  "), "token"),
        (lambda d: d["perfis"]["cliente1"].update(baseUrl=3), "baseUrl"),
        (lambda d: d["perfis"].update(cliente1="texto"), "objeto"),
        (lambda d: d.update(perfilPadrao="nao-existe"), "nao-existe"),
        (lambda d: d.pop("perfilPadrao"), "perfilPadrao"),
    ],
)
def test_config_invalida(mexer: Any, trecho: str) -> None:
    dados = valido()
    mexer(dados)
    with pytest.raises(ConfigError, match=trecho):
        parse_config(dados)


def test_raiz_precisa_ser_objeto() -> None:
    with pytest.raises(ConfigError, match="objeto JSON"):
        parse_config([])


def test_arquivo_inexistente(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="não encontrado"):
        load_config(tmp_path / "nada.json")


def test_json_malformado(tmp_path: Path) -> None:
    arq = tmp_path / "config.json"
    arq.write_text("{ não é json", encoding="utf-8")
    with pytest.raises(ConfigError, match="malformado"):
        load_config(arq)


def test_le_do_arquivo_e_respeita_variavel_de_ambiente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arq = tmp_path / "meu.json"
    arq.write_text(json.dumps(valido()), encoding="utf-8")
    monkeypatch.setenv("DOCNUVEM_TESTER_CONFIG", str(arq))
    assert config_path() == arq
    assert load_config().perfilPadrao == "cliente2"


def test_load_config_aceita_arquivo_com_bom(tmp_path: Path) -> None:
    arq = tmp_path / "config.json"
    arq.write_bytes(b"\xef\xbb\xbf" + json.dumps(valido()).encode("utf-8"))
    assert load_config(arq).perfilPadrao == "cliente2"


def test_config_path_no_executavel_procura_ao_lado_do_exe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe_dir, outra = tmp_path / "exe", tmp_path / "outra"
    exe_dir.mkdir()
    outra.mkdir()
    (exe_dir / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("DOCNUVEM_TESTER_CONFIG", raising=False)
    monkeypatch.chdir(outra)
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(exe_dir / "docnuvem-web.exe"))
    assert config_path() == exe_dir / "config.json"


def test_config_path_prefere_a_pasta_atual_mesmo_no_executavel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe_dir, aqui = tmp_path / "exe", tmp_path / "aqui"
    exe_dir.mkdir()
    aqui.mkdir()
    (exe_dir / "config.json").write_text("{}", encoding="utf-8")
    (aqui / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("DOCNUVEM_TESTER_CONFIG", raising=False)
    monkeypatch.chdir(aqui)
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(exe_dir / "docnuvem-web.exe"))
    assert config_path() == aqui / "config.json"


def test_config_path_fora_do_executavel_ignora_a_pasta_do_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOCNUVEM_TESTER_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    assert config_path() == tmp_path / "config.json"
