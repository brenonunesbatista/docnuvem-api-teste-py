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


def test_importar_pasta_mostra_o_plano_confirma_e_envia(
    pagina: Pagina, upstream: Upstream, tmp_path: Path
) -> None:
    page = pagina.page
    origem = tmp_path / "cliente"
    (origem / "Contratos").mkdir(parents=True)
    (origem / "raiz.pdf").write_bytes(b"%PDF raiz")
    (origem / "Contratos" / "um.pdf").write_bytes(b"%PDF um")
    (origem / "programa.exe").write_bytes(b"MZ")
    upstream.respostas[IMPORTAR] = (200, '{"documentoId": 5}')
    pagina.ir("Importar pasta")
    page.fill("#ip-origem", str(origem))
    page.fill("#ip-pasta", "Cliente X")
    pagina.botao("Ver o que será enviado").click()
    expect(page.locator("main")).to_contain_text("2 arquivo(s) em 2 pasta(s)")
    expect(page.locator("main")).to_contain_text("extensão não aceita (.exe)")
    expect(page.locator("main")).to_contain_text("/Meus documentos/Cliente X")  # o plano é visível
    assert _enviadas(upstream, *IMPORTAR) == []  # ver o plano nunca envia
    page.fill("#ip-int", "0")
    pagina.botao("Enviar 2 arquivo(s)").click()
    expect(page.locator("main [role=alertdialog]")).to_contain_text("API não permite excluir")
    assert _enviadas(upstream, *IMPORTAR) == []
    pagina.botao("Confirmar e enviar").click()
    expect(page.locator("main")).to_contain_text("2 enviado(s)")
    assert len(_enviadas(upstream, *IMPORTAR)) == 2
    # um segundo envio da mesma pasta não duplica nada
    pagina.botao("Ver o que será enviado").click()
    expect(page.locator("main")).to_contain_text("2 já enviado(s) antes")
    expect(pagina.botao("Enviar 0 arquivo(s)")).to_be_disabled()


def test_importar_pasta_nao_envia_em_perfil_somente_leitura(pagina: Pagina, tmp_path: Path) -> None:
    page = pagina.page
    (tmp_path / "a.pdf").write_bytes(b"x")
    page.select_option("#perfil", "leitura")
    pagina.ir("Importar pasta")
    page.fill("#ip-origem", str(tmp_path))
    page.fill("#ip-pasta", "X")
    pagina.botao("Ver o que será enviado").click()
    expect(page.locator("main .dangerbox")).to_contain_text("somente leitura")
    expect(pagina.botao("Enviar 1 arquivo(s)")).to_be_disabled()


# ------------------------------------------------- estrutura de pastas ---

OFICIAL_PASTAS = ("GET", "/api/diretorios")
SINC_PASTAS = ("POST", "/api/sync/listarDiretoriosFilhos")
LOGIN_SYNC = ("POST", "/api/sync/login")
SENHA = "S3nha-MUITO-secreta!"
TOKEN_USUARIO = "tok-USUARIO-0123456789"


def _arvore_de_pastas(upstream: Upstream) -> None:
    """Meus documentos > Contratos (10) > 2024 (12); Atas (11). O endpoint oficial recusa."""
    filhos = {
        "0": [
            (10, "Contratos", "/Meus documentos/Contratos"),
            (11, "Atas", "/Meus documentos/Atas"),
        ],
        "10": [(12, "2024", "/Meus documentos/Contratos/2024")],
    }

    def sinc(q: dict[str, list[str]]) -> tuple[int, str]:
        itens = [
            {"id": i, "nome": n, "caminhoNomesPais": c, "lixeira": False}
            for i, n, c in filhos.get(q["diretorioPaiId"][0], [])
        ]
        return 200, json.dumps({"diretorios": itens, "temMais": False})

    upstream.respostas[OFICIAL_PASTAS] = (401, "")
    upstream.dinamicas[SINC_PASTAS] = sinc


def _seletor(page: Any) -> Any:
    return page.locator("[role=dialog][aria-label*='estrutura']")


