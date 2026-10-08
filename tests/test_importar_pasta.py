"""Importar uma pasta do computador para o Docnuvem."""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from docnuvem_tester import importar_pasta
from docnuvem_tester.importar_pasta import ErroImportacao, destino_da_api, validar_origem, varrer

from .conftest import App, Upstream

IMPORTAR = ("POST", "/importar")


def _arvore(raiz: Path) -> Path:
    """Uma pasta de cliente típica, com lixo no meio."""
    origem = raiz / "cliente"
    for sub in ("Clientes/2024", ".oculta", "$RECYCLE.BIN"):
        (origem / sub).mkdir(parents=True)
    arquivos = {
        "contrato.pdf": b"%PDF contrato",
        "vazio.pdf": b"",
        "Thumbs.db": b"lixo",
        "~$rascunho.docx": b"temp",
        "programa.exe": b"MZ",
        "semextensao": b"x",
        "Clientes/ana.pdf": b"%PDF ana",
        "Clientes/2024/maio.xlsx": b"planilha",
        ".oculta/segredo.pdf": b"nao",
        "$RECYCLE.BIN/lixo.pdf": b"nao",
    }
    for rel, conteudo in arquivos.items():
        (origem / rel).write_bytes(conteudo)
    return origem


def _enviadas(upstream: Upstream) -> list[dict[str, Any]]:
    return [c for c in upstream.recebidas if (c["method"], c["rota"]) == IMPORTAR]


def _consulta(chamada: dict[str, Any]) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(chamada["path"]).query).items()}


# ------------------------------------------------------------------ varredura ---


def test_varrer_separa_o_que_vale_do_que_nao(tmp_path: Path) -> None:
    origem = _arvore(tmp_path)
    ok, ignorados = varrer(origem, subpastas=True)
    assert [(a.partes, a.nome) for a in ok] == [
        ((), "contrato.pdf"),
        (("Clientes",), "ana.pdf"),
        (("Clientes", "2024"), "maio.xlsx"),
    ]
    motivos = {i["arquivo"].replace("\\", "/"): i["motivo"] for i in ignorados}
    assert motivos == {
        "vazio.pdf": "arquivo vazio",
        "programa.exe": "extensão não aceita (.exe)",
        "semextensao": "sem extensão",
    }  # lixo do sistema, temporários, pastas ocultas e a lixeira nem entram na conta


def test_varrer_sem_subpastas_so_olha_a_raiz(tmp_path: Path) -> None:
    ok, _ = varrer(_arvore(tmp_path), subpastas=False)
    assert [a.nome for a in ok] == ["contrato.pdf"]


def test_varrer_ignora_arquivo_grande_demais(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(importar_pasta, "MAX_BYTES_ARQUIVO", 10)
    (tmp_path / "grande.pdf").write_bytes(b"x" * 50)
    (tmp_path / "ok.pdf").write_bytes(b"x")
    ok, ignorados = varrer(tmp_path, subpastas=False)
    assert [a.nome for a in ok] == ["ok.pdf"]
    assert ignorados[0]["motivo"].startswith("maior que")


def test_varrer_limite_de_arquivos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importar_pasta, "MAX_ARQUIVOS", 2)
    for i in range(3):
        (tmp_path / f"a{i}.pdf").write_bytes(b"x")
    with pytest.raises(ErroImportacao, match="mais de 2 arquivos"):
        varrer(tmp_path, subpastas=False)


def test_validar_origem(tmp_path: Path) -> None:
    assert validar_origem(str(tmp_path)) == tmp_path
    for ruim, trecho in [("", "Escolha"), ("relativa/x", "caminho completo")]:
        with pytest.raises(ErroImportacao, match=trecho):
            validar_origem(ruim)
    with pytest.raises(ErroImportacao, match="não existe"):
        validar_origem(str(tmp_path / "nao-existe"))


@pytest.mark.parametrize(
    ("pai", "pasta", "partes", "esperado"),
    [
        ("", "Cliente X", (), ("", "Cliente X")),
        (
            "/Meus documentos/Contratos",
            "Cliente X",
            (),
            ("/Meus documentos/Contratos", "Cliente X"),
        ),
        ("", "Cliente X", ("2024",), ("/Meus documentos/Cliente X", "2024")),
        ("", "Cliente X", ("A", "B"), ("/Meus documentos/Cliente X/A", "B")),
        ("/Raiz/", "/Cliente/", ("A", "B", "C"), ("/Raiz/Cliente/A/B", "C")),
    ],
)
def test_destino_da_api(
    pai: str, pasta: str, partes: tuple[str, ...], esperado: tuple[str, str]
) -> None:
    assert destino_da_api(pai, pasta, partes) == esperado


