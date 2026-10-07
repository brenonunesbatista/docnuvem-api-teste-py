"""Etapa 3: comparar instâncias, lote para escolas e teste de fumaça."""

from __future__ import annotations

import csv
import json
import re
import time
from typing import Any

import pytest

from docnuvem_tester import lote
from docnuvem_tester.lote import ErroLote, cpf_valido, ler_csv, preparar
from docnuvem_tester.roteiro import pdf_minimo

from .conftest import App, Upstream

# ============================================================ comparar ===


def _por_instancia(dados: dict[str, Any]) -> Any:
    """Resposta que depende da instância pedida (cada perfil enxerga um conteúdo)."""

    def resposta(q: dict[str, list[str]]) -> tuple[int, str]:
        return 200, json.dumps(dados[q["instancia"][0]])

    return resposta


def _modelo(nome: str, gerar: bool = True, *variaveis: str) -> dict[str, Any]:
    return {
        "id": 1,
        "codigo": nome[:3].upper(),
        "nome": nome,
        "geravelPorApi": gerar,
        "variaveis": [{"chave": v} for v in variaveis],
    }


def _comparar(app: App, tipo: str, a: str = "cliente1", b: str = "prod") -> Any:
    return app.http.post("/_comparar", json={"tipo": tipo, "a": a, "b": b})


def test_comparar_modelos(app: App, upstream: Upstream) -> None:
    upstream.dinamicas[("GET", "/api/modelos")] = _por_instancia(
        {
            "cliente1": {
                "modelos": [
                    _modelo("Contrato", True, "nome"),
                    _modelo("Só no A"),
                    _modelo("Igual", True, "x"),
                ]
            },
            "prod": {
                "modelos": [
                    _modelo("Contrato", False, "nome", "valor"),
                    _modelo("Só no B"),
                    _modelo("Igual", True, "x"),
                ]
            },
        }
    )
    r = _comparar(app, "modelos").json()
    assert r["ok"] is True
    assert (r["a"]["total"], r["b"]["total"]) == (3, 3)
    assert r["apenasA"] == ["Só no A"]
    assert r["apenasB"] == ["Só no B"]
    assert r["iguais"] == 1
    assert r["mesmo"] is False
    (dif,) = r["diferentes"]
    assert dif["chave"] == "Contrato"
    campos = {c["campo"]: (c["a"], c["b"]) for c in dif["campos"]}
    assert campos["gerável por API"] == ("sim", "não")
    assert campos["variáveis"] == ("nome", "nome, valor")


def test_comparar_modelos_identicos(app: App, upstream: Upstream) -> None:
    mesmos = {"modelos": [_modelo("A"), _modelo("B")]}
    upstream.dinamicas[("GET", "/api/modelos")] = _por_instancia(
        {"cliente1": mesmos, "prod": mesmos}
    )
    r = _comparar(app, "modelos").json()
    assert r["mesmo"] is True
    assert r["iguais"] == 2


def test_comparar_pastas(app: App, upstream: Upstream) -> None:
    def pasta(i: int, caminho: str) -> dict[str, Any]:
        return {"diretorioId": i, "caminho": caminho}

    upstream.dinamicas[("GET", "/api/diretorios")] = _por_instancia(
        {
            "cliente1": {"temMais": False, "diretorios": [pasta(1, "/A"), pasta(2, "/A/B")]},
            "prod": {"temMais": False, "diretorios": [pasta(9, "/A"), pasta(8, "/C")]},
        }
    )
    r = _comparar(app, "pastas").json()
    assert r["apenasA"] == ["/A/B"]
    assert r["apenasB"] == ["/C"]
    assert r["iguais"] == 1  # "/A" existe nos dois, mesmo com ids diferentes


def test_comparar_diagnostico_destaca_so_a_situacao(app: App) -> None:
    r = _comparar(app, "diagnostico", "cliente1", "ruim").json()
    assert r["comparou"] is True
    chaves = {d["chave"]: d["campos"][0] for d in r["diferentes"]}
    assert "Token aceito" in chaves
    assert chaves["Token aceito"]["a"].startswith("ok")
    assert chaves["Token aceito"]["b"].startswith("erro")
    assert r["iguais"] >= 1  # "API acessível" é igual nos dois (o tempo não conta)


