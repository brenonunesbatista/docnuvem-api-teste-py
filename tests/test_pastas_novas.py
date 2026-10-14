"""Árvore completa no seletor, criar pasta e excluir a pasta inteira (sincronizador)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from docnuvem_tester import pastas

from .conftest import App, Upstream
from .test_excluir import LISTAGEM, _consulta

OFICIAL = ("GET", "/api/diretorios")
ARVORE = ("POST", "/api/sync/tree")
CRIAR = ("POST", "/api/sync/criarDiretorio")
EXCLUIR_PASTA = ("POST", "/api/sync/excluirDiretorio")


def _pasta(id_: int, nome: str, pai: int | None, caminho: str, lixeira: bool = False) -> dict:
    return {
        "id": id_,
        "nome": nome,
        "diretorioPaiId": pai,
        "caminhoNomesPais": caminho,
        "lixeira": lixeira,
    }


def _doc(id_: int, pasta: int, lixeira: bool = False) -> dict:
    return {"id": id_, "nome": f"d{id_}", "diretorioId": pasta, "lixeira": lixeira}


def _arvore(upstream: Upstream) -> None:
    """Meus documentos > Contratos(10) > 2024(12); Atas(11); lixo(13, na lixeira); Envio(-1)."""
    dado = {
        "root": {"id": 0, "nome": "Meus documentos", "caminho": "/Meus documentos"},
        "diretorios": [
            _pasta(-1, "Envio inteligente", None, "/Envio Inteligente"),
            _pasta(10, "Contratos", 0, "/Meus documentos/Contratos"),
            _pasta(11, "Atas", 0, "/Meus documentos/Atas"),
            _pasta(12, "2024", 10, "/Meus documentos/Contratos/2024"),
            _pasta(13, "velha", 0, "/Meus documentos/velha", lixeira=True),
        ],
        "documentos": [
            _doc(1, 10),
            _doc(2, 12),
            _doc(3, 12),
            _doc(4, 12, lixeira=True),  # na lixeira: não conta
            _doc(5, 11),
        ],
    }
    upstream.respostas[OFICIAL] = (401, "")
    upstream.respostas[ARVORE] = (200, json.dumps(dado))


def _filhos(app: App, pai: int = 0) -> Any:
    return app.http.get(f"/_pastas/filhos?perfil=cliente1&pai={pai}")


def _chamadas(upstream: Upstream, rota: tuple[str, str]) -> list[dict[str, Any]]:
    return [c for c in upstream.recebidas if (c["method"], c["rota"]) == rota]


# ------------------------------------------------------------------ árvore ---


def test_arvore_inteira_e_lida_uma_vez_e_traz_a_contagem(app: App, upstream: Upstream) -> None:
    _arvore(upstream)
    r = _filhos(app).json()
    assert r["ok"] is True and r["fonte"] == "arvore"
    assert [p["nome"] for p in r["pastas"]] == ["Atas", "Contratos"]  # sem lixeira nem "Envio"
    contratos = r["pastas"][1]
    assert (contratos["docs"], contratos["docsTotal"]) == (1, 3)  # 1 direto; 2024 tem 2 válidos
    assert _filhos(app, 10).json()["pastas"][0]["nome"] == "2024"
    assert len(_chamadas(upstream, ARVORE)) == 1  # navegar não bate na API de novo


def test_arvore_vence_com_o_tempo(
    app: App, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arvore(upstream)
    _filhos(app)
    monkeypatch.setattr(pastas, "TTL_ARVORE", -1.0)
    _filhos(app)
    assert len(_chamadas(upstream, ARVORE)) == 2


def test_resposta_estranha_da_arvore_cai_para_o_sincronizador(app: App, upstream: Upstream) -> None:
    upstream.respostas[OFICIAL] = (401, "")
    upstream.respostas[ARVORE] = (200, "{}")
    upstream.respostas[("POST", "/api/sync/listarDiretoriosFilhos")] = (
        200,
        json.dumps({"diretorios": [{"id": 7, "nome": "X", "caminhoNomesPais": "/X"}]}),
    )
    r = _filhos(app).json()
    assert r["fonte"] == "sincronizador" and r["pastas"][0]["id"] == 7


def test_endpoint_oficial_continua_tendo_prioridade(app: App, upstream: Upstream) -> None:
    _arvore(upstream)
    upstream.respostas[OFICIAL] = (
        200,
        json.dumps({"diretorios": [{"diretorioId": 2, "nome": "Z", "caminho": "/Z"}]}),
    )
    assert _filhos(app).json()["fonte"] == "api"
    assert _chamadas(upstream, ARVORE) == []


# ------------------------------------------------------------- criar pasta ---


def _criar(app: App, **extra: Any) -> Any:
    corpo = {"perfil": "cliente1", "paiId": 10, "nome": "Novas", **extra}
    return app.http.post("/_pastas/criar", json=corpo)


def test_cria_a_pasta_com_os_parametros_certos_e_esquece_a_arvore(
    app: App, upstream: Upstream
) -> None:
    _arvore(upstream)
    _filhos(app)
    upstream.respostas[CRIAR] = (200, '{"id": 99, "nome": "Novas"}')
    r = _criar(app, nome="  Novas  ")
    assert r.status_code == 200 and r.json()["ok"] is True
    c = _chamadas(upstream, CRIAR)[0]
    q = _consulta(c)
    assert (q["diretorioPaiId"], q["nome"], q["instancia"]) == ("10", "Novas", "cliente1")
    _filhos(app)
    assert len(_chamadas(upstream, ARVORE)) == 2  # a árvore foi lida de novo


@pytest.mark.parametrize("nome", ["", "   ", "a/b", "a\\b", "x" * 256])
def test_nome_de_pasta_invalido(app: App, upstream: Upstream, nome: str) -> None:
    assert _criar(app, nome=nome).status_code == 400
    assert _chamadas(upstream, CRIAR) == []


def test_pai_invalido(app: App, upstream: Upstream) -> None:
    assert _criar(app, paiId="x").status_code == 400
    assert _criar(app, paiId=-3).status_code == 400


def test_criar_respeita_a_protecao_do_perfil(app: App, upstream: Upstream) -> None:
    assert _criar(app, perfil="leitura").status_code == 403
    assert _criar(app, perfil="prod").status_code == 428
    assert _chamadas(upstream, CRIAR) == []
    upstream.respostas[CRIAR] = (200, "{}")
    assert _criar(app, perfil="prod", confirmarProtecao=True).status_code == 200


def test_erro_da_api_ao_criar_aparece_e_vai_para_o_log(app: App, upstream: Upstream) -> None:
    upstream.respostas[CRIAR] = (400, '{"retorno": "Já existe uma pasta com esse nome"}')
    r = _criar(app)
    assert r.status_code == 502
    assert "Já existe uma pasta com esse nome" in r.json()["erro"]
    textos = [str(e["body"]) for e in app.historico.todas()]  # type: ignore[union-attr]
    assert any("Criar a pasta" in t and "falhou" in t for t in textos)


def test_criar_pasta_ok_vai_para_o_log(app: App, upstream: Upstream) -> None:
    upstream.respostas[CRIAR] = (200, "{}")
    _criar(app)
    textos = [str(e["body"]) for e in app.historico.todas()]  # type: ignore[union-attr]
    assert any('Pasta "Novas" criada dentro da pasta 10' in t for t in textos)


# ----------------------------------------------------- excluir pasta inteira ---


def _contagem(upstream: Upstream, total: int, pendentes: int) -> None:
    def resposta(q: dict[str, list[str]]) -> tuple[int, str]:
        n = pendentes if q.get("status") == ["pendente"] else total
        return 200, json.dumps({"total": n, "temMais": False, "documentos": []})

    upstream.dinamicas[LISTAGEM] = resposta


def _verificar(app: App, **extra: Any) -> Any:
    return app.http.post(
        "/_excluir/pasta/verificar", json={"perfil": "cliente1", "diretorioId": 10, **extra}
    )


def _excluir(app: App, confirmacao: str = "EXCLUIR PASTA 10", **extra: Any) -> Any:
    corpo = {"perfil": "cliente1", "diretorioId": 10, "confirmacao": confirmacao, **extra}
    return app.http.post("/_excluir/pasta/executar", json=corpo)


def test_verificar_a_pasta_inteira(app: App, upstream: Upstream) -> None:
    _arvore(upstream)
    _contagem(upstream, total=42, pendentes=3)
    r = _verificar(app).json()
    assert (r["total"], r["fluxosAtivos"], r["subpastas"]) == (42, 3, 1)
    assert r["confirmacao"] == "EXCLUIR PASTA 10"
    assert _chamadas(upstream, EXCLUIR_PASTA) == []


def test_verificar_funciona_sem_a_arvore(app: App, upstream: Upstream) -> None:
    upstream.respostas[ARVORE] = (401, "")
    _contagem(upstream, total=5, pendentes=0)
    assert _verificar(app).json()["subpastas"] is None


def test_excluir_a_pasta_inteira(app: App, upstream: Upstream) -> None:
    _arvore(upstream)
    _contagem(upstream, total=42, pendentes=0)
    upstream.respostas[EXCLUIR_PASTA] = (200, "{}")
    r = _excluir(app, confirmacao=" excluir  pasta 10 ")
    assert r.status_code == 200 and r.json()["total"] == 42
    q = _consulta(_chamadas(upstream, EXCLUIR_PASTA)[0])
    assert (q["diretorioId"], q["instancia"]) == ("10", "cliente1")
    textos = [str(e["body"]) for e in app.historico.todas()]  # type: ignore[union-attr]
    assert any("Pasta 10 excluída inteira" in t and "42 documento(s)" in t for t in textos)


def test_confirmacao_errada_nao_exclui(app: App, upstream: Upstream) -> None:
    _contagem(upstream, total=1, pendentes=0)
    assert _excluir(app, confirmacao="EXCLUIR").status_code == 428
    assert _excluir(app, confirmacao="EXCLUIR PASTA 11").status_code == 428
    assert _chamadas(upstream, EXCLUIR_PASTA) == []


def test_com_fluxo_ativo_recusa_e_manda_usar_a_exclusao_em_massa(
    app: App, upstream: Upstream
) -> None:
    _contagem(upstream, total=10, pendentes=2)
    r = _excluir(app)
    assert r.status_code == 400
    assert "2 documento(s) com fluxo de assinatura ativo" in r.json()["erro"]
    assert _chamadas(upstream, EXCLUIR_PASTA) == []


def test_raiz_e_pasta_invalida_sao_recusadas(app: App, upstream: Upstream) -> None:
    for pasta in (0, -1, "x"):
        r = app.http.post(
            "/_excluir/pasta/executar",
            json={"perfil": "cliente1", "diretorioId": pasta, "confirmacao": "EXCLUIR PASTA 0"},
        )
        assert r.status_code == 400
    assert _chamadas(upstream, EXCLUIR_PASTA) == []


def test_excluir_pasta_respeita_a_protecao_do_perfil(app: App, upstream: Upstream) -> None:
    _contagem(upstream, total=1, pendentes=0)
    assert _excluir(app, perfil="leitura").status_code == 403
    assert _excluir(app, perfil="prod").status_code == 428
    assert _chamadas(upstream, EXCLUIR_PASTA) == []


def test_erro_da_api_ao_excluir_a_pasta(app: App, upstream: Upstream) -> None:
    _contagem(upstream, total=1, pendentes=0)
    upstream.respostas[EXCLUIR_PASTA] = (403, '{"retorno": "sem permissão"}')
    r = _excluir(app)
    assert r.status_code == 400 and "sem permissão" in r.json()["erro"]
    assert "Entre com o seu usuário" in r.json()["erro"]
    textos = [str(e["body"]) for e in app.historico.todas()]  # type: ignore[union-attr]
    assert any("falhou" in t and "pasta 10" in t for t in textos)


def test_excluir_pasta_exige_origem_da_pagina(app: App) -> None:
    ruim = {"Origin": "http://evil.example"}
    corpo = {"perfil": "cliente1", "diretorioId": 10, "confirmacao": "EXCLUIR PASTA 10"}
    assert app.http.post("/_excluir/pasta/executar", json=corpo, headers=ruim).status_code == 403
    assert app.http.post("/_excluir/pasta/verificar", json=corpo, headers=ruim).status_code == 403
    assert app.http.post("/_pastas/criar", json=corpo, headers=ruim).status_code == 403