# --------------------------------------------------------------------- ensaio ---


def _corpo(origem: Path, /, **extra: Any) -> dict[str, Any]:
    return {
        "perfil": "cliente1",
        "origem": str(origem),
        "subpastas": True,
        "pastaPai": "",
        "pasta": "Cliente X",
        "intervaloMs": 0,
        "confirmarEnvio": True,
        **extra,
    }


def test_ensaio_mostra_o_que_seria_enviado_sem_chamar_a_api(
    app: App, upstream: Upstream, tmp_path: Path
) -> None:
    origem = _arvore(tmp_path)
    r = app.http.post("/_importar/ensaio", json=_corpo(origem)).json()
    assert r["ok"] is True
    assert (r["arquivos"], r["aEnviar"], r["jaEnviados"], r["nIgnorados"]) == (3, 3, 0, 3)
    assert r["pastas"] == 3
    assert r["porExtensao"] == {"pdf": 2, "xlsx": 1}
    assert r["bytes"] == len(b"%PDF contrato") + len(b"%PDF ana") + len(b"planilha")
    destinos = {(a["arquivo"].replace("\\", "/"), a["pastaPai"], a["pasta"]) for a in r["amostra"]}
    assert ("Clientes/2024/maio.xlsx", "/Meus documentos/Cliente X/Clientes", "2024") in destinos
    assert upstream.recebidas == []  # o ensaio nunca fala com a API


@pytest.mark.parametrize(
    ("campos", "trecho"),
    [
        ({"pasta": ""}, "pasta de destino"),
        ({"origem": ""}, "Escolha"),
        ({"origem": "relativa"}, "caminho completo"),
    ],
)
def test_ensaio_recusa_pedido_invalido(
    app: App, tmp_path: Path, campos: dict[str, Any], trecho: str
) -> None:
    r = app.http.post("/_importar/ensaio", json=_corpo(_arvore(tmp_path), **campos))
    assert r.status_code == 400
    assert trecho in r.json()["erro"]


def test_ensaio_perfil_desconhecido(app: App, tmp_path: Path) -> None:
    r = app.http.post("/_importar/ensaio", json=_corpo(_arvore(tmp_path), perfil="ninguem"))
    assert r.status_code == 404


# ---------------------------------------------------------------------- envio ---


def _iniciar(app: App, origem: Path, **extra: Any) -> dict[str, Any]:
    r = app.http.post("/_importar/iniciar", json=_corpo(origem, **extra))
    return dict(r.json()) | {"_status": r.status_code}


def _esperar(app: App, id_: str, limite: float = 15.0) -> dict[str, Any]:
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        dado = dict(app.http.get(f"/_importar/{id_}").json())
        if dado["estado"] != "executando":
            return dado
        time.sleep(0.03)
    raise AssertionError("a importação não terminou a tempo")


def _importar(app: App, origem: Path, **extra: Any) -> dict[str, Any]:
    r = _iniciar(app, origem, **extra)
    assert r["ok"] is True, r
    return _esperar(app, r["id"])


def test_envia_os_arquivos_para_as_pastas_certas(
    app: App, upstream: Upstream, tmp_path: Path
) -> None:
    upstream.respostas[IMPORTAR] = (200, '{"documentoId": 7}')
    origem = _arvore(tmp_path)
    fim = _importar(app, origem, tipo="Contrato", pastaPai="/Meus documentos/Clientes")
    assert (fim["estado"], fim["total"], fim["enviados"], fim["nFalhas"]) == ("concluido", 3, 3, 0)
    por_nome = {_consulta(c)["nomeArquivo"]: c for c in _enviadas(upstream)}
    raiz = por_nome["contrato.pdf"]
    assert _consulta(raiz) | {"instancia": "x"} == {
        "instancia": "x",
        "nomeArquivo": "contrato.pdf",
        "nomePasta": "Cliente X",
        "nomePastaPai": "/Meus documentos/Clientes",
        "tipo": "Contrato",
    }
    assert raiz["ct"].startswith("multipart/form-data")
    assert b"%PDF contrato" in raiz["corpo"] and b'filename="contrato.pdf"' in raiz["corpo"]
    fundo = _consulta(por_nome["maio.xlsx"])
    assert fundo["nomePasta"] == "2024"
    assert fundo["nomePastaPai"] == "/Meus documentos/Clientes/Cliente X/Clientes"