def test_comparar_so_faz_leituras(app: App, upstream: Upstream) -> None:
    for tipo in ("modelos", "pastas", "diagnostico"):
        _comparar(app, tipo)
    assert upstream.recebidas
    assert {c["method"] for c in upstream.recebidas} == {"GET"}


def test_comparar_um_lado_com_erro_nao_compara(app: App, upstream: Upstream) -> None:
    upstream.dinamicas[("GET", "/api/modelos")] = lambda q: (
        (500, "") if q["instancia"][0] == "prod" else (200, '{"modelos": []}')
    )
    r = _comparar(app, "modelos").json()
    assert r["comparou"] is False
    assert r["a"]["erro"] == ""
    assert "HTTP 500" in r["b"]["erro"]


def test_comparar_lado_fora_do_ar(app: App) -> None:
    r = _comparar(app, "modelos", "cliente1", "fora").json()
    assert r["comparou"] is False
    assert "conexão" in r["b"]["erro"]


@pytest.mark.parametrize(
    ("corpo", "status"),
    [
        ({"tipo": "nada", "a": "cliente1", "b": "prod"}, 400),
        ({"tipo": "modelos", "a": "cliente1", "b": "cliente1"}, 400),
        ({"tipo": "modelos", "a": "cliente1", "b": "ninguem"}, 404),
        ({"tipo": "modelos", "a": "ninguem", "b": "prod"}, 404),
    ],
)
def test_comparar_pedido_invalido(app: App, corpo: dict[str, str], status: int) -> None:
    assert app.http.post("/_comparar", json=corpo).status_code == status


def test_comparar_exige_origem_da_pagina(app: App) -> None:
    r = app.http.post(
        "/_comparar",
        json={"tipo": "modelos", "a": "cliente1", "b": "prod"},
        headers={"Origin": "http://evil.example"},
    )
    assert r.status_code == 403


# ============================================================= lote ===

VALIDO = "529.982.247-25"
OUTRO = "111.444.777-35"
CABECALHO = "codigoMatricula;nome;cpf;email;telefone;tipoSolicitacao;emailResponsavel\n"


def _csv(*linhas: str) -> str:
    return CABECALHO + "\n".join(linhas) + "\n"


def _aluno(n: int, cpf: str = VALIDO, extra: str = ";;") -> str:
    return f"M{n};Aluno {n};{cpf};aluno{n}@exemplo.com.br{extra}"


@pytest.mark.parametrize(
    ("cpf", "esperado"),
    [
        ("529.982.247-25", True),
        ("52998224725", True),
        ("111.444.777-35", True),
        ("111.111.111-11", False),
        ("529.982.247-24", False),
        ("123", False),
        ("", False),
    ],
)
def test_cpf_valido(cpf: str, esperado: bool) -> None:
    assert cpf_valido(cpf) is esperado


def test_ler_csv_aceita_virgula_e_ponto_e_virgula_e_bom() -> None:
    ponto_virgula = ler_csv("﻿matrícula;Nome;CPF;E-mail\nM1;Ana;529.982.247-25;a@b.co\n")
    virgula = ler_csv("codigoMatricula,nome,cpf,email\nM1,Ana,529.982.247-25,a@b.co\n")
    for linhas in (ponto_virgula, virgula):
        assert linhas == [
            {
                "linha": "2",
                "codigoMatricula": "M1",
                "nome": "Ana",
                "cpf": "529.982.247-25",
                "email": "a@b.co",
            }
        ]


@pytest.mark.parametrize(
    ("texto", "trecho"),
    [
        ("", "vazio"),
        ("nome;cpf;email\nA;1;a@b.co\n", "codigoMatricula"),
        ("codigoMatricula;nome;cpf;email\n", "não há alunos"),
        ("codigoMatricula;nome;cpf;email\n;;;\n\n", "não há alunos"),
    ],
)
def test_ler_csv_recusa(texto: str, trecho: str) -> None:
    with pytest.raises(ErroLote, match=trecho):
        ler_csv(texto)


def test_ler_csv_limite_de_linhas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lote, "MAX_LINHAS", 2)
    with pytest.raises(ErroLote, match="máximo"):
        ler_csv(_csv(_aluno(1), _aluno(2), _aluno(3)))


