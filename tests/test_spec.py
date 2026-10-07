"""Verificador de contrato (docnuvem-spec)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from docnuvem_tester.spec import COBERTOS, CORPOS, comparar, main, relatorio_texto

FIXTURE = Path(__file__).parent / "fixtures" / "openapi_resumo.json"


def spec_igual() -> dict[str, Any]:
    """Um OpenAPI sintético que bate exatamente com o que a ferramenta conhece."""
    paths: dict[str, Any] = {}
    for (metodo, caminho), params in COBERTOS.items():
        paths.setdefault(caminho, {})[metodo.lower()] = {
            "parameters": [
                {"name": n, "in": "path" if "{" + n + "}" in caminho else "query"}
                for n in sorted(params)
            ]
        }
    schemas = {n: {"properties": {k: {} for k in props}} for n, props in CORPOS.items()}
    return {"info": {"version": "1.0"}, "paths": paths, "components": {"schemas": schemas}}


def test_contrato_real_de_2026_10_07_bate_com_o_que_a_ferramenta_conhece() -> None:
    spec = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rel = comparar(spec)
    assert rel["ok"], relatorio_texto(rel)


def test_sintetico_em_dia() -> None:
    assert comparar(spec_igual())["ok"] is True


def test_endpoint_novo() -> None:
    spec = spec_igual()
    spec["paths"]["/api/relatorios"] = {"get": {"parameters": []}}
    rel = comparar(spec)
    assert rel["ok"] is False
    assert rel["endpointsNovos"] == ["GET /api/relatorios"]


def test_sincronizador_e_ignorado() -> None:
    spec = spec_igual()
    spec["paths"]["/api/sync/tree"] = {"post": {"parameters": []}}
    assert comparar(spec)["ok"] is True


def test_endpoint_que_sumiu() -> None:
    spec = spec_igual()
    del spec["paths"]["/api/diretorios"]
    assert comparar(spec)["endpointsSumidos"] == ["GET /api/diretorios"]


def test_parametro_novo_e_removido() -> None:
    spec = spec_igual()
    params = spec["paths"]["/api/documentos"]["get"]["parameters"]
    params.append({"name": "ordenar", "in": "query"})
    params[:] = [p for p in params if p["name"] != "tamanho"]
    d = comparar(spec)["parametros"]["GET /api/documentos"]
    assert d == {"novos": ["ordenar"], "removidos": ["tamanho"]}


def test_cabecalho_authorization_nao_conta_como_parametro() -> None:
    spec = spec_igual()
    spec["paths"]["/api/modelos"]["get"]["parameters"].append(
        {"name": "Authorization", "in": "header"}
    )
    assert comparar(spec)["ok"] is True


def test_propriedade_nova_no_corpo() -> None:
    spec = spec_igual()
    spec["components"]["schemas"]["SignatarioAssinaturaRequest"]["properties"]["biometria"] = {}
    assert comparar(spec)["corpos"]["SignatarioAssinaturaRequest"]["novos"] == ["biometria"]


def test_esquema_que_sumiu() -> None:
    spec = spec_igual()
    del spec["components"]["schemas"]["PosicaoAssinaturaRequest"]
    assert "PosicaoAssinaturaRequest" in comparar(spec)["corpos"]


def test_relatorio_texto_lista_as_novidades() -> None:
    spec = spec_igual()
    spec["paths"]["/api/relatorios"] = {"get": {"parameters": []}}
    texto = relatorio_texto(comparar(spec))
    assert "GET /api/relatorios" in texto
    assert texto.startswith("A API mudou")


def test_cli_com_arquivo_em_dia(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    arq = tmp_path / "spec.json"
    arq.write_text(json.dumps(spec_igual()), encoding="utf-8")
    with pytest.raises(SystemExit) as saida:
        main(["--arquivo", str(arq)])
    assert saida.value.code == 0
    assert "Contrato em dia" in capsys.readouterr().out


def test_cli_com_mudanca_sai_com_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = copy.deepcopy(spec_igual())
    spec["paths"]["/api/novo"] = {"post": {"parameters": []}}
    arq = tmp_path / "spec.json"
    arq.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(SystemExit) as saida:
        main(["--arquivo", str(arq), "--json"])
    assert saida.value.code == 1
    assert json.loads(capsys.readouterr().out)["endpointsNovos"] == ["POST /api/novo"]


def test_cli_arquivo_ilegivel_sai_com_2(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as saida:
        main(["--arquivo", str(tmp_path / "nao-existe.json")])
    assert saida.value.code == 2