def test_sem_subpastas_envia_so_a_raiz(app: App, upstream: Upstream, tmp_path: Path) -> None:
    fim = _importar(app, _arvore(tmp_path), subpastas=False)
    assert fim["total"] == 1
    assert [_consulta(c)["nomeArquivo"] for c in _enviadas(upstream)] == ["contrato.pdf"]


def test_parametros_vazios_nao_sao_enviados(app: App, upstream: Upstream, tmp_path: Path) -> None:
    _importar(app, _arvore(tmp_path), subpastas=False)
    consulta = _consulta(_enviadas(upstream)[0])
    assert "nomePastaPai" not in consulta and "tipo" not in consulta  # a API recusa vazio


def test_segundo_envio_pula_o_que_ja_foi(app: App, upstream: Upstream, tmp_path: Path) -> None:
    origem = _arvore(tmp_path)
    _importar(app, origem)
    assert len(_enviadas(upstream)) == 3
    ens = app.http.post("/_importar/ensaio", json=_corpo(origem)).json()
    assert (ens["jaEnviados"], ens["aEnviar"]) == (3, 0)
    r = _iniciar(app, origem)
    assert r["_status"] == 400
    assert "todos os arquivos já foram enviados" in r["erro"]
    (origem / "novo.pdf").write_bytes(b"%PDF novo")  # só o que chegou depois é enviado
    fim = _importar(app, origem)
    assert (fim["total"], fim["puladosAntes"]) == (1, 3)
    assert len(_enviadas(upstream)) == 4


def test_pular_enviados_desligado_reenvia_tudo(
    app: App, upstream: Upstream, tmp_path: Path
) -> None:
    origem = _arvore(tmp_path)
    _importar(app, origem)
    fim = _importar(app, origem, pularEnviados=False)
    assert fim["enviados"] == 3
    assert len(_enviadas(upstream)) == 6


def test_arquivo_alterado_conta_como_novo(app: App, upstream: Upstream, tmp_path: Path) -> None:
    origem = _arvore(tmp_path)
    _importar(app, origem, subpastas=False)
    (origem / "contrato.pdf").write_bytes(b"%PDF contrato REVISADO")
    fim = _importar(app, origem, subpastas=False)
    assert fim["enviados"] == 1


def test_o_mesmo_arquivo_em_outro_destino_nao_e_pulado(
    app: App, upstream: Upstream, tmp_path: Path
) -> None:
    origem = _arvore(tmp_path)
    _importar(app, origem, subpastas=False, pasta="Cliente X")
    assert _importar(app, origem, subpastas=False, pasta="Cliente Y")["enviados"] == 1


def test_sem_historico_nao_ha_memoria(
    app_sem_historico: App, upstream: Upstream, tmp_path: Path
) -> None:
    origem = _arvore(tmp_path)
    ens = app_sem_historico.http.post("/_importar/ensaio", json=_corpo(origem)).json()
    assert ens["jaEnviados"] == 0
    _importar(app_sem_historico, origem, perfil="cliente1")
    assert _importar(app_sem_historico, origem, perfil="cliente1")["enviados"] == 3


def test_falha_isolada_nao_derruba_o_resto(app: App, upstream: Upstream, tmp_path: Path) -> None:
    upstream.dinamicas[IMPORTAR] = lambda q: (
        (422, '{"retorno": "Tipo inválido"}') if q["nomeArquivo"][0] == "ana.pdf" else (200, "{}")
    )
    fim = _importar(app, _arvore(tmp_path))
    assert (fim["enviados"], fim["nFalhas"]) == (2, 1)
    assert fim["falhas"][0]["arquivo"].replace("\\", "/") == "Clientes/ana.pdf"
    assert "HTTP 422: Tipo inválido" in fim["falhas"][0]["erro"]
    assert len(_enviadas(upstream)) == 3  # uma tentativa por arquivo, sem repetição


def test_falha_nao_e_anotada_como_enviada(app: App, upstream: Upstream, tmp_path: Path) -> None:
    origem = _arvore(tmp_path)
    upstream.respostas[IMPORTAR] = (500, "")
    _importar(app, origem, subpastas=False)
    upstream.respostas[IMPORTAR] = (200, "{}")
    assert _importar(app, origem, subpastas=False)["enviados"] == 1  # pode tentar de novo


