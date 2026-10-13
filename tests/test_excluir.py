"""Excluir documentos de uma pasta em massa, cancelando antes os fluxos ativos."""

from __future__ import annotations

import csv
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from docnuvem_tester.excluir import frase_de_confirmacao

from .conftest import TOKEN_OK, App, Upstream

LISTAGEM = ("GET", "/api/documentos")
EXCLUIR = ("POST", "/api/sync/excluirDocumento")
LOGIN = ("POST", "/api/sync/login")
SENHA = "senha-de-teste"
TOKEN_USUARIO = "tok-USUARIO-0123456789"


def _docs(upstream: Upstream, *docs: tuple[int, str, str, int | None]) -> None:
    """(documentoId, nome, status, assinaturaId)"""
    itens = [
        {
            "documentoId": i,
            "nomeArquivo": nome,
            "status": status,
            "assinaturaId": ass,
            "diretorioId": 5,
        }
        for i, nome, status, ass in docs
    ]
    upstream.respostas[LISTAGEM] = (
        200,
        json.dumps({"total": len(itens), "temMais": False, "documentos": itens}),
    )


def _tres(upstream: Upstream) -> None:
    _docs(
        upstream,
        (1, "ativo.pdf", "pendente", 101),
        (2, "assinado.pdf", "assinado", 102),
        (3, "solto.pdf", "sem_assinatura", None),
    )


def _corpo(**extra: Any) -> dict[str, Any]:
    return {
        "perfil": "cliente1",
        "diretorioId": 5,
        "subpastas": False,
        "cancelarFluxos": True,
        "intervaloMs": 0,
        "confirmacao": "EXCLUIR 3",
        **extra,
    }


def _ensaio(app: App, **extra: Any) -> Any:
    return app.http.post("/_excluir/ensaio", json=_corpo(**extra))


def _iniciar(app: App, **extra: Any) -> dict[str, Any]:
    r = app.http.post("/_excluir/iniciar", json=_corpo(**extra))
    return dict(r.json()) | {"_status": r.status_code}


def _esperar(app: App, id_: str, limite: float = 15.0) -> dict[str, Any]:
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        dado = dict(app.http.get(f"/_excluir/{id_}").json())
        if dado["estado"] not in ("listando", "executando"):
            return dado
        time.sleep(0.03)
    raise AssertionError("a exclusão não terminou a tempo")


def _excluir(app: App, **extra: Any) -> dict[str, Any]:
    r = _iniciar(app, **extra)
    assert r["ok"] is True, r
    return _esperar(app, r["id"])


def _destrutivas(upstream: Upstream) -> list[dict[str, Any]]:
    """Tudo que altera algo: cancelamentos e exclusões."""
    return [c for c in upstream.recebidas if c["method"] in ("POST", "DELETE")]


def _consulta(chamada: dict[str, Any]) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(chamada["path"]).query).items()}


# --------------------------------------------------------------------- ensaio ---


