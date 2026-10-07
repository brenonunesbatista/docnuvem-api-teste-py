"""Baixar todos os documentos de uma pasta para o computador."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

import pytest

from docnuvem_tester import download, web
from docnuvem_tester.download import ErroDownload, nome_seguro, validar_destino

from .conftest import TOKEN_OK, App, Upstream

# ------------------------------------------------------------ nomes e destino ---


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("contrato.pdf", "contrato.pdf"),
        ("a/b\\c:d*e?.pdf", "a_b_c_d_e_.pdf"),
        ("..", "doc"),
        (".", "doc"),
        ("   ", "doc"),
        ("nome com ponto final.", "nome com ponto final"),
        ("CON", "_CON"),
        ("con.txt", "_con.txt"),
        ("../../etc/passwd", ".._.._etc_passwd"),
    ],
)
def test_nome_seguro(entrada: str, esperado: str) -> None:
    assert nome_seguro(entrada, "doc") == esperado


def test_nome_seguro_corta_mantendo_a_extensao() -> None:
    longo = nome_seguro("x" * 300 + ".pdf", "doc")
    assert len(longo) <= 150
    assert longo.endswith(".pdf")


def test_destino_precisa_ser_absoluto(tmp_path: Path) -> None:
    with pytest.raises(ErroDownload, match="caminho completo"):
        validar_destino("pasta/relativa")
    with pytest.raises(ErroDownload, match="Escolha"):
        validar_destino("  ")
    assert validar_destino(str(tmp_path / "nova" / "sub")).is_dir()  # cria se preciso


def test_destino_nao_gravavel(tmp_path: Path) -> None:
    arquivo = tmp_path / "arquivo.txt"
    arquivo.write_text("x")
    with pytest.raises(ErroDownload, match="Não consigo gravar"):
        validar_destino(str(arquivo / "dentro"))


# --------------------------------------------------------------- servidor ---


def _docs(*itens: tuple[int, str, int]) -> tuple[int, str]:
    docs = [{"documentoId": i, "nomeArquivo": n, "diretorioId": d} for i, n, d in itens]
    return 200, json.dumps({"total": len(docs), "temMais": False, "documentos": docs})


def _preparar(upstream: Upstream, *itens: tuple[int, str, int]) -> None:
    upstream.respostas[("GET", "/api/documentos")] = _docs(*itens)
    for i, nome, _ in itens:
        link = {"urlDownload": f"{upstream.base}/arquivo/{i}", "expiraEm": "15 min"}
        upstream.respostas[("GET", f"/api/documento/{i}/download")] = (200, json.dumps(link))
        upstream.respostas[("GET", f"/arquivo/{i}")] = (200, f"conteudo de {nome}")


def _iniciar(app: App, destino: Path, /, **extra: Any) -> dict[str, Any]:
    corpo = {
        "perfil": "cliente1",
        "diretorioId": 5,
        "destino": str(destino),
        "subpastas": False,
        "estrutura": False,
        "conflito": "renomear",
        "ensaio": False,
        **extra,
    }
    r = app.http.post("/_baixar/iniciar", json=corpo)
    return dict(r.json()) | {"_status": r.status_code}


def _esperar(app: App, id_: str, limite: float = 15.0) -> dict[str, Any]:
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        dado = dict(app.http.get(f"/_baixar/{id_}").json())
        if dado["estado"] not in ("listando", "baixando"):
            return dado
        time.sleep(0.05)
    raise AssertionError("o download não terminou a tempo")


def _baixar(app: App, destino: Path, **extra: Any) -> dict[str, Any]:
    r = _iniciar(app, destino, **extra)
    assert r["ok"] is True, r
    return _esperar(app, r["id"])


def test_baixa_todos_os_arquivos(app: App, upstream: Upstream, tmp_path: Path) -> None:
    _preparar(upstream, (1, "contrato.pdf", 5), (2, "anexo.txt", 5))
    fim = _baixar(app, tmp_path / "saida")
    assert fim["estado"] == "concluido"
    assert (fim["total"], fim["baixados"], fim["nFalhas"]) == (2, 2, 0)
    assert (tmp_path / "saida" / "contrato.pdf").read_text() == "conteudo de contrato.pdf"
    assert (tmp_path / "saida" / "anexo.txt").read_text() == "conteudo de anexo.txt"
    assert fim["bytes"] == len("conteudo de contrato.pdf") + len("conteudo de anexo.txt")
    assert not list((tmp_path / "saida").glob("*.part"))


def test_link_temporario_e_baixado_sem_o_token(
    app: App, upstream: Upstream, tmp_path: Path
) -> None:
    _preparar(upstream, (1, "a.pdf", 5))
    _baixar(app, tmp_path / "saida")
    pedidos = {c["rota"]: c for c in upstream.recebidas}
    assert pedidos["/api/documento/1/download"]["auth"] == f"Bearer {TOKEN_OK}"
    assert pedidos["/arquivo/1"]["auth"] is None  # nunca manda o token para o link temporário


def test_so_faz_leituras_na_api(app: App, upstream: Upstream, tmp_path: Path) -> None:
    _preparar(upstream, (1, "a.pdf", 5))
    _baixar(app, tmp_path / "saida")
    assert {c["method"] for c in upstream.recebidas} == {"GET"}


def test_ensaio_so_conta_e_nao_grava(app: App, upstream: Upstream, tmp_path: Path) -> None:
    _preparar(upstream, (1, "a.pdf", 5), (2, "b.pdf", 6))
    saida = tmp_path / "saida"
    fim = _baixar(app, saida, ensaio=True)
    assert fim["estado"] == "concluido"
    assert fim["ensaio"] is True
    assert (fim["total"], fim["pastas"], fim["baixados"]) == (2, 2, 0)
    assert fim["amostra"] == ["a.pdf", "b.pdf"]
    assert not saida.exists()
    assert not any("/download" in c["rota"] for c in upstream.recebidas)


def test_nomes_repetidos_nao_se_sobrescrevem(app: App, upstream: Upstream, tmp_path: Path) -> None:
    _preparar(upstream, (1, "contrato.pdf", 5), (2, "contrato.pdf", 5))
    fim = _baixar(app, tmp_path / "saida")
    assert fim["baixados"] == 2
    nomes = sorted(p.name for p in (tmp_path / "saida").glob("*.pdf"))
    assert nomes == ["contrato (2).pdf", "contrato.pdf"]


def test_arquivo_existente_e_renomeado_ou_pulado(
    app: App, upstream: Upstream, tmp_path: Path
) -> None:
    _preparar(upstream, (1, "a.pdf", 5))
    saida = tmp_path / "saida"
    saida.mkdir()
    (saida / "a.pdf").write_text("antigo")
    fim = _baixar(app, saida, conflito="pular")
    assert (fim["pulados"], fim["baixados"]) == (1, 0)
    assert (saida / "a.pdf").read_text() == "antigo"  # nunca sobrescreve
    fim = _baixar(app, saida, conflito="renomear")
    assert (fim["pulados"], fim["baixados"]) == (0, 1)
    assert (saida / "a.pdf").read_text() == "antigo"
    assert (saida / "a (1).pdf").read_text() == "conteudo de a.pdf"


def test_nome_perigoso_fica_dentro_do_destino(app: App, upstream: Upstream, tmp_path: Path) -> None:
    _preparar(upstream, (1, "../../fora.txt", 5))
    saida = tmp_path / "saida"
    fim = _baixar(app, saida)
    assert fim["baixados"] == 1
    assert not (tmp_path / "fora.txt").exists()
    assert [p.name for p in saida.iterdir() if p.suffix == ".txt"] == [".._.._fora.txt"]


def test_refaz_a_arvore_de_pastas(app: App, upstream: Upstream, tmp_path: Path) -> None:
    _preparar(upstream, (1, "raiz.pdf", 5), (2, "filho.pdf", 6), (3, "neto.pdf", 7))
    upstream.respostas[("GET", "/api/diretorios")] = (
        200,
        json.dumps(
            {
                "temMais": False,
                "diretorios": [
                    {"diretorioId": 5, "caminho": "/Meus documentos/Clientes"},
                    {"diretorioId": 6, "caminho": "/Meus documentos/Clientes/2024"},
                    {"diretorioId": 7, "caminho": "/Meus documentos/Clientes/2024/Maio:1"},
                ],
            }
        ),
    )
    saida = tmp_path / "saida"
    fim = _baixar(app, saida, subpastas=True, estrutura=True)
    assert fim["baixados"] == 3
    assert (saida / "raiz.pdf").is_file()
    assert (saida / "2024" / "filho.pdf").is_file()
    assert (saida / "2024" / "Maio_1" / "neto.pdf").is_file()  # nome de pasta sanitizado


def test_sem_estrutura_tudo_vai_para_a_mesma_pasta(
    app: App, upstream: Upstream, tmp_path: Path
) -> None:
    _preparar(upstream, (1, "a.pdf", 5), (2, "b.pdf", 6))
    saida = tmp_path / "saida"
    _baixar(app, saida, subpastas=True, estrutura=False)
    assert sorted(p.name for p in saida.glob("*.pdf")) == ["a.pdf", "b.pdf"]


def test_arvore_indisponivel_cai_para_pasta_unica_com_aviso(
    app: App, upstream: Upstream, tmp_path: Path
) -> None:
    _preparar(upstream, (1, "a.pdf", 5), (2, "b.pdf", 6))
    upstream.respostas[("GET", "/api/diretorios")] = (401, "")  # o que a API real faz hoje
    saida = tmp_path / "saida"
    fim = _baixar(app, saida, subpastas=True, estrutura=True)
    assert fim["estado"] == "concluido"
    assert fim["baixados"] == 2
    assert "mesma pasta" in fim["aviso"]
    assert sorted(p.name for p in saida.glob("*.pdf")) == ["a.pdf", "b.pdf"]


def test_pagina_todas_as_paginas(
    app: App, upstream: Upstream, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(download, "TAMANHO_PAGINA", 2)
    todos = [(i, f"d{i}.pdf", 5) for i in range(1, 6)]
    _preparar(upstream, *todos)

    def pagina(q: dict[str, list[str]]) -> tuple[int, str]:
        n, tam = int(q["pagina"][0]), int(q["tamanho"][0])
        fatia = todos[n * tam : (n + 1) * tam]
        docs = [{"documentoId": i, "nomeArquivo": nm, "diretorioId": d} for i, nm, d in fatia]
        corpo = {"total": len(todos), "temMais": (n + 1) * tam < len(todos), "documentos": docs}
        return 200, json.dumps(corpo)

    upstream.dinamicas[("GET", "/api/documentos")] = pagina
    fim = _baixar(app, tmp_path / "saida")
    assert (fim["total"], fim["baixados"]) == (5, 5)


def test_falha_de_um_arquivo_nao_derruba_os_outros(
    app: App, upstream: Upstream, tmp_path: Path
) -> None:
    _preparar(upstream, (1, "ok.pdf", 5), (2, "ruim.pdf", 5), (3, "ok2.pdf", 5))
    upstream.respostas[("GET", "/api/documento/2/download")] = (500, '{"retorno": "falhou"}')
    fim = _baixar(app, tmp_path / "saida")
    assert fim["estado"] == "concluido"
    assert (fim["baixados"], fim["nFalhas"]) == (2, 1)
    assert fim["falhas"][0]["documentoId"] == 2
    assert "HTTP 500" in fim["falhas"][0]["erro"]


def test_link_que_nao_baixa_conta_como_falha(app: App, upstream: Upstream, tmp_path: Path) -> None:
    _preparar(upstream, (1, "a.pdf", 5))
    upstream.respostas[("GET", "/arquivo/1")] = (404, "")
    saida = tmp_path / "saida"
    fim = _baixar(app, saida)
    assert fim["nFalhas"] == 1
    assert not list(saida.glob("a.pdf*"))  # nada pela metade


def test_link_invalido_e_recusado(app: App, upstream: Upstream, tmp_path: Path) -> None:
    _preparar(upstream, (1, "a.pdf", 5))
    upstream.respostas[("GET", "/api/documento/1/download")] = (
        200,
        '{"urlDownload": "file:///c:/windows/win.ini"}',
    )
    fim = _baixar(app, tmp_path / "saida")
    assert fim["nFalhas"] == 1
    assert "link de download válido" in fim["falhas"][0]["erro"]


def test_erro_ao_listar_termina_com_erro(app: App, upstream: Upstream, tmp_path: Path) -> None:
    upstream.respostas[("GET", "/api/documentos")] = (401, '{"retorno": "Token inválido"}')
    fim = _baixar(app, tmp_path / "saida")
    assert fim["estado"] == "erro"
    assert "HTTP 401" in fim["erro"]


def test_pasta_vazia(app: App, upstream: Upstream, tmp_path: Path) -> None:
    _preparar(upstream)
    fim = _baixar(app, tmp_path / "saida")
    assert (fim["estado"], fim["total"]) == ("concluido", 0)


def test_gera_relatorio_csv(app: App, upstream: Upstream, tmp_path: Path) -> None:
    _preparar(upstream, (1, "a.pdf", 5), (2, "b.pdf", 5))
    upstream.respostas[("GET", "/api/documento/2/download")] = (500, "")
    saida = tmp_path / "saida"
    fim = _baixar(app, saida)
    relatorio = Path(fim["relatorio"])
    assert relatorio.parent == saida
    linhas = list(csv.reader(relatorio.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))
    assert linhas[0] == ["documentoId", "nome", "arquivo_local", "situacao", "erro"]
    assert {(r[0], r[3]) for r in linhas[1:]} == {("1", "baixado"), ("2", "falhou")}


def test_cancelar(app: App, upstream: Upstream, tmp_path: Path) -> None:
    todos = [(i, f"d{i}.pdf", 5) for i in range(1, 30)]
    _preparar(upstream, *todos)

    def devagar(q: dict[str, list[str]]) -> tuple[int, str]:
        time.sleep(0.05)
        return 200, "x"

    for i, _, _ in todos:
        upstream.dinamicas[("GET", f"/arquivo/{i}")] = devagar
    r = _iniciar(app, tmp_path / "saida")
    assert app.http.post(f"/_baixar/{r['id']}/cancelar").json() == {"ok": True}
    fim = _esperar(app, r["id"])
    assert fim["estado"] == "cancelado"
    assert fim["baixados"] < len(todos)


def test_so_um_download_por_vez(app: App, upstream: Upstream, tmp_path: Path) -> None:
    _preparar(upstream, (1, "a.pdf", 5))

    def devagar(q: dict[str, list[str]]) -> tuple[int, str]:
        time.sleep(0.5)
        return 200, "x"

    upstream.dinamicas[("GET", "/arquivo/1")] = devagar
    primeiro = _iniciar(app, tmp_path / "um")
    segundo = _iniciar(app, tmp_path / "dois")
    assert segundo["_status"] == 409
    assert "andamento" in segundo["erro"]
    _esperar(app, primeiro["id"])
    assert _iniciar(app, tmp_path / "tres")["ok"] is True  # liberado depois de terminar


@pytest.mark.parametrize(
    ("campos", "trecho"),
    [
        ({"diretorioId": ""}, "diretorioId"),
        ({"diretorioId": "abc"}, "diretorioId"),
        ({"diretorioId": 0}, "diretorioId"),
        ({"diretorioId": -3}, "diretorioId"),
        ({"destino": ""}, "Escolha"),
        ({"destino": "relativo/x"}, "caminho completo"),
        ({"conflito": "apagar"}, "Conflito"),
    ],
)
def test_pedido_invalido(app: App, tmp_path: Path, campos: dict[str, Any], trecho: str) -> None:
    r = _iniciar(app, tmp_path / "saida", **campos)
    assert r["_status"] == 400
    assert trecho in r["erro"]


def test_perfil_desconhecido_no_download(app: App, tmp_path: Path) -> None:
    assert _iniciar(app, tmp_path, perfil="ninguem")["_status"] == 404


def test_perfil_protegido_pode_baixar(app: App, upstream: Upstream, tmp_path: Path) -> None:
    """Baixar só lê da API, então vale até para o perfil que bloqueia escritas."""
    _preparar(upstream, (1, "a.pdf", 5))
    fim = _baixar(app, tmp_path / "saida", perfil="leitura")
    assert fim["baixados"] == 1


def test_download_desconhecido(app: App) -> None:
    assert app.http.get("/_baixar/naoexiste").status_code == 404
    assert app.http.post("/_baixar/naoexiste/cancelar").status_code == 404


def test_download_exige_origem_da_pagina(app: App, tmp_path: Path) -> None:
    ruim = {"Origin": "http://evil.example"}
    corpo = {"perfil": "cliente1", "diretorioId": 1, "destino": str(tmp_path / "x")}
    assert app.http.post("/_baixar/iniciar", json=corpo, headers=ruim).status_code == 403
    assert app.http.post("/_baixar/abc/cancelar", headers=ruim).status_code == 403
    assert app.http.post("/_pasta/escolher", json={}, headers=ruim).status_code == 403
    assert app.http.post("/_pasta/abrir", json={"id": "x"}, headers=ruim).status_code == 403
    assert app.http.get("/_baixar/abc", headers={"Host": "evil.example"}).status_code == 403
    assert not (tmp_path / "x").exists()


# ------------------------------------------------------- janela de pasta ---


def test_escolher_pasta(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    recebido: list[str] = []
    monkeypatch.setattr(
        web, "escolher_pasta", lambda inicial="": recebido.append(inicial) or "C:\\x"
    )
    r = app.http.post("/_pasta/escolher", json={"inicial": "C:\\ini"}).json()
    assert r == {"ok": True, "caminho": "C:\\x", "cancelado": False}
    assert recebido == ["C:\\ini"]


def test_escolher_pasta_cancelado(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "escolher_pasta", lambda inicial="": None)
    r = app.http.post("/_pasta/escolher", json={}).json()
    assert r == {"ok": True, "caminho": "", "cancelado": True}


def test_escolher_pasta_indisponivel(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    def falha(inicial: str = "") -> str:
        raise ErroDownload("sem janela")

    monkeypatch.setattr(web, "escolher_pasta", falha)
    r = app.http.post("/_pasta/escolher", json={})
    assert r.status_code == 501
    assert r.json()["erro"] == "sem janela"


def test_abrir_pasta_so_do_download_concluido(
    app: App, upstream: Upstream, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    abertas: list[Path] = []
    monkeypatch.setattr(web, "abrir_pasta", abertas.append)
    _preparar(upstream, (1, "a.pdf", 5))
    saida = tmp_path / "saida"
    fim = _baixar(app, saida)
    assert app.http.post("/_pasta/abrir", json={"id": fim["id"]}).json() == {"ok": True}
    assert abertas == [saida]
    # um id inventado (ou o ensaio) nunca abre uma pasta qualquer
    assert app.http.post("/_pasta/abrir", json={"id": "x"}).status_code == 404
    ensaio = _baixar(app, tmp_path, ensaio=True)
    assert app.http.post("/_pasta/abrir", json={"id": ensaio["id"]}).status_code == 404
    assert abertas == [saida]


def test_perfis_sugere_a_pasta_de_downloads(app: App) -> None:
    dados = app.http.get("/_perfis").json()
    assert dados["pastaDownloads"].endswith("Docnuvem")