def test_preparar_aponta_os_problemas_por_linha() -> None:
    texto = _csv(
        _aluno(1),
        _aluno(2, cpf="111.111.111-11"),
        "M3;;" + VALIDO + ";sem-arroba;123;9;ruim",
        _aluno(1, OUTRO),  # matrícula M1 repetida
    )
    r = preparar(texto)
    assert [v["codigoMatricula"] for v in r["validas"]] == ["M1"]
    por_linha = {i["linha"]: i["problemas"] for i in r["invalidas"]}
    assert por_linha[3] == ["CPF inválido"]
    assert set(por_linha[4]) == {
        "nome vazio",
        "e-mail inválido",
        "telefone deve ter DDD (10 a 13 dígitos)",
        "tipoSolicitacao deve ser 1 ou 2",
        "e-mail do responsável inválido",
    }
    assert por_linha[5] == ["matrícula repetida no arquivo"]


def test_validar_pela_api(app: App) -> None:
    r = app.http.post("/_lote/validar", json={"csv": _csv(_aluno(1), _aluno(2, cpf="1"))}).json()
    assert (r["linhas"], r["validas"], r["nInvalidas"]) == (2, 1, 1)
    assert r["amostra"][0]["codigoMatricula"] == "M1"
    assert r["invalidas"][0]["linha"] == 3


def test_validar_arquivo_ruim(app: App) -> None:
    r = app.http.post("/_lote/validar", json={"csv": "nome;cpf\n"})
    assert r.status_code == 400
    assert "Faltam colunas" in r.json()["erro"]


def _lote(app: App, csv_texto: str, **extra: Any) -> dict[str, Any]:
    corpo = {
        "perfil": "cliente1",
        "csv": csv_texto,
        "modo": "enviar",
        "intervaloMs": 0,
        "confirmarEnvio": True,
        **extra,
    }
    r = app.http.post("/_lote/iniciar", json=corpo)
    return dict(r.json()) | {"_status": r.status_code}


def _esperar(app: App, id_: str, limite: float = 15.0) -> dict[str, Any]:
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        dado = dict(app.http.get(f"/_lote/{id_}").json())
        if dado["estado"] == "executando":
            time.sleep(0.03)
            continue
        return dado
    raise AssertionError("o lote não terminou a tempo")


def _rodar(app: App, csv_texto: str, **extra: Any) -> dict[str, Any]:
    r = _lote(app, csv_texto, **extra)
    assert r["ok"] is True, r
    return _esperar(app, r["id"])


SOLICITAR = ("POST", "/api/solicitacaoAluno/solicitarEnvioDocumentos")
CONSULTAR = ("GET", "/api/solicitacaoAluno/consultarStatus")


def test_envia_so_as_linhas_validas(app: App, upstream: Upstream) -> None:
    upstream.respostas[SOLICITAR] = (200, '{"emailEnviado": true, "linkEnvio": "https://l/1"}')
    fim = _rodar(
        app,
        _csv(
            _aluno(1, extra=";;1;"),
            _aluno(2, cpf="1"),
            _aluno(3, extra="; 11 98765-4321;2;mae@x.co"),
        ),
    )
    assert (fim["estado"], fim["total"], fim["ok"], fim["invalidas"]) == ("concluido", 2, 2, 1)
    enviados = [json.loads(c["corpo"]) for c in upstream.recebidas if c["method"] == "POST"]
    assert [e["codigoMatricula"] for e in enviados] == ["M1", "M3"]
    assert enviados[0]["cpf"] == "52998224725"  # só dígitos
    assert enviados[0]["tipoSolicitacao"] == 1
    assert "telefone" not in enviados[0]
    assert enviados[1]["telefone"] == "11987654321"
    assert enviados[1]["tipoSolicitacao"] == 2
    assert enviados[1]["emailResponsavel"] == "mae@x.co"


def test_envio_exige_confirmacao(app: App, upstream: Upstream) -> None:
    r = _lote(app, _csv(_aluno(1)), confirmarEnvio=False)
    assert r["_status"] == 428
    assert not [c for c in upstream.recebidas if c["method"] == "POST"]


def test_falha_nao_e_repetida_e_nao_derruba_o_lote(app: App, upstream: Upstream) -> None:
    upstream.dinamicas[SOLICITAR] = lambda q: (200, "{}")
    upstream.respostas[SOLICITAR] = (422, '{"retorno": "Modelo não encontrado"}')
    upstream.dinamicas.pop(SOLICITAR)
    fim = _rodar(app, _csv(_aluno(1), _aluno(2, OUTRO)))
    assert (fim["ok"], fim["nFalhas"]) == (0, 2)
    assert "HTTP 422: Modelo não encontrado" in fim["falhas"][0]["erro"]
    posts = [c for c in upstream.recebidas if c["method"] == "POST"]
    assert len(posts) == 2  # uma tentativa por aluno, sem repetição