def test_ensaio_resume_sem_alterar_nada(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    r = _ensaio(app).json()
    assert r["ok"] is True
    assert (r["total"], r["fluxosAtivos"]) == (3, 1)
    assert r["porStatus"] == {"pendente": 1, "assinado": 1, "sem_assinatura": 1}
    assert r["serao"] == {"cancelados": 1, "excluidos": 3, "pulados": 0}
    assert r["confirmacao"] == "EXCLUIR 3"
    assert [a["nome"] for a in r["amostra"]] == ["ativo.pdf", "assinado.pdf", "solto.pdf"]
    assert _destrutivas(upstream) == []  # a verificação nunca altera nada
    assert {c["method"] for c in upstream.recebidas} == {"GET"}


def test_ensaio_sem_cancelar_fluxos_mostra_o_que_sera_pulado(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    r = _ensaio(app, cancelarFluxos=False).json()
    assert r["serao"] == {"cancelados": 0, "excluidos": 2, "pulados": 1}


def test_so_fluxo_pendente_conta_como_ativo(app: App, upstream: Upstream) -> None:
    _docs(
        upstream,
        (1, "a.pdf", "expirado", 11),
        (2, "b.pdf", "cancelado", 12),
        (3, "c.pdf", "assinado", 13),
        (4, "d.pdf", "pendente", None),  # pendente mas sem id de assinatura: nada a cancelar
    )
    assert _ensaio(app).json()["fluxosAtivos"] == 0


def test_ensaio_passa_pasta_subpastas_e_filtros_para_a_listagem(
    app: App, upstream: Upstream
) -> None:
    _tres(upstream)
    _ensaio(app, subpastas=True, status="assinado", dataInicio="2026-01-01", dataFim="2026-02-01")
    consulta = _consulta(next(c for c in upstream.recebidas if c["rota"] == "/api/documentos"))
    assert consulta["diretorioId"] == "5"
    assert consulta["incluirSubpastas"] == "true"
    assert (consulta["status"], consulta["dataInicio"], consulta["dataFim"]) == (
        "assinado",
        "2026-01-01",
        "2026-02-01",
    )


def test_ensaio_nao_envia_filtro_vazio(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    _ensaio(app, status="", dataInicio="", dataFim="")
    caminho = next(c for c in upstream.recebidas if c["rota"] == "/api/documentos")["path"]
    assert "status" not in caminho and "dataInicio" not in caminho


@pytest.mark.parametrize(
    ("campos", "trecho"),
    [
        ({"diretorioId": 0}, "raiz"),
        ({"diretorioId": -1}, "raiz"),
        ({"diretorioId": ""}, "diretorioId"),
        ({"diretorioId": "abc"}, "diretorioId"),
        ({"status": "apagado"}, "Status inválido"),
        ({"dataInicio": "01/02/2026"}, "dataInicio"),
    ],
)
def test_ensaio_recusa_pedido_invalido(
    app: App, upstream: Upstream, campos: dict[str, Any], trecho: str
) -> None:
    _tres(upstream)
    r = _ensaio(app, **campos)
    assert r.status_code == 400
    assert trecho in r.json()["erro"]
    assert upstream.recebidas == []


def _paginado(total: int, nome: str = "d") -> Any:
    """Uma API com `total` documentos, paginada de verdade (pagina/tamanho)."""

    def resposta(q: dict[str, list[str]]) -> tuple[int, str]:
        pagina, tamanho = int(q["pagina"][0]), int(q["tamanho"][0])
        ini = pagina * tamanho
        itens = [
            {"documentoId": i, "nomeArquivo": f"{nome}{i}.pdf", "status": "sem_assinatura"}
            for i in range(ini + 1, min(ini + tamanho, total) + 1)
        ]
        corpo = {"total": total, "temMais": ini + tamanho < total, "documentos": itens}
        return 200, json.dumps(corpo)

    return resposta


def test_nao_ha_limite_de_quantidade_na_verificacao(app: App, upstream: Upstream) -> None:
    upstream.dinamicas[LISTAGEM] = _paginado(12_345)
    r = _ensaio(app, confirmacao="").json()
    assert r["ok"] is True
    assert r["total"] == 12_345  # bem acima dos 5000 do limite antigo
    assert r["confirmacao"] == "EXCLUIR 12345"


def test_exclusao_de_mais_de_5000_comeca_depois_de_ler_tudo(app: App, upstream: Upstream) -> None:
    upstream.dinamicas[LISTAGEM] = _paginado(6000)
    r = _iniciar(app, confirmacao="EXCLUIR 6000", intervaloMs=2000)
    assert r["ok"] is True
    fim = None
    for _ in range(200):  # espera a leitura terminar e a primeira exclusão sair
        s = app.http.get(f"/_excluir/{r['id']}").json()
        if s["estado"] == "executando" and s["excluidos"] >= 1:
            fim = s
            break
        time.sleep(0.05)
    assert fim is not None
    assert fim["total"] == 6000
    app.http.post(f"/_excluir/{r['id']}/cancelar")
    final = _esperar(app, r["id"])
    assert final["estado"] == "cancelado"
    assert 1 <= final["excluidos"] < 6000


def test_a_leitura_aparece_como_listando_e_depois_segue(app: App, upstream: Upstream) -> None:
    paginas = _paginado(450)

    def devagar(q: dict[str, list[str]]) -> tuple[int, str]:
        if q["tamanho"][0] != "1":
            time.sleep(0.25)
        return paginas(q)

    upstream.dinamicas[LISTAGEM] = devagar
    r = _iniciar(app, confirmacao="EXCLUIR 450")
    vistos = set()
    for _ in range(400):
        s = app.http.get(f"/_excluir/{r['id']}").json()
        vistos.add(s["estado"])
        if s["estado"] not in ("listando", "executando"):
            break
        time.sleep(0.03)
    assert "listando" in vistos and s["estado"] == "concluido"
    assert s["excluidos"] == 450 and s["lidos"] == 450


def test_pagina_repetida_pela_api_nao_trava(app: App, upstream: Upstream) -> None:
    repetida = [
        {"documentoId": i, "nomeArquivo": f"r{i}.pdf", "status": "assinado"} for i in (1, 2)
    ]
    upstream.dinamicas[LISTAGEM] = lambda q: (
        200,
        json.dumps({"total": 99999, "temMais": True, "documentos": repetida}),
    )
    r = _ensaio(app, confirmacao="").json()
    assert r["total"] == 2  # a API não avança: para e não conta repetidos


def test_sem_total_na_api_cai_para_a_leitura_completa(app: App, upstream: Upstream) -> None:
    itens = [{"documentoId": i, "nomeArquivo": f"{i}.pdf", "status": "assinado"} for i in (1, 2)]
    upstream.respostas[LISTAGEM] = (200, json.dumps({"temMais": False, "documentos": itens}))
    fim = _excluir(app, confirmacao="EXCLUIR 2")
    assert fim["excluidos"] == 2


def test_pasta_que_muda_durante_a_leitura_nao_exclui_nada(app: App, upstream: Upstream) -> None:
    """A contagem rápida dizia 3, mas a leitura completa achou 4: nada pode ser excluído."""

    def resposta(q: dict[str, list[str]]) -> tuple[int, str]:
        docs = [
            {"documentoId": i, "nomeArquivo": f"{i}.pdf", "status": "assinado"}
            for i in (1, 2, 3, 4)
        ]
        if q["tamanho"][0] == "1":
            return 200, json.dumps({"total": 3, "temMais": True, "documentos": docs[:1]})
        return 200, json.dumps({"total": 4, "temMais": False, "documentos": docs})

    upstream.dinamicas[LISTAGEM] = resposta
    r = _iniciar(app, confirmacao="EXCLUIR 3")
    assert r["ok"] is True
    fim = _esperar(app, r["id"])
    assert fim["estado"] == "erro"
    assert "mudou durante a leitura" in fim["erro"]
    assert "Nada foi excluído" in fim["erro"]
    assert _destrutivas(upstream) == []


def test_falha_na_leitura_nao_exclui_nada(app: App, upstream: Upstream) -> None:
    paginas = _paginado(500)
    upstream.dinamicas[LISTAGEM] = lambda q: (500, "") if q["pagina"][0] == "1" else paginas(q)
    r = _iniciar(app, confirmacao="EXCLUIR 500")
    fim = _esperar(app, r["id"])
    assert fim["estado"] == "erro"
    assert "Não consegui ler a pasta" in fim["erro"]
    assert _destrutivas(upstream) == []


def test_cancelar_durante_a_leitura_nao_exclui_nada(app: App, upstream: Upstream) -> None:
    paginas = _paginado(2000)

    def devagar(q: dict[str, list[str]]) -> tuple[int, str]:
        if q["tamanho"][0] != "1":
            time.sleep(0.2)
        return paginas(q)

    upstream.dinamicas[LISTAGEM] = devagar
    r = _iniciar(app, confirmacao="EXCLUIR 2000")
    time.sleep(0.4)
    app.http.post(f"/_excluir/{r['id']}/cancelar")
    fim = _esperar(app, r["id"])
    assert fim["estado"] == "cancelado"
    assert _destrutivas(upstream) == []


def test_ensaio_perfil_desconhecido(app: App) -> None:
    assert _ensaio(app, perfil="ninguem").status_code == 404


def test_ensaio_api_fora_do_ar(app: App) -> None:
    r = _ensaio(app, perfil="fora")
    assert r.status_code == 400
    assert "conexão" in r.json()["erro"]


# ------------------------------------------------------------------ confirmação ---


def test_confirmacao_e_obrigatoria_e_exata(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    for ruim in ("", "excluir", "EXCLUIR 2", "EXCLUIR 30", "sim", "EXCLUIR"):
        r = _iniciar(app, confirmacao=ruim)
        assert r["_status"] == 428, ruim
        assert "confirmação não confere" in r["erro"]
    assert _destrutivas(upstream) == []


def test_confirmacao_aceita_caixa_e_espacos(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    assert _excluir(app, confirmacao="  excluir   3 ")["excluidos"] == 3


def test_se_a_pasta_mudou_desde_a_verificacao_nao_exclui(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    n = _ensaio(app).json()["total"]
    _docs(
        upstream,
        (1, "a.pdf", "assinado", 1),
        (2, "b.pdf", "assinado", 2),
        (3, "c.pdf", "sem_assinatura", None),
        (4, "novo.pdf", "sem_assinatura", None),
    )
    r = _iniciar(app, confirmacao=frase_de_confirmacao(n))
    assert r["_status"] == 428
    assert "Agora há 4 documento(s)" in r["erro"]
    assert _destrutivas(upstream) == []


def test_nao_exclui_a_raiz_nem_com_confirmacao(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    r = _iniciar(app, diretorioId=0)
    assert r["_status"] == 400
    assert "raiz" in r["erro"]
    assert upstream.recebidas == [] and _destrutivas(upstream) == []


def test_pasta_vazia(app: App, upstream: Upstream) -> None:
    _docs(upstream)
    r = _iniciar(app, confirmacao="EXCLUIR 0")
    assert r["_status"] == 400
    assert "Não há documentos" in r["erro"]


# --------------------------------------------------------------------- execução ---


def test_cancela_o_fluxo_ativo_antes_de_excluir(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    fim = _excluir(app)
    assert (fim["estado"], fim["excluidos"], fim["fluxosCancelados"], fim["nFalhas"]) == (
        "concluido",
        3,
        1,
        0,
    )
    sequencia = [(c["method"], c["rota"]) for c in _destrutivas(upstream)]
    assert sequencia == [
        ("DELETE", "/api/assinatura/101"),  # o fluxo ativo cai primeiro...
        ("POST", "/api/sync/excluirDocumento"),  # ...e só então o documento vai para a lixeira
        ("POST", "/api/sync/excluirDocumento"),
        ("POST", "/api/sync/excluirDocumento"),
    ]
    excluidos = [
        _consulta(c)["documentoId"] for c in _destrutivas(upstream) if c["method"] == "POST"
    ]
    assert excluidos == ["1", "2", "3"]


def test_so_cancela_o_que_esta_ativo(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    _excluir(app)
    cancelados = [c["rota"] for c in upstream.recebidas if c["method"] == "DELETE"]
    assert cancelados == ["/api/assinatura/101"]  # o assinado e o solto não são mexidos


def test_cancelamento_leva_instancia_e_motivo(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    _excluir(app)
    pedido = next(c for c in upstream.recebidas if c["method"] == "DELETE")
    consulta = _consulta(pedido)
    assert consulta["instancia"] == "cliente1"
    assert "Exclusão em massa" in consulta["motivo"]
    assert pedido["auth"] == f"Bearer {TOKEN_OK}"


def test_sem_cancelar_fluxos_o_documento_ativo_e_pulado(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    fim = _excluir(app, cancelarFluxos=False, confirmacao="EXCLUIR 3")
    assert (fim["excluidos"], fim["pulados"], fim["fluxosCancelados"]) == (2, 1, 0)
    assert [c for c in upstream.recebidas if c["method"] == "DELETE"] == []
    ids = {_consulta(c)["documentoId"] for c in _destrutivas(upstream)}
    assert ids == {"2", "3"}  # o ativo não foi tocado


@pytest.mark.parametrize("status", [404, 409])
def test_fluxo_que_ja_nao_estava_ativo_nao_impede_a_exclusao(
    app: App, upstream: Upstream, status: int
) -> None:
    _tres(upstream)
    upstream.respostas[("DELETE", "/api/assinatura/101")] = (
        status,
        '{"retorno": "estado inválido"}',
    )
    fim = _excluir(app)
    assert (fim["excluidos"], fim["nFalhas"], fim["fluxosCancelados"]) == (3, 0, 0)


def test_falha_ao_cancelar_o_fluxo_nao_exclui_aquele_documento(
    app: App, upstream: Upstream
) -> None:
    _tres(upstream)
    upstream.respostas[("DELETE", "/api/assinatura/101")] = (500, '{"retorno": "erro interno"}')
    fim = _excluir(app)
    assert (fim["excluidos"], fim["nFalhas"]) == (2, 1)
    assert fim["falhas"][0]["documentoId"] == 1
    assert "não consegui cancelar o fluxo" in fim["falhas"][0]["erro"]
    ids = {
        _consulta(c)["documentoId"]
        for c in upstream.recebidas
        if c["rota"] == "/api/sync/excluirDocumento"
    }
    assert "1" not in ids  # o documento com fluxo não cancelado fica intacto


def test_falha_isolada_nao_derruba_o_resto_e_nao_repete(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    upstream.dinamicas[EXCLUIR] = lambda q: (500, "") if q["documentoId"][0] == "2" else (200, "{}")
    fim = _excluir(app)
    assert (fim["excluidos"], fim["nFalhas"]) == (2, 1)
    assert fim["falhas"][0]["documentoId"] == 2
    assert (
        len([c for c in upstream.recebidas if c["rota"] == "/api/sync/excluirDocumento"]) == 3
    )  # sem repetição


def test_sem_permissao_para_tudo_na_primeira_recusa(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    upstream.respostas[EXCLUIR] = (401, '{"retorno": "Esta instância exige login de usuário."}')
    fim = _excluir(app)
    assert fim["estado"] == "erro"
    assert "Entre com o seu usuário em Perfis" in fim["erro"]
    assert len([c for c in upstream.recebidas if c["rota"] == "/api/sync/excluirDocumento"]) == 1


def test_para_depois_de_falhas_seguidas(app: App, upstream: Upstream) -> None:
    docs = [(i, f"d{i}.pdf", "sem_assinatura", None) for i in range(1, 10)]
    _docs(upstream, *docs)
    upstream.respostas[EXCLUIR] = (500, "")
    fim = _excluir(app, confirmacao="EXCLUIR 9")
    assert fim["estado"] == "erro"
    assert "5 falhas seguidas" in fim["erro"]
    assert len([c for c in upstream.recebidas if c["rota"] == "/api/sync/excluirDocumento"]) == 5


def test_so_age_nos_documentos_que_a_listagem_devolveu(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    _excluir(app)
    ids = {
        _consulta(c)["documentoId"]
        for c in upstream.recebidas
        if c["rota"] == "/api/sync/excluirDocumento"
    }
    assert ids <= {"1", "2", "3"}


def test_exclusao_usa_o_token_do_usuario_e_o_cancelamento_o_da_instancia(
    app_cfg: App, upstream: Upstream
) -> None:
    def login(q: dict[str, list[str]]) -> tuple[int, str]:
        return (
            (200, json.dumps({"token": TOKEN_USUARIO})) if q["senha"][0] == SENHA else (401, "{}")
        )

    upstream.dinamicas[LOGIN] = login
    app_cfg.http.post("/_config/login", json={"perfil": "a", "login": "ana", "senha": SENHA})
    _tres(upstream)
    corpo = _corpo(perfil="a")
    r = app_cfg.http.post("/_excluir/iniciar", json=corpo).json()
    _esperar(app_cfg, r["id"])
    sync = [c for c in upstream.recebidas if c["rota"] == "/api/sync/excluirDocumento"]
    assert sync and all(c["auth"] == f"Bearer {TOKEN_USUARIO}" for c in sync)
    cancelamento = next(c for c in upstream.recebidas if c["method"] == "DELETE")
    assert cancelamento["auth"] == f"Bearer {TOKEN_OK}"


def test_sem_login_a_exclusao_usa_o_token_da_instancia(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    _excluir(app)
    sync = [c for c in upstream.recebidas if c["rota"] == "/api/sync/excluirDocumento"]
    assert all(c["auth"] == f"Bearer {TOKEN_OK}" for c in sync)


# ------------------------------------------------------------ perfil protegido ---


def test_perfil_somente_leitura_nao_exclui(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    r = _iniciar(app, perfil="leitura")
    assert r["_status"] == 403
    assert _destrutivas(upstream) == []


def test_perfil_de_producao_exige_confirmacao_extra(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    assert _iniciar(app, perfil="prod")["_status"] == 428
    assert _destrutivas(upstream) == []
    assert _excluir(app, perfil="prod", confirmarProtecao=True)["excluidos"] == 3


def test_a_verificacao_vale_ate_em_perfil_somente_leitura(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    assert _ensaio(app, perfil="leitura").json()["total"] == 3


# ------------------------------------------------------------------- andamento ---


def test_cancelar_a_exclusao(app: App, upstream: Upstream) -> None:
    docs = [(i, f"d{i}.pdf", "sem_assinatura", None) for i in range(1, 8)]
    _docs(upstream, *docs)
    r = _iniciar(app, confirmacao="EXCLUIR 7", intervaloMs=2000)
    time.sleep(0.3)
    assert app.http.post(f"/_excluir/{r['id']}/cancelar").json() == {"ok": True}
    inicio = time.monotonic()
    fim = _esperar(app, r["id"])
    assert fim["estado"] == "cancelado"
    assert time.monotonic() - inicio < 1.5  # acordou do intervalo
    assert fim["excluidos"] < 7


def test_uma_exclusao_por_vez(app: App, upstream: Upstream) -> None:
    docs = [(i, f"d{i}.pdf", "sem_assinatura", None) for i in range(1, 4)]
    _docs(upstream, *docs)
    primeira = _iniciar(app, confirmacao="EXCLUIR 3", intervaloMs=1500)
    segunda = _iniciar(app, confirmacao="EXCLUIR 3")
    assert segunda["_status"] == 409
    app.http.post(f"/_excluir/{primeira['id']}/cancelar")
    _esperar(app, primeira["id"])


def test_intervalo_invalido(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    assert _iniciar(app, intervaloMs="abc")["_status"] == 400


def test_relatorio_csv(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    upstream.dinamicas[EXCLUIR] = lambda q: (500, "") if q["documentoId"][0] == "3" else (200, "{}")
    fim = _excluir(app)
    r = app.http.get(f"/_excluir/{fim['id']}/relatorio.csv")
    assert "attachment" in r.headers["content-disposition"]
    linhas = list(csv.reader(r.content.decode("utf-8-sig").splitlines(), delimiter=";"))
    assert linhas[0] == [
        "documentoId",
        "nome",
        "status_antes",
        "fluxo",
        "situacao",
        "http",
        "detalhe",
    ]
    por_id = {linha[0]: linha for linha in linhas[1:]}
    assert (por_id["1"][3], por_id["1"][4]) == ("cancelado", "excluido")
    assert (por_id["2"][3], por_id["2"][4]) == ("—", "excluido")
    assert por_id["3"][4] == "falhou"


def test_desconhecida(app: App) -> None:
    assert app.http.get("/_excluir/nada").status_code == 404
    assert app.http.get("/_excluir/nada/relatorio.csv").status_code == 404
    assert app.http.post("/_excluir/nada/cancelar").status_code == 404


def test_exige_origem_da_pagina(app: App, upstream: Upstream) -> None:
    _tres(upstream)
    ruim = {"Origin": "http://evil.example"}
    assert app.http.post("/_excluir/ensaio", json=_corpo(), headers=ruim).status_code == 403
    assert app.http.post("/_excluir/iniciar", json=_corpo(), headers=ruim).status_code == 403
    assert app.http.post("/_excluir/x/cancelar", headers=ruim).status_code == 403
    assert upstream.recebidas == []


def test_o_proxy_continua_sem_rota_de_exclusao(app: App, upstream: Upstream) -> None:
    """A página não consegue excluir por fora do fluxo com confirmação."""
    r = app.http.post("/_proxy/cliente1/api/sync/excluirDocumento?documentoId=1")
    assert r.status_code == 403
    assert _destrutivas(upstream) == []