def test_escolher_a_pasta_navegando_pela_estrutura(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _arvore_de_pastas(upstream)
    pagina.ir("Criar de modelo")
    page.get_by_role("button", name="Escolher na estrutura de pastas…").first.click()
    modal = _seletor(page)
    expect(modal).to_contain_text("Escolher pasta")
    expect(modal.locator(".pkcard")).to_have_count(2)  # cartões, não uma lista aninhada
    expect(modal).to_contain_text("endpoint de pastas da API recusou")  # avisa de onde veio
    modal.get_by_role("button", name="Abrir Contratos").click()  # entra na pasta
    expect(modal.locator(".pkcrumbs")).to_contain_text("Meus documentos")
    expect(modal.locator(".pkcrumbs .cur")).to_have_text("Contratos")
    expect(modal.locator(".pkcard")).to_have_count(1)
    modal.get_by_role("button", name="Abrir 2024").click()
    expect(modal.locator("[class*=empty]")).to_contain_text("não tem subpastas")
    expect(modal).to_contain_text("/Meus documentos/Contratos/2024")  # a pasta atual
    modal.get_by_role("button", name="Escolher esta pasta").click()
    expect(modal).to_have_count(0)
    expect(page.locator("#f-template-nomePasta")).to_have_value("2024")
    expect(page.locator("#f-template-nomePastaPai")).to_have_value("/Meus documentos/Contratos")


def test_escolher_direto_no_cartao_e_voltar_pelo_caminho(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _arvore_de_pastas(upstream)
    pagina.ir("Criar de modelo")
    page.get_by_role("button", name="Escolher na estrutura de pastas…").first.click()
    modal = _seletor(page)
    modal.get_by_role("button", name="Abrir Contratos").click()
    modal.get_by_role("button", name="Meus documentos", exact=True).click()  # migalha de pão
    expect(modal.locator(".pkcard")).to_have_count(2)
    modal.get_by_role("button", name="Escolher Atas").click()  # sem precisar entrar
    expect(page.locator("#f-template-nomePasta")).to_have_value("Atas")
    expect(page.locator("#f-template-nomePastaPai")).to_have_value("/Meus documentos")


def test_filtrar_as_pastas_do_nivel(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _arvore_de_pastas(upstream)
    pagina.ir("Criar de modelo")
    page.get_by_role("button", name="Escolher na estrutura de pastas…").first.click()
    modal = _seletor(page)
    modal.get_by_label("Filtrar as pastas desta pasta").fill("ata")
    expect(modal.locator(".pkcard")).to_have_count(1)
    expect(modal).to_contain_text("1 de 2 pasta(s)")
    modal.get_by_label("Filtrar as pastas desta pasta").fill("zzz")
    expect(modal).to_contain_text("Nenhuma pasta com esse nome")


def test_na_estrutura_a_raiz_so_vale_quando_o_campo_e_um_id(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _arvore_de_pastas(upstream)
    modal = _seletor(page)
    pagina.ir("Criar de modelo")  # campo de nome de pasta: a raiz não serve
    page.get_by_role("button", name="Escolher na estrutura de pastas…").first.click()
    expect(modal.get_by_role("button", name="Escolher esta pasta")).to_be_disabled()
    expect(modal).to_contain_text("A raiz não serve como pasta do formulário")
    page.keyboard.press("Escape")
    expect(modal).to_have_count(0)  # Esc fecha
    pagina.ir("Baixar pasta")  # campo de ID: a raiz é o 0
    page.get_by_role("button", name="Escolher na estrutura de pastas…").click()
    modal.get_by_role("button", name="Escolher esta pasta (ID 0)").click()
    expect(page.locator("#bx-dir")).to_have_value("0")
    page.get_by_role("button", name="Escolher na estrutura de pastas…").click()
    modal.get_by_role("button", name="Escolher Atas").click()
    expect(page.locator("#bx-dir")).to_have_value("11")


def test_estrutura_de_pastas_no_importar_pasta(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _arvore_de_pastas(upstream)
    pagina.ir("Importar pasta")
    page.get_by_role("button", name="Escolher na estrutura de pastas…").click()
    modal = _seletor(page)
    modal.get_by_role("button", name="Escolher Contratos").click()
    expect(page.locator("#ip-pasta")).to_have_value("Contratos")
    expect(page.locator("#ip-pai")).to_have_value("/Meus documentos")


def test_seletor_de_pastas_e_responsivo_no_celular(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _arvore_de_pastas(upstream)
    page.set_viewport_size({"width": 390, "height": 800})
    pagina.ir("Criar de modelo")
    page.get_by_role("button", name="Escolher na estrutura de pastas…").first.click()
    modal = _seletor(page)
    expect(modal.locator(".pkcard")).to_have_count(2)
    caixa = modal.bounding_box()
    assert caixa is not None
    assert caixa["width"] >= 385  # ocupa a largura inteira, como uma gaveta
    assert caixa["y"] + caixa["height"] >= 790  # encostada embaixo
    cartoes = modal.locator(".pkcard")
    primeiro, segundo = cartoes.nth(0).bounding_box(), cartoes.nth(1).bounding_box()
    assert primeiro and segundo
    assert segundo["y"] > primeiro["y"] + primeiro["height"] - 2  # uma coluna só
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def test_estrutura_de_pastas_quando_a_api_nao_deixa_listar(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    upstream.respostas[OFICIAL_PASTAS] = (401, "")
    upstream.respostas[SINC_PASTAS] = (500, "")
    pagina.ir("Criar de modelo")
    page.get_by_role("button", name="Escolher na estrutura de pastas…").first.click()
    modal = _seletor(page)
    expect(modal).to_contain_text("Digite o caminho")  # a saída: continuar digitando
    expect(modal.get_by_role("button", name="Entrar com usuário…")).to_have_count(0)
    expect(modal.get_by_role("button", name="Tentar de novo")).to_be_visible()


# ------------------------------------------------------ login de usuário ---


def _login_na_api(upstream: Upstream) -> None:
    """A API só aceita a senha certa e, com o token do usuário, lista as pastas."""

    def login(q: dict[str, list[str]]) -> tuple[int, str]:
        if q["senha"][0] != SENHA:
            return 401, '{"retorno": "Usuário ou senha inválidos"}'
        return 200, json.dumps({"token": TOKEN_USUARIO})

    upstream.dinamicas[LOGIN_SYNC] = login
    upstream.respostas[OFICIAL_PASTAS] = (401, "")
    pasta = {
        "id": 77,
        "nome": "Pasta do usuário",
        "caminhoNomesPais": "/Meus documentos/Pasta do usuário",
    }
    upstream.por_token[(*SINC_PASTAS, f"Bearer {TOKEN_USUARIO}")] = (
        200,
        json.dumps({"diretorios": [pasta], "temMais": False}),
    )
    upstream.respostas[SINC_PASTAS] = (
        401,
        '{"retorno": "Esta instância exige login de usuário no sincronizador."}',
    )


def test_seletor_pede_login_e_depois_lista_as_pastas(
    abrir: Any, app_cfg: App, upstream: Upstream
) -> None:
    pagina = abrir(app_cfg.base)
    page = pagina.page
    _login_na_api(upstream)
    pagina.ir("Criar de modelo")
    page.get_by_role("button", name="Escolher na estrutura de pastas…").first.click()
    seletor = _seletor(page)
    expect(seletor).to_contain_text("Entre com o seu usuário")
    seletor.get_by_role("button", name="Entrar com usuário…").click()
    login = page.locator("[role=dialog][aria-label*='Entrar']")
    expect(login).to_contain_text("Entrar na instância do perfil a")
    expect(login).to_contain_text("não é guardada")  # diz o que acontece com a senha
    expect(login.get_by_role("button", name="Entrar", exact=True)).to_be_disabled()  # campos vazios
    login.get_by_label("Usuário").fill("ana@cliente.com.br")
    login.get_by_label("Senha").fill("senha errada")
    login.get_by_role("button", name="Entrar", exact=True).click()
    expect(login).to_contain_text("Usuário ou senha inválidos")
    expect(login.get_by_label("Senha")).to_have_value("")  # a senha sai da tela ao enviar
    login.get_by_label("Senha").fill(SENHA)
    login.get_by_role("button", name="Entrar", exact=True).click()
    expect(login).to_have_count(0)
    expect(seletor.locator(".pkcard")).to_have_count(1)  # voltou ao seletor e já lista
    expect(seletor.get_by_role("button", name="Abrir Pasta do usuário")).to_be_visible()
    expect(seletor).to_contain_text("com o seu usuário")
    # nada secreto ficou na página
    html = page.content()
    assert SENHA not in html and TOKEN_USUARIO not in html


def test_perfis_mostra_o_usuario_e_permite_sair(
    abrir: Any, app_cfg: App, upstream: Upstream
) -> None:
    pagina = abrir(app_cfg.base)
    page = pagina.page
    _login_na_api(upstream)
    pagina.ir("Perfis")
    cartao = page.locator(".pfr").first
    expect(cartao).to_contain_text("Sem login de usuário")
    cartao.get_by_role("button", name="Entrar com usuário…").click()
    login = page.locator("[role=dialog][aria-label*='Entrar']")
    login.get_by_label("Usuário").fill("ana@cliente.com.br")
    login.get_by_label("Senha").fill(SENHA)
    page.keyboard.press("Enter")  # Enter envia
    expect(login).to_have_count(0)
    expect(cartao).to_contain_text("ana@cliente.com.br")
    expect(cartao.get_by_role("button", name="Trocar usuário…")).to_be_visible()
    assert SENHA not in page.content() and TOKEN_USUARIO not in page.content()
    cartao.get_by_role("button", name="Sair").click()
    expect(cartao).to_contain_text("Sem login de usuário")
    assert app_cfg.servidor.arquivo_config is not None
    gravado = app_cfg.servidor.arquivo_config.read_text(encoding="utf-8")
    assert SENHA not in gravado and TOKEN_USUARIO not in gravado


def test_login_nao_funciona_em_perfil_somente_leitura(
    abrir: Any, app_cfg: App, upstream: Upstream
) -> None:
    pagina = abrir(app_cfg.base)
    page = pagina.page
    _login_na_api(upstream)
    app_cfg.http.post(
        "/_config/perfil",
        json={"original": "b", "nome": "b", "instancia": "b", "baseUrl": upstream.base, "token": "",
              "protecao": "bloquear"},
    )  # fmt: skip
    page.reload()
    page.wait_for_selector("#perfil option", state="attached")
    pagina.ir("Perfis")
    page.locator(".pfr").nth(1).get_by_role("button", name="Entrar com usuário…").click()
    login = page.locator("[role=dialog][aria-label*='Entrar']")
    expect(login).to_contain_text("somente leitura")
    login.get_by_label("Usuário").fill("x")
    login.get_by_label("Senha").fill("y")
    expect(login.get_by_role("button", name="Entrar", exact=True)).to_be_disabled()


# ------------------------------------------------ painel e monitor ---


def test_painel_mostra_todas_as_instancias_de_uma_vez(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    modelos = [{"id": 1, "geravelPorApi": True}, {"id": 2}]
    upstream.dinamicas[("GET", "/api/modelos")] = lambda q: (
        (401, "{}") if q["instancia"][0] == "ruim" else (200, json.dumps({"modelos": modelos}))
    )
    upstream.respostas[("GET", "/api/documentos")] = (
        200,
        json.dumps({"total": 9, "documentos": []}),
    )
    pagina.ir("Painel das instâncias")
    cartoes = page.locator(".pfr")
    expect(cartoes).to_have_count(5)
    expect(page.locator("main")).to_contain_text("3 de 5 instância(s) online")
    expect(cartoes.filter(has_text="ruim")).to_contain_text("Token recusado")
    expect(cartoes.filter(has_text="fora")).to_contain_text("Fora do ar")
    ok = cartoes.filter(has_text="cliente1")
    expect(ok).to_contain_text("Online")
    expect(ok).to_contain_text("2 (1 geráveis por API)")
    expect(ok).to_contain_text("9")  # pendentes


def test_painel_troca_de_perfil_e_abre_o_diagnostico(pagina: Pagina) -> None:
    page = pagina.page
    pagina.ir("Painel das instâncias")
    cartao = page.locator(".pfr").filter(has_text="ruim")
    cartao.get_by_role("button", name="Diagnóstico").click()
    expect(page.locator("#perfil")).to_have_value("ruim")
    expect(page.locator("main")).to_contain_text("Instância do perfil ruim")


def test_monitor_avisa_o_que_ja_esta_com_problema_e_para(pagina: Pagina) -> None:
    page = pagina.page
    pagina.ir("Painel das instâncias")
    page.locator("label.sw", has_text="prod").click()  # tira prod e leitura da vigilância
    page.locator("label.sw", has_text="leitura").click()
    pagina.botao("Iniciar monitor").click()
    expect(page.locator("main")).to_contain_text("Monitorando")
    eventos = page.locator("main .dglist .mi")
    expect(eventos).to_have_count(2)
    expect(eventos.filter(has_text="Queda")).to_contain_text("fora")
    expect(eventos.filter(has_text="Token recusado")).to_contain_text("ruim")
    expect(page.locator(".hb").first).to_be_visible()  # o histórico das verificações
    pagina.botao("Parar").click()
    expect(page.locator("main")).to_contain_text("Desligado")


def test_monitor_recusa_iniciar_sem_nenhum_perfil(pagina: Pagina) -> None:
    page = pagina.page
    pagina.ir("Painel das instâncias")
    for nome in ("cliente1", "ruim", "fora", "prod", "leitura"):
        page.locator("label.sw", has_text=nome).click()
    pagina.botao("Iniciar monitor").click()
    expect(page.locator("main .dangerbox")).to_contain_text("ao menos um perfil")