def test_para_depois_de_falhas_seguidas(app: App, upstream: Upstream) -> None:
    upstream.respostas[SOLICITAR] = (500, "")
    alunos = [_aluno(i) for i in range(1, 9)]
    fim = _rodar(app, _csv(*alunos))
    assert fim["estado"] == "erro"
    assert "5 falhas seguidas" in fim["erro"]
    assert len([c for c in upstream.recebidas if c["method"] == "POST"]) == 5


def test_sucesso_zera_a_contagem_de_falhas_seguidas(app: App, upstream: Upstream) -> None:
    contagem = {"n": 0}

    def alterna(q: dict[str, list[str]]) -> tuple[int, str]:
        contagem["n"] += 1
        return (500, "") if contagem["n"] % 2 else (200, '{"emailEnviado": true}')

    upstream.dinamicas[SOLICITAR] = alterna
    fim = _rodar(app, _csv(*[_aluno(i) for i in range(1, 11)]))
    assert fim["estado"] == "concluido"
    assert (fim["ok"], fim["nFalhas"]) == (5, 5)


def test_consultar_status_em_lote(app: App, upstream: Upstream) -> None:
    def consulta(q: dict[str, list[str]]) -> tuple[int, str]:
        m = q["codigoMatricula"][0]
        if m == "M3":
            return 404, '{"retorno": "Aluno não encontrado"}'
        pend = 2 if m == "M1" else 0
        s = {"quantidadeItensPendentes": pend}
        return 200, json.dumps({"solicitacoes": [s]})

    upstream.dinamicas[CONSULTAR] = consulta
    fim = _rodar(
        app, _csv(_aluno(1), _aluno(2, OUTRO), _aluno(3, "390.533.447-05")), modo="consultar"
    )
    assert fim["modo"] == "consultar"
    assert (fim["ok"], fim["nFalhas"], fim["pendentesItens"]) == (2, 1, 2)
    assert {c["method"] for c in upstream.recebidas} == {"GET"}  # nada é enviado


def test_consultar_vale_ate_em_perfil_protegido(app: App) -> None:
    fim = _rodar(app, _csv(_aluno(1)), modo="consultar", perfil="leitura")
    assert fim["estado"] == "concluido"


def test_perfil_protegido_no_envio(app: App, upstream: Upstream) -> None:
    csv_texto = _csv(_aluno(1))
    assert _lote(app, csv_texto, perfil="leitura")["_status"] == 403
    assert _lote(app, csv_texto, perfil="prod")["_status"] == 428
    assert not [c for c in upstream.recebidas if c["method"] == "POST"]
    fim = _rodar(app, csv_texto, perfil="prod", confirmarProtecao=True)
    assert fim["ok"] == 1


def test_cancelar_lote(app: App, upstream: Upstream) -> None:
    r = _lote(app, _csv(*[_aluno(i) for i in range(1, 8)]), intervaloMs=2000)
    time.sleep(0.2)
    assert app.http.post(f"/_lote/{r['id']}/cancelar").json() == {"ok": True}
    inicio = time.monotonic()
    fim = _esperar(app, r["id"])
    assert fim["estado"] == "cancelado"
    assert time.monotonic() - inicio < 1.5  # acordou do intervalo, não esperou os 2 s
    assert fim["ok"] < 7


def test_um_lote_por_vez(app: App, upstream: Upstream) -> None:
    primeiro = _lote(app, _csv(*[_aluno(i) for i in range(1, 4)]), intervaloMs=1500)
    segundo = _lote(app, _csv(_aluno(9)))
    assert segundo["_status"] == 409
    app.http.post(f"/_lote/{primeiro['id']}/cancelar")
    _esperar(app, primeiro["id"])


def test_intervalo_invalido(app: App) -> None:
    assert _lote(app, _csv(_aluno(1)), intervaloMs="abc")["_status"] == 400


