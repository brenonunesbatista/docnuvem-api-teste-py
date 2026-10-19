"""Cancelar fluxos em massa: varre uma pasta (e subpastas) e só cancela os fluxos ativos."""

from __future__ import annotations

import csv
import io
import json
import time
from typing import Any

from .conftest import App, Upstream
from .test_excluir import LISTAGEM, _consulta, _corpo, _esperar

EXCLUIR = ("POST", "/api/sync/excluirDocumento")

# (documentoId, nome, status, assinaturaId)
DOCS = [
    (1, "pendente-a.pdf", "pendente", 101),
    (2, "assinado.pdf", "assinado", 102),
    (3, "expirado.pdf", "expirado", 103),
    (4, "solto.pdf", "sem_assinatura", None),
    (5, "pendente-b.pdf", "pendente", 105),
]


def _por_status(upstream: Upstream, docs: list[tuple[int, str, str, int | None]] = DOCS) -> None:
    """Uma API que respeita o filtro `status` e a paginação."""

    def resposta(q: dict[str, list[str]]) -> tuple[int, str]:
        status = q.get("status", [""])[0]
        itens = [
            {
                "documentoId": i,
                "nomeArquivo": n,
                "status": st,
                "assinaturaId": a,
                "diretorioId": 5,
            }
            for i, n, st, a in docs
            if not status or st == status
        ]
        pagina, tamanho = int(q["pagina"][0]), int(q["tamanho"][0])
        parte = itens[pagina * tamanho : (pagina + 1) * tamanho]
        corpo = {
            "total": len(itens),
            "temMais": (pagina + 1) * tamanho < len(itens),
            "documentos": parte,
        }
        return 200, json.dumps(corpo)

    upstream.dinamicas[LISTAGEM] = resposta


def _cancelamentos(upstream: Upstream) -> list[str]:
    return [c["rota"] for c in upstream.recebidas if c["method"] == "DELETE"]


def _excluidos(upstream: Upstream) -> list[dict[str, Any]]:
    return [c for c in upstream.recebidas if (c["method"], c["rota"]) == EXCLUIR]


def _cancelar(app: App, **extra: Any) -> dict[str, Any]:
    corpo = _corpo(modo="cancelar", subpastas=True, confirmacao="CANCELAR 2", **extra)
    r = app.http.post("/_excluir/iniciar", json=corpo)
    assert r.status_code == 200, r.text
    return _esperar(app, r.json()["id"])