def test_para_depois_de_falhas_seguidas(app: App, upstream: Upstream, tmp_path: Path) -> None:
    origem = tmp_path / "muitos"
    origem.mkdir()
    for i in range(9):
        (origem / f"a{i}.pdf").write_bytes(b"x")
    upstream.respostas[IMPORTAR] = (500, "")
    fim = _importar(app, origem, subpastas=False)
    assert fim["estado"] == "erro"
    assert "5 falhas seguidas" in fim["erro"]
    assert len(_enviadas(upstream)) == 5


def test_falha_de_conexao_avisa_que_pode_ter_chegado(app: App, tmp_path: Path) -> None:
    origem = tmp_path / "pasta"
    origem.mkdir()
    (origem / "a.pdf").write_bytes(b"x")
    fim = _importar(app, origem, perfil="fora", subpastas=False)
    assert fim["nFalhas"] == 1
    assert "Não sei se o arquivo chegou" in fim["falhas"][0]["erro"]


def test_perfil_protegido(app: App, upstream: Upstream, tmp_path: Path) -> None:
    origem = _arvore(tmp_path)
    assert _iniciar(app, origem, perfil="leitura")["_status"] == 403
    assert _iniciar(app, origem, perfil="prod")["_status"] == 428
    assert _iniciar(app, origem, confirmarEnvio=False)["_status"] == 428
    assert _enviadas(upstream) == []
    assert _importar(app, origem, perfil="prod", confirmarProtecao=True)["enviados"] == 3


def test_cancelar(app: App, upstream: Upstream, tmp_path: Path) -> None:
    origem = tmp_path / "pasta"
    origem.mkdir()
    for i in range(6):
        (origem / f"a{i}.pdf").write_bytes(b"x")
    r = _iniciar(app, origem, subpastas=False, intervaloMs=2000)
    time.sleep(0.3)
    assert app.http.post(f"/_importar/{r['id']}/cancelar").json() == {"ok": True}
    inicio = time.monotonic()
    fim = _esperar(app, r["id"])
    assert fim["estado"] == "cancelado"
    assert time.monotonic() - inicio < 1.5  # acordou do intervalo
    assert fim["enviados"] < 6


def test_uma_importacao_por_vez(app: App, upstream: Upstream, tmp_path: Path) -> None:
    origem = tmp_path / "pasta"
    origem.mkdir()
    for i in range(3):
        (origem / f"a{i}.pdf").write_bytes(b"x")
    primeiro = _iniciar(app, origem, subpastas=False, intervaloMs=1500)
    segundo = _iniciar(app, origem, subpastas=False, pularEnviados=False)
    assert segundo["_status"] == 409
    app.http.post(f"/_importar/{primeiro['id']}/cancelar")
    _esperar(app, primeiro["id"])


def test_intervalo_invalido(app: App, tmp_path: Path) -> None:
    assert _iniciar(app, _arvore(tmp_path), intervaloMs="abc")["_status"] == 400


def test_relatorio_csv(app: App, upstream: Upstream, tmp_path: Path) -> None:
    upstream.dinamicas[IMPORTAR] = lambda q: (
        (500, "") if q["nomeArquivo"][0] == "ana.pdf" else (200, '{"documentoId": 9}')
    )
    fim = _importar(app, _arvore(tmp_path))
    r = app.http.get(f"/_importar/{fim['id']}/relatorio.csv")
    assert "attachment" in r.headers["content-disposition"]
    linhas = list(csv.reader(r.content.decode("utf-8-sig").splitlines(), delimiter=";"))
    assert linhas[0] == [
        "arquivo",
        "pastaPai",
        "pasta",
        "situacao",
        "http",
        "documentoId",
        "detalhe",
    ]
    situacoes = {linha[0].replace("\\", "/"): linha[3] for linha in linhas[1:]}
    assert situacoes == {
        "contrato.pdf": "enviado",
        "Clientes/ana.pdf": "falhou",
        "Clientes/2024/maio.xlsx": "enviado",
    }
    assert linhas[1][5] == "9"


def test_desconhecida(app: App) -> None:
    assert app.http.get("/_importar/nada").status_code == 404
    assert app.http.get("/_importar/nada/relatorio.csv").status_code == 404
    assert app.http.post("/_importar/nada/cancelar").status_code == 404


def test_exige_origem_da_pagina(app: App, tmp_path: Path) -> None:
    ruim = {"Origin": "http://evil.example"}
    corpo = _corpo(_arvore(tmp_path))
    assert app.http.post("/_importar/ensaio", json=corpo, headers=ruim).status_code == 403
    assert app.http.post("/_importar/iniciar", json=corpo, headers=ruim).status_code == 403
    assert app.http.post("/_importar/x/cancelar", headers=ruim).status_code == 403