def test_lote_sem_linha_valida(app: App) -> None:
    r = _lote(app, _csv(_aluno(1, cpf="1")))
    assert r["_status"] == 400
    assert "Nenhuma linha válida" in r["erro"]


def test_modo_invalido(app: App) -> None:
    assert _lote(app, _csv(_aluno(1)), modo="apagar")["_status"] == 400


def test_relatorio_do_lote(app: App, upstream: Upstream) -> None:
    upstream.respostas[SOLICITAR] = (200, '{"emailEnviado": true, "linkEnvio": "https://l/1"}')
    fim = _rodar(app, _csv(_aluno(1), _aluno(2, cpf="1")))
    r = app.http.get(f"/_lote/{fim['id']}/relatorio.csv")
    assert "attachment" in r.headers["content-disposition"]
    linhas = list(csv.reader(r.content.decode("utf-8-sig").splitlines(), delimiter=";"))
    assert linhas[0] == ["linha", "codigoMatricula", "nome", "situacao", "http", "detalhe"]
    assert linhas[1][3] == "enviada"
    assert "https://l/1" in linhas[1][5]
    assert linhas[2][3] == "ignorada"
    assert "CPF inválido" in linhas[2][5]


def test_lote_desconhecido(app: App) -> None:
    assert app.http.get("/_lote/nada").status_code == 404
    assert app.http.get("/_lote/nada/relatorio.csv").status_code == 404
    assert app.http.post("/_lote/nada/cancelar").status_code == 404


def test_lote_exige_origem_da_pagina(app: App) -> None:
    ruim = {"Origin": "http://evil.example"}
    corpo = {"perfil": "cliente1", "csv": _csv(_aluno(1)), "modo": "enviar", "confirmarEnvio": True}
    assert app.http.post("/_lote/iniciar", json=corpo, headers=ruim).status_code == 403
    assert app.http.post("/_lote/validar", json={"csv": ""}, headers=ruim).status_code == 403
    assert app.http.post("/_lote/x/cancelar", headers=ruim).status_code == 403


# ============================================================ fumaça ===

IMPORTAR = ("POST", "/importar")
ASSINAR = ("POST", "/api/assinatura")
STATUS = ("GET", "/api/documento/77/status")
LINK = ("GET", "/api/documento/77/download")
CANCELAR = ("DELETE", "/api/assinatura/5")


def _fumaca_ok(upstream: Upstream) -> None:
    upstream.respostas[IMPORTAR] = (200, '{"documentoId": 77}')
    upstream.respostas[ASSINAR] = (201, '{"assinaturaId": 5, "conviteEnviado": false}')
    upstream.respostas[STATUS] = (200, '{"statusDescricao": "Pendente", "signatarios": [{}]}')
    upstream.respostas[LINK] = (200, '{"urlDownload": "https://x.example/arq"}')
    upstream.respostas[CANCELAR] = (200, "{}")


def _fumaca(app: App, perfil: str = "cliente1", **extra: Any) -> Any:
    return app.http.post("/_roteiro/fumaca", json={"perfil": perfil, "confirmar": True, **extra})


def _niveis(r: dict[str, Any]) -> dict[str, str]:
    return {p["id"]: p["nivel"] for p in r["passos"]}


def test_pdf_de_teste_e_valido() -> None:
    pdf = pdf_minimo()
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    inicio_xref = int(re.search(rb"startxref\n(\d+)", pdf).group(1))  # type: ignore[union-attr]
    assert pdf[inicio_xref:].startswith(b"xref")
    for n, pos in enumerate(re.findall(rb"(\d{10}) 00000 n", pdf), start=1):
        assert pdf[int(pos) :].startswith(f"{n} 0 obj".encode())


def test_fumaca_tudo_certo(app: App, upstream: Upstream) -> None:
    _fumaca_ok(upstream)
    r = _fumaca(app).json()
    assert r["sucesso"] is True
    assert _niveis(r) == dict.fromkeys(
        ("diagnostico", "importar", "assinatura", "status", "download", "cancelar"), "ok"
    )
    assert (r["documentoId"], r["assinaturaId"]) == (77, 5)


