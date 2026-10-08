"""A página num navegador de verdade: o que o usuário clica e vê.

Rode com `pytest -m browser`. Cada teste também falha se a página soltar um erro de JavaScript.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect

from ..conftest import App, Upstream
from .conftest import Pagina

pytestmark = pytest.mark.browser

IMPORTAR = ("POST", "/importar")
ASSINAR = ("POST", "/api/assinatura")
STATUS = ("GET", "/api/documento/77/status")
LINK = ("GET", "/api/documento/77/download")
CANCELAR = ("DELETE", "/api/assinatura/5")


def _enviadas(upstream: Upstream, metodo: str, rota: str) -> list[dict[str, Any]]:
    return [c for c in upstream.recebidas if c["method"] == metodo and c["rota"] == rota]


# ------------------------------------------------------------------ base ---


def test_carrega_com_os_perfis_e_o_tema_escuro(pagina: Pagina) -> None:
    page = pagina.page
    expect(page.locator(".banner")).to_contain_text("API real")
    opcoes = page.locator("#perfil option").all_inner_texts()
    assert opcoes == ["cliente1", "ruim", "fora", "prod", "leitura"]
    expect(page.locator(".app")).to_have_attribute("data-theme", "dark")
    expect(page.locator(".top .pill").first).to_contain_text("API online")


def test_tema_alterna_e_fica_lembrado(abrir: Any) -> None:
    pagina = abrir()
    page = pagina.page
    page.get_by_role("button", name="Mudar para tema claro").click()
    expect(page.locator(".app")).to_have_attribute("data-theme", "light")
    page.reload()
    page.wait_for_selector("#perfil option", state="attached")
    expect(page.locator(".app")).to_have_attribute("data-theme", "light")


def test_todas_as_telas_do_menu_abrem_sem_erro(pagina: Pagina) -> None:
    page = pagina.page
    total = page.locator(".navb").count()
    assert total >= 20
    for i in range(total):
        item = page.locator(".navb").nth(i)
        rotulo = item.inner_text().strip()
        item.click()
        expect(page.locator("main")).not_to_be_empty()
        assert pagina.texto().strip(), f"a tela {rotulo!r} abriu vazia"
        expect(item).to_have_attribute("aria-current", "page")


def test_menu_recolhe_e_expande(pagina: Pagina) -> None:
    page = pagina.page
    page.get_by_role("button", name="Recolher menu").click()
    expect(page.locator(".side")).to_have_class(re.compile(r"\bmin\b"))
    page.get_by_role("button", name="Expandir menu").click()
    expect(page.locator(".side")).not_to_have_class(re.compile(r"\bmin\b"))


# ---------------------------------------------------------- diagnóstico ---


def test_diagnostico_mostra_cada_verificacao(pagina: Pagina) -> None:
    pagina.ir("Diagnóstico")
    expect(pagina.page.locator(".dg")).to_have_count(5)
    texto = pagina.texto()
    for titulo in ("API acessível", "Token aceito", "Modelos de documento", "Pastas"):
        assert titulo in texto


def test_diagnostico_de_perfil_com_token_ruim(pagina: Pagina) -> None:
    pagina.page.select_option("#perfil", "ruim")
    pagina.ir("Diagnóstico")
    expect(pagina.page.locator("main")).to_contain_text("Com problema")
    expect(pagina.page.locator("main")).to_contain_text("Causa provável")
    expect(pagina.page.locator("main")).to_contain_text("Pulado")


# ------------------------------------------------------------ importação ---


def test_gerar_pdf_de_teste_e_importar(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    upstream.respostas[IMPORTAR] = (200, '{"documentoId": 99}')
    pagina.ir("Importar arquivo")
    page.get_by_role("button", name="Gerar PDF de teste").click()
    expect(page.locator(".dfile")).to_contain_text("teste-100KB-1p.pdf")
    expect(page.locator("#f-importar-nomeArquivo")).to_have_value("teste-100KB-1p.pdf")
    page.fill("#f-importar-nomePasta", "Contratos")
    pagina.botao("Importar arquivo").click()
    expect(page.locator("main")).to_contain_text("documentoId")
    (envio,) = _enviadas(upstream, *IMPORTAR)
    assert envio["ct"].startswith("multipart/form-data")
    assert b"%PDF-1.4" in envio["corpo"]
    assert len(envio["corpo"]) > 100 * 1024  # o PDF de 100 KB foi inteiro


def test_pdf_de_teste_em_outro_tamanho(pagina: Pagina) -> None:
    page = pagina.page
    pagina.ir("Importar arquivo")
    page.get_by_label("Tamanho do PDF de teste").select_option("1048576")
    page.get_by_label("Páginas do PDF de teste").select_option("3")
    page.get_by_role("button", name="Gerar PDF de teste").click()
    expect(page.locator(".dfile")).to_contain_text("teste-1MB-3p.pdf")
    expect(page.locator(".dfile")).to_contain_text("1 MB")


# ------------------------------------------------------- perfil protegido ---


def test_perfil_protegido_pede_confirmacao_antes_de_alterar(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    page.select_option("#perfil", "prod")
    expect(page.locator(".top")).to_contain_text("Produção")
    pagina.ir("Cancelar assinatura")
    page.fill("#f-cancelar-assinaturaId", "5")
    page.fill("#f-cancelar-confirm", "5")
    page.locator("main button.dng", has_text="Cancelar assinatura").click()
    modal = page.locator(".modalb")
    expect(modal).to_contain_text("Perfil protegido: prod")
    expect(modal).to_contain_text("DELETE /api/assinatura/5")
    modal.get_by_role("button", name="Cancelar").click()
    expect(modal).to_have_count(0)
    assert _enviadas(upstream, *CANCELAR) == []  # nada chegou à API
    page.locator("main button.dng", has_text="Cancelar assinatura").click()
    page.locator(".modalb").get_by_role("button", name="Enviar mesmo assim").click()
    expect(page.locator(".rh")).to_be_visible()
    assert len(_enviadas(upstream, *CANCELAR)) == 1


def test_perfil_somente_leitura_recusa_a_escrita(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    page.select_option("#perfil", "leitura")
    pagina.ir("Cancelar assinatura")
    page.fill("#f-cancelar-assinaturaId", "5")
    page.fill("#f-cancelar-confirm", "5")
    page.locator("main button.dng", has_text="Cancelar assinatura").click()
    expect(page.locator(".rh")).to_contain_text("403")
    expect(page.locator("main")).to_contain_text("só aceita leituras")
    assert _enviadas(upstream, *CANCELAR) == []


# ---------------------------------------------------------------- histórico ---


def test_favorito_do_historico_sobrevive_a_recarregar(abrir: Any) -> None:
    pagina = abrir()
    page = pagina.page
    pagina.ir("Modelos")  # a tela consulta sozinha e a chamada entra no log
    page.get_by_role("button", name="Abrir log de chamadas").click()
    expect(page.locator(".lwrap")).to_have_count(1)
    page.locator(".lwrap .ib").click()
    expect(page.locator(".lwrap .ib")).to_have_attribute("aria-pressed", "true")
    page.reload()
    page.wait_for_selector("#perfil option", state="attached")
    page.get_by_role("button", name="Abrir log de chamadas").click()
    expect(page.locator(".lwrap")).to_have_count(1)
    expect(page.locator(".lwrap .ib")).to_have_attribute("aria-pressed", "true")


def test_relato_de_bug_copia_o_curl_com_o_token_mascarado(pagina: Pagina) -> None:
    page = pagina.page
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    pagina.ir("Modelos")
    page.get_by_role("button", name="Copiar relato de bug").click()
    expect(page.locator(".toast")).to_contain_text("Relato copiado")
    copiado = page.evaluate("navigator.clipboard.readText()")
    assert "RELATO DE PROBLEMA" in copiado
    assert "Bearer ***" in copiado
    assert "tok-ok-1234" not in copiado  # o token de verdade nunca aparece


# ------------------------------------------------------------- baixar pasta ---


def test_baixar_pasta_grava_os_arquivos(pagina: Pagina, upstream: Upstream, tmp_path: Path) -> None:
    page = pagina.page
    docs = [{"documentoId": i, "nomeArquivo": f"doc{i}.pdf", "diretorioId": 5} for i in (1, 2)]
    upstream.respostas[("GET", "/api/documentos")] = (
        200,
        json.dumps({"total": 2, "temMais": False, "documentos": docs}),
    )
    for i in (1, 2):
        link = json.dumps({"urlDownload": f"{upstream.base}/arquivo/{i}"})
        upstream.respostas[("GET", f"/api/documento/{i}/download")] = (200, link)
        upstream.respostas[("GET", f"/arquivo/{i}")] = (200, f"conteudo {i}")
    pagina.ir("Baixar pasta")
    page.fill("#bx-dir", "0")  # 0 é a raiz de Meus documentos
    page.fill("#bx-dest", str(tmp_path / "saida"))
    pagina.botao("Só contar os documentos").click()
    expect(page.locator("main")).to_contain_text("2 documento(s) em 1 pasta(s). Nada foi baixado.")
    pagina.botao("Baixar", exato=True).click()
    expect(page.locator("main")).to_contain_text("2 baixado(s)")
    assert (tmp_path / "saida" / "doc1.pdf").read_text() == "conteudo 1"
    assert (tmp_path / "saida" / "doc2.pdf").read_text() == "conteudo 2"


def test_baixar_pasta_recusa_destino_relativo(pagina: Pagina) -> None:
    page = pagina.page
    pagina.ir("Baixar pasta")
    page.fill("#bx-dir", "5")
    page.fill("#bx-dest", "pasta/relativa")
    pagina.botao("Baixar", exato=True).click()
    expect(page.locator("main .dangerbox")).to_contain_text("caminho completo")


# ------------------------------------------------------------------ rotina ---

CSV = (
    "codigoMatricula;nome;cpf;email\n"
    "1;Ana;529.982.247-25;ana@exemplo.com.br\n"
    "2;Beto;111.444.777-35;beto@exemplo.com.br\n"
    "3;Sem CPF;;x@y.co\n"
)


def test_lote_valida_o_csv_e_consulta(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    pagina.ir("Lote para escolas")
    page.set_input_files(
        "#lt-arq", files=[{"name": "alunos.csv", "mimeType": "text/csv", "buffer": CSV.encode()}]
    )
    expect(page.locator("main")).to_contain_text("3 linha(s)")
    expect(page.locator("main")).to_contain_text("2 válida(s)")
    expect(page.locator("main")).to_contain_text("CPF inválido")
    page.select_option("#lt-modo", "consultar")
    page.fill("#lt-int", "0")
    pagina.botao("Consultar 2 aluno(s)").click()
    expect(page.locator("main")).to_contain_text("2 consultado(s)")
    assert len(_enviadas(upstream, "GET", "/api/solicitacaoAluno/consultarStatus")) == 2


def test_lote_pede_confirmacao_antes_de_enviar_emails(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    pagina.ir("Lote para escolas")
    page.set_input_files(
        "#lt-arq", files=[{"name": "alunos.csv", "mimeType": "text/csv", "buffer": CSV.encode()}]
    )
    page.fill("#lt-int", "0")
    pagina.botao("Enviar 2 solicitação(ões)").click()
    expect(page.locator("main [role=alertdialog]")).to_contain_text("e-mail(s) REAIS")
    assert _enviadas(upstream, "POST", "/api/solicitacaoAluno/solicitarEnvioDocumentos") == []
    pagina.botao("Confirmar e enviar").click()
    expect(page.locator("main")).to_contain_text("2 enviada(s)")
    assert len(_enviadas(upstream, "POST", "/api/solicitacaoAluno/solicitarEnvioDocumentos")) == 2


def test_lote_nao_envia_em_perfil_somente_leitura(pagina: Pagina) -> None:
    page = pagina.page
    page.select_option("#perfil", "leitura")
    pagina.ir("Lote para escolas")
    page.set_input_files(
        "#lt-arq", files=[{"name": "alunos.csv", "mimeType": "text/csv", "buffer": CSV.encode()}]
    )
    expect(page.locator("main .dangerbox")).to_contain_text("somente leitura")
    expect(pagina.botao("Enviar 2 solicitação(ões)")).to_be_disabled()


def _fumaca_ok(upstream: Upstream) -> None:
    upstream.respostas[IMPORTAR] = (200, '{"documentoId": 77}')
    upstream.respostas[ASSINAR] = (201, '{"assinaturaId": 5, "conviteEnviado": false}')
    upstream.respostas[STATUS] = (200, '{"statusDescricao": "Pendente", "signatarios": [{}]}')
    upstream.respostas[LINK] = (200, '{"urlDownload": "https://x.example/arq"}')
    upstream.respostas[CANCELAR] = (200, "{}")


def test_teste_de_fumaca_exige_o_aceite_e_roda_o_roteiro(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _fumaca_ok(upstream)
    pagina.ir("Teste de fumaça")
    rodar = pagina.botao("Rodar teste de fumaça")
    expect(rodar).to_be_disabled()  # sem o aceite não roda
    page.locator("main label.sw").first.click()
    rodar.click()
    expect(page.locator("main")).to_contain_text("Tudo certo")
    assert page.locator(".dg .pill.ok").count() == 6
    assert len(_enviadas(upstream, *CANCELAR)) == 1


def test_teste_de_fumaca_mostra_o_passo_que_falhou(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _fumaca_ok(upstream)
    upstream.respostas[ASSINAR] = (400, '{"retorno": "Signatário inválido"}')
    pagina.ir("Teste de fumaça")
    page.locator("main label.sw").first.click()
    pagina.botao("Rodar teste de fumaça").click()
    expect(page.locator("main")).to_contain_text("Com problema")
    expect(page.locator("main")).to_contain_text("Signatário inválido")  # a causa aparece


def test_comparar_dois_perfis(pagina: Pagina) -> None:
    page = pagina.page
    pagina.ir("Comparar instâncias")
    page.select_option("#cm-a", "cliente1")
    page.select_option("#cm-b", "prod")
    pagina.botao("Comparar", exato=True).click()
    expect(page.locator("main")).to_contain_text("Nenhuma diferença")
    page.select_option("#cm-b", "ruim")
    page.select_option("#cm-t", "diagnostico")
    pagina.botao("Comparar", exato=True).click()
    expect(page.locator("main")).to_contain_text("Token aceito")
    expect(page.locator("main")).to_contain_text("diferente(s)")


def test_vigia_acompanha_os_pendentes(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    upstream.respostas[("GET", "/api/documentos")] = (
        200,
        json.dumps({"total": 1, "documentos": [{"documentoId": 3, "nomeArquivo": "alvo.pdf"}]}),
    )
    corpo = {
        "documentoId": 3,
        "nomeArquivo": "alvo.pdf",
        "status": "pendente",
        "signatarios": [{"nome": "Ana", "email": "a@x.co", "status": "pendente"}],
    }
    upstream.respostas[("GET", "/api/documento/3/status")] = (200, json.dumps(corpo))
    pagina.ir("Vigia de assinaturas")
    pagina.botao("Iniciar vigia").click()
    expect(page.locator("main")).to_contain_text("Vigiando")
    expect(page.locator("main")).to_contain_text("alvo.pdf")
    expect(page.locator("main")).to_contain_text("1 documento(s) acompanhado(s)")
    pagina.botao("Parar").click()
    expect(page.locator("main")).to_contain_text("Parado")


# ------------------------------------------------------ gerenciar perfis ---


def test_perfis_criar_editar_e_remover(abrir: Any, app_cfg: App) -> None:
    pagina = abrir(app_cfg.base)
    page = pagina.page
    pagina.ir("Perfis")
    expect(page.locator(".pfr")).to_have_count(2)
    pagina.botao("Novo perfil").click()
    page.fill("#pf-nome", "novo")
    page.fill("#pf-inst", "Novo")
    page.fill("#pf-base", "servidor-sem-http")
    page.fill("#pf-token", "tk-123")
    pagina.botao("Salvar no config.json").click()
    expect(page.locator("main .dangerbox")).to_contain_text("http://")  # validação do servidor
    page.fill("#pf-base", "http://api:8083")
    page.select_option("#pf-prot", "bloquear")
    pagina.botao("Salvar no config.json").click()
    expect(page.locator(".pfr")).to_have_count(3)
    expect(page.locator("#perfil option")).to_have_text(["a", "b", "novo"])
    expect(page.locator(".pfr", has_text="novo")).to_contain_text("Produção · só leitura")
    # o token nunca aparece, nem mascarado além dos 4 últimos caracteres
    assert "tk-123" not in page.content()
    card = page.locator(".pfr", has_text="novo")
    card.get_by_role("button", name="Editar").click()
    expect(page.locator("#pf-token")).to_have_value("")  # token atual não volta para a tela
    page.fill("#pf-nome", "renomeado")
    pagina.botao("Salvar no config.json").click()
    expect(page.locator(".pfr", has_text="renomeado")).to_have_count(1)
    card = page.locator(".pfr", has_text="renomeado")
    card.get_by_role("button", name="Remover").click()
    card.get_by_role("button", name="Confirmar remoção").click()  # exige o segundo clique
    expect(page.locator(".pfr")).to_have_count(2)


def test_baixar_pasta_com_filtros_de_status_e_data(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    upstream.respostas[("GET", "/api/documentos")] = (
        200,
        json.dumps(
            {"total": 1, "temMais": False, "documentos": [{"documentoId": 1, "diretorioId": 5}]}
        ),
    )
    pagina.ir("Baixar pasta")
    page.fill("#bx-dir", "5")
    page.select_option("#bx-st", "assinado")
    page.fill("#bx-di", "2026-01-01")
    page.fill("#bx-df", "2026-03-31")
    pagina.botao("Só contar os documentos").click()
    expect(page.locator("main")).to_contain_text("Filtros: status assinado, a partir de 2026-01-01")
    caminho = _enviadas(upstream, "GET", "/api/documentos")[0]["path"]
    assert "status=assinado" in caminho
    assert "dataInicio=2026-01-01" in caminho
    assert "dataFim=2026-03-31" in caminho