def test_ensaio_conta_so_os_fluxos_ativos(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    r = app.http.post("/_excluir/ensaio", json=_corpo(modo="cancelar", subpastas=True)).json()
    assert r["modo"] == "cancelar" and r["total"] == 2
    assert r["porStatus"] == {"pendente": 2}
    assert r["serao"] == {"cancelados": 2, "excluidos": 0, "pulados": 0}
    assert r["confirmacao"] == "CANCELAR 2"
    assert _cancelamentos(upstream) == [] and _excluidos(upstream) == []


def test_incluir_expirados_soma_os_vencidos(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    corpo = _corpo(modo="cancelar", subpastas=True, incluirExpirados=True)
    r = app.http.post("/_excluir/ensaio", json=corpo).json()
    assert r["total"] == 3 and r["porStatus"] == {"pendente": 2, "expirado": 1}
    assert r["confirmacao"] == "CANCELAR 3"


def test_cancela_os_fluxos_e_nao_exclui_nada(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    fim = _cancelar(app)
    assert (fim["modo"], fim["estado"]) == ("cancelar", "concluido")
    assert (fim["fluxosCancelados"], fim["excluidos"], fim["nFalhas"]) == (2, 0, 0)
    assert sorted(_cancelamentos(upstream)) == ["/api/assinatura/101", "/api/assinatura/105"]
    assert _excluidos(upstream) == []  # nenhum documento vai para a lixeira
    pedido = next(c for c in upstream.recebidas if c["method"] == "DELETE")
    assert "Cancelamento em massa" in _consulta(pedido)["motivo"]


def test_a_leitura_pede_so_pendentes_e_com_subpastas(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    _cancelar(app)
    consultas = [c["path"] for c in upstream.recebidas if c["rota"] == "/api/documentos"]
    assert consultas and all("status=pendente" in c for c in consultas)
    assert all("incluirSubpastas=true" in c for c in consultas)


def test_com_expirados_cancela_tambem_os_vencidos(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    corpo = _corpo(modo="cancelar", subpastas=True, incluirExpirados=True, confirmacao="CANCELAR 3")
    fim = _esperar(app, app.http.post("/_excluir/iniciar", json=corpo).json()["id"])
    assert fim["fluxosCancelados"] == 3
    assert sorted(_cancelamentos(upstream)) == [
        "/api/assinatura/101",
        "/api/assinatura/103",
        "/api/assinatura/105",
    ]  # o assinado (102) nunca é tocado


def test_documento_assinado_nunca_e_cancelado(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    _cancelar(app)
    assert "/api/assinatura/102" not in _cancelamentos(upstream)


def test_confirmacao_do_modo_errado_nao_vale(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    for frase in ("EXCLUIR 2", "CANCELAR 5", ""):
        corpo = _corpo(modo="cancelar", subpastas=True, confirmacao=frase)
        r = app.http.post("/_excluir/iniciar", json=corpo)
        assert r.status_code == 428, frase
    assert _cancelamentos(upstream) == []


def test_fluxo_que_ja_nao_estava_ativo_e_pulado(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    upstream.respostas[("DELETE", "/api/assinatura/101")] = (409, '{"retorno": "não cancelável"}')
    fim = _cancelar(app)
    assert (fim["fluxosCancelados"], fim["pulados"], fim["nFalhas"]) == (1, 1, 0)


def test_falha_isolada_nao_derruba_o_resto(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    upstream.respostas[("DELETE", "/api/assinatura/101")] = (500, "")
    fim = _cancelar(app)
    assert (fim["fluxosCancelados"], fim["nFalhas"]) == (1, 1)
    assert fim["falhas"][0]["documentoId"] == 1
    assert _cancelamentos(upstream).count("/api/assinatura/101") == 1  # sem repetição


def test_sem_permissao_para_na_primeira_recusa(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    upstream.respostas[("DELETE", "/api/assinatura/101")] = (401, '{"retorno": "token"}')
    fim = _cancelar(app)
    assert fim["estado"] == "erro" and "sem permissão" in fim["erro"]
    assert _cancelamentos(upstream) == ["/api/assinatura/101"]


def test_perfis_protegidos(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    corpo = _corpo(modo="cancelar", subpastas=True, confirmacao="CANCELAR 2")
    assert (
        app.http.post("/_excluir/iniciar", json={**corpo, "perfil": "leitura"}).status_code == 403
    )
    assert app.http.post("/_excluir/iniciar", json={**corpo, "perfil": "prod"}).status_code == 428
    assert _cancelamentos(upstream) == []


def test_modo_invalido(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    assert app.http.post("/_excluir/ensaio", json=_corpo(modo="apagar")).status_code == 400


def test_relatorio_csv_do_cancelamento(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    corpo = _corpo(modo="cancelar", subpastas=True, confirmacao="CANCELAR 2")
    id_ = app.http.post("/_excluir/iniciar", json=corpo).json()["id"]
    _esperar(app, id_)
    texto = app.http.get(f"/_excluir/{id_}/relatorio.csv").content.decode("utf-8-sig")
    linhas = list(csv.reader(io.StringIO(texto), delimiter=";"))
    assert [ln[4] for ln in linhas[1:]] == ["cancelado", "cancelado"]
    assert {ln[1] for ln in linhas[1:]} == {"pendente-a.pdf", "pendente-b.pdf"}


def test_verificacao_em_segundo_plano_do_modo_cancelar(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    corpo = _corpo(modo="cancelar", subpastas=True)
    v = app.http.post("/_excluir/verificar", json=corpo).json()
    for _ in range(300):
        sn = app.http.get(f"/_excluir/verificacao/{v['id']}").json()
        if sn["estado"] != "lendo":
            break
        time.sleep(0.03)
    assert sn["estado"] == "pronta" and sn["resultado"]["modo"] == "cancelar"
    assert sn["resultado"]["total"] == 2
    antes = len([c for c in upstream.recebidas if c["rota"] == "/api/documentos"])
    ini = _corpo(modo="cancelar", subpastas=True, confirmacao="CANCELAR 2", verificacaoId=v["id"])
    fim = _esperar(app, app.http.post("/_excluir/iniciar", json=ini).json()["id"])
    depois = len([c for c in upstream.recebidas if c["rota"] == "/api/documentos"])
    assert fim["fluxosCancelados"] == 2
    assert depois - antes == 1  # só a contagem; a lista veio da verificação


def test_verificacao_de_outro_modo_nao_e_reaproveitada(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    v = app.http.post("/_excluir/verificar", json=_corpo(subpastas=True)).json()  # modo excluir
    for _ in range(300):
        if app.http.get(f"/_excluir/verificacao/{v['id']}").json()["estado"] != "lendo":
            break
        time.sleep(0.03)
    ini = _corpo(modo="cancelar", subpastas=True, confirmacao="CANCELAR 2", verificacaoId=v["id"])
    fim = _esperar(app, app.http.post("/_excluir/iniciar", json=ini).json()["id"])
    assert fim["fluxosCancelados"] == 2 and fim["excluidos"] == 0


def test_duas_pastas_somam_os_fluxos(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    corpo = _corpo(modo="cancelar", diretorioIds=[5, 6], subpastas=True)
    r = app.http.post("/_excluir/ensaio", json=corpo).json()
    assert r["total"] == 2  # a mesma lista nas duas pastas: o documento conta uma vez


def test_o_log_diz_cancelamento_e_nao_exclusao(app: App, upstream: Upstream) -> None:
    _por_status(upstream)
    upstream.respostas[("DELETE", "/api/assinatura/101")] = (500, "")
    _cancelar(app)
    textos = [str(e["body"]) for e in app.historico.todas()]  # type: ignore[union-attr]
    assert any(
        "Cancelamento de fluxos em massa iniciado" in t and "só cancela" in t for t in textos
    )
    assert any("Cancelamento de fluxos em massa de pasta 5" in t for t in textos)
    assert not any("Exclusão em massa" in t for t in textos)


# ---- a API real: a listagem traz `statusAssinatura` e NÃO traz `assinaturaId` ---------------


def _lista_como_a_api_real(upstream: Upstream) -> None:
    def resposta(q: dict[str, list[str]]) -> tuple[int, str]:
        status = q.get("status", [""])[0]
        itens = [
            {"documentoId": i, "nomeArquivo": n, "statusAssinatura": st, "diretorioId": 5}
            for i, n, st, _ in DOCS
            if not status or st == status
        ]
        return 200, json.dumps({"total": len(itens), "temMais": False, "documentos": itens})

    upstream.dinamicas[LISTAGEM] = resposta
    for i, _, _, ass in DOCS:
        if ass is not None:
            body = json.dumps({"documentoId": i, "assinaturaId": ass, "status": "pendente"})
            upstream.respostas[("GET", f"/api/documento/{i}/status")] = (200, body)


def test_api_real_o_id_do_fluxo_vem_do_status_do_documento(app: App, upstream: Upstream) -> None:
    _lista_como_a_api_real(upstream)
    r = app.http.post("/_excluir/ensaio", json=_corpo(modo="cancelar", subpastas=True)).json()
    assert r["total"] == 2 and r["fluxosAtivos"] == 2
    fim = _cancelar(app)
    assert fim["fluxosCancelados"] == 2 and fim["nFalhas"] == 0
    assert sorted(_cancelamentos(upstream)) == ["/api/assinatura/101", "/api/assinatura/105"]


def test_api_real_se_nao_acha_o_fluxo_nao_cancela_nem_inventa(app: App, upstream: Upstream) -> None:
    _lista_como_a_api_real(upstream)
    upstream.respostas[("GET", "/api/documento/1/status")] = (500, "")  # não dá para saber
    upstream.respostas[("GET", "/api/documento/5/status")] = (200, '{"status": "sem_assinatura"}')
    fim = _cancelar(app)
    assert (fim["fluxosCancelados"], fim["nFalhas"], fim["pulados"]) == (0, 1, 1)
    assert _cancelamentos(upstream) == []


def test_so_cancelar_nunca_toca_em_assinado_mesmo_que_a_api_ignore_o_filtro(
    app: App, upstream: Upstream
) -> None:
    def sem_filtro(q: dict[str, list[str]]) -> tuple[int, str]:  # devolve tudo, como um bug da API
        itens = [
            {"documentoId": i, "nomeArquivo": n, "statusAssinatura": st, "assinaturaId": a}
            for i, n, st, a in DOCS
        ]
        return 200, json.dumps({"total": len(itens), "temMais": False, "documentos": itens})

    upstream.dinamicas[LISTAGEM] = sem_filtro
    corpo = _corpo(modo="cancelar", subpastas=True, confirmacao="CANCELAR 5")
    fim = _esperar(app, app.http.post("/_excluir/iniciar", json=corpo).json()["id"])
    assert fim["fluxosCancelados"] == 3  # pendente-a, expirado e pendente-b: os cancelável
    assert "/api/assinatura/102" not in _cancelamentos(upstream)  # o assinado, jamais


def test_exclusao_na_api_real_cancela_o_fluxo_antes_de_excluir(
    app: App, upstream: Upstream
) -> None:
    """O bug que isto pega: sem `assinaturaId` na listagem, o fluxo ativo nunca era cancelado."""
    _lista_como_a_api_real(upstream)
    corpo = _corpo(subpastas=True, confirmacao="EXCLUIR 5", cancelarFluxos=True)
    fim = _esperar(app, app.http.post("/_excluir/iniciar", json=corpo).json()["id"])
    assert fim["fluxosCancelados"] == 2 and fim["excluidos"] == 5
    ordem = [
        (c["method"], c["rota"])
        for c in upstream.recebidas
        if c["method"] == "DELETE" or (c["method"], c["rota"]) == EXCLUIR
    ]
    assert ordem.index(("DELETE", "/api/assinatura/101")) < ordem.index(EXCLUIR)


def test_exclusao_nao_exclui_documento_cujo_fluxo_nao_deu_para_cancelar(
    app: App, upstream: Upstream
) -> None:
    _lista_como_a_api_real(upstream)
    upstream.respostas[("GET", "/api/documento/1/status")] = (500, "")
    corpo = _corpo(subpastas=True, confirmacao="EXCLUIR 5", cancelarFluxos=True)
    fim = _esperar(app, app.http.post("/_excluir/iniciar", json=corpo).json()["id"])
    ids = {_consulta(c)["documentoId"] for c in _excluidos(upstream)}
    assert "1" not in ids  # ficaria com o fluxo ativo: não é excluído
    assert fim["nFalhas"] == 1 and fim["excluidos"] == 4