def test_fumaca_envia_o_pdf_e_a_assinatura_sem_convite(app: App, upstream: Upstream) -> None:
    _fumaca_ok(upstream)
    _fumaca(app)
    por_rota = {(c["method"], c["rota"]): c for c in upstream.recebidas}
    imp = por_rota[IMPORTAR]
    assert imp["ct"].startswith("multipart/form-data")
    assert b"%PDF-1.4" in imp["corpo"]
    assert "nomePasta=Teste+de+fuma%C3%A7a" in imp["path"] or "nomePasta=Teste%20de" in imp["path"]
    corpo = json.loads(por_rota[ASSINAR]["corpo"])
    assert corpo["documentoId"] == 77
    assert corpo["enviarConvite"] is False  # nunca manda e-mail
    assert corpo["signatarios"][0]["cpf"] == "52998224725"
    assert "motivo=" in por_rota[CANCELAR]["path"]


def test_fumaca_falha_ao_assinar_pula_o_resto_e_nao_cancela(app: App, upstream: Upstream) -> None:
    _fumaca_ok(upstream)
    upstream.respostas[ASSINAR] = (400, '{"retorno": "Signatário inválido"}')
    r = _fumaca(app).json()
    assert r["sucesso"] is False
    niveis = _niveis(r)
    assert niveis["importar"] == "ok"
    assert niveis["assinatura"] == "erro"
    assert (niveis["status"], niveis["download"], niveis["cancelar"]) == ("pulado",) * 3
    assert (
        "Signatário inválido" in next(p for p in r["passos"] if p["id"] == "assinatura")["detalhe"]
    )
    assert CANCELAR not in {(c["method"], c["rota"]) for c in upstream.recebidas}


def test_fumaca_limpa_a_assinatura_mesmo_se_um_passo_falhar(app: App, upstream: Upstream) -> None:
    _fumaca_ok(upstream)
    upstream.respostas[STATUS] = (500, "")
    r = _fumaca(app).json()
    assert r["sucesso"] is False
    niveis = _niveis(r)
    assert niveis["status"] == "erro"
    assert niveis["download"] == "pulado"
    assert niveis["cancelar"] == "ok"  # a limpeza roda sempre
    assert CANCELAR in {(c["method"], c["rota"]) for c in upstream.recebidas}


def test_fumaca_alerta_se_a_api_enviou_convite(app: App, upstream: Upstream) -> None:
    _fumaca_ok(upstream)
    upstream.respostas[ASSINAR] = (201, '{"assinaturaId": 5, "conviteEnviado": true}')
    r = _fumaca(app).json()
    assert _niveis(r)["assinatura"] == "erro"
    assert _niveis(r)["cancelar"] == "ok"


def test_fumaca_com_token_ruim_nao_escreve_nada(app: App, upstream: Upstream) -> None:
    r = _fumaca(app, "ruim").json()
    assert r["sucesso"] is False
    assert _niveis(r)["diagnostico"] == "erro"
    assert [c["method"] for c in upstream.recebidas if c["method"] != "GET"] == []


def test_fumaca_exige_confirmacao_e_respeita_a_protecao(app: App, upstream: Upstream) -> None:
    _fumaca_ok(upstream)
    sem = app.http.post("/_roteiro/fumaca", json={"perfil": "cliente1"})
    assert sem.status_code == 428
    assert _fumaca(app, "leitura").status_code == 403
    assert not [c for c in upstream.recebidas if c["method"] != "GET"]
    assert (
        _fumaca(app, "prod").json()["sucesso"] is True
    )  # protegido-confirmar aceita a confirmação


def test_fumaca_perfil_desconhecido(app: App) -> None:
    assert _fumaca(app, "ninguem").status_code == 404


def test_fumaca_exige_origem_da_pagina(app: App, upstream: Upstream) -> None:
    r = app.http.post(
        "/_roteiro/fumaca",
        json={"perfil": "cliente1", "confirmar": True},
        headers={"Origin": "http://evil.example"},
    )
    assert r.status_code == 403
    assert upstream.recebidas == []


def test_fumaca_que_falha_ainda_devolve_os_passos(app: App, upstream: Upstream) -> None:
    """Regressão: o resultado do roteiro não pode se confundir com o sucesso da chamada."""
    _fumaca_ok(upstream)
    upstream.respostas[IMPORTAR] = (500, '{"retorno": "erro ao importar"}')
    r = _fumaca(app).json()
    assert r["ok"] is True  # a chamada em si funcionou
    assert r["sucesso"] is False  # o roteiro é que falhou
    assert "erro ao importar" in next(p for p in r["passos"] if p["id"] == "importar")["detalhe"]
