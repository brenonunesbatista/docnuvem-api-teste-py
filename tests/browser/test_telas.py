"""A página num navegador de verdade: o que o usuário clica e vê.

Rode com `pytest -m browser`. Cada teste também falha se a página soltar um erro de JavaScript.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

# O job comum do CI não instala o Playwright: sem ele, este arquivo é só ignorado.
pytest.importorskip("playwright")

from playwright.sync_api import expect  # noqa: E402

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


# ----------------------------------------------------- excluir em massa ---

EXCLUIR_SYNC = ("POST", "/api/sync/excluirDocumento")


def _tres_documentos(upstream: Upstream) -> None:
    docs = [
        {"documentoId": 1, "nomeArquivo": "ativo.pdf", "status": "pendente", "assinaturaId": 101},
        {
            "documentoId": 2,
            "nomeArquivo": "assinado.pdf",
            "status": "assinado",
            "assinaturaId": 102,
        },
        {"documentoId": 3, "nomeArquivo": "solto.pdf", "status": "sem_assinatura"},
    ]
    corpo = {"total": 3, "temMais": False, "documentos": docs}
    upstream.respostas[("GET", "/api/documentos")] = (200, json.dumps(corpo))


def _alteracoes(upstream: Upstream) -> list[str]:
    return [
        f"{c['method']} {c['rota']}"
        for c in upstream.recebidas
        if c["method"] in ("POST", "DELETE")
    ]


def test_excluir_em_massa_so_depois_de_ver_e_confirmar(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _tres_documentos(upstream)
    pagina.ir("Excluir em massa")
    expect(page.locator("main")).to_contain_text("Ação destrutiva")
    expect(page.locator("main")).to_contain_text("lixeira")
    page.fill("#ex-dir", "5")
    pagina.botao("Ver o que será excluído").click()
    expect(page.locator("main")).to_contain_text("3 documento(s) nesta seleção")
    expect(page.locator("main")).to_contain_text("1 têm fluxo de assinatura ATIVO")
    expect(page.locator("main")).to_contain_text("não tem volta")
    expect(page.locator("main")).to_contain_text("3 documento(s) irão para a lixeira")
    botao = pagina.botao("Excluir 3 documento(s)")
    expect(botao).to_be_disabled()  # sem digitar a confirmação não libera
    page.fill("#ex-conf", "EXCLUIR 2")
    expect(botao).to_be_disabled()  # número errado também não
    page.fill("#ex-conf", "excluir  3")
    expect(botao).to_be_enabled()
    assert _alteracoes(upstream) == []  # até aqui nada foi alterado
    page.fill("#ex-int", "0")
    botao.click()
    expect(page.locator("main")).to_contain_text("3 excluído(s)")
    expect(page.locator("main")).to_contain_text("1 fluxo(s) cancelado(s)")
    expect(page.locator("main")).to_contain_text("lixeira da plataforma")
    assert _alteracoes(upstream) == [
        "DELETE /api/assinatura/101",  # o fluxo ativo primeiro
        "POST /api/sync/excluirDocumento",
        "POST /api/sync/excluirDocumento",
        "POST /api/sync/excluirDocumento",
    ]


def test_excluir_nao_deixa_esvaziar_a_raiz(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _tres_documentos(upstream)
    pagina.ir("Excluir em massa")
    page.fill("#ex-dir", "0")
    pagina.botao("Ver o que será excluído").click()
    expect(page.locator("main .dangerbox").last).to_contain_text("raiz")
    page.get_by_role("button", name="Escolher na estrutura de pastas…").click()
    modal = page.locator("[role=dialog][aria-label*='estrutura']")
    expect(modal.get_by_role("button", name="Escolher esta pasta")).to_be_disabled()
    expect(modal).to_contain_text("não serve")
    assert _alteracoes(upstream) == []


def test_excluir_mudar_um_filtro_invalida_a_verificacao(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _tres_documentos(upstream)
    pagina.ir("Excluir em massa")
    page.fill("#ex-dir", "5")
    pagina.botao("Ver o que será excluído").click()
    page.fill("#ex-conf", "EXCLUIR 3")
    expect(pagina.botao("Excluir 3 documento(s)")).to_be_enabled()
    page.select_option("#ex-st", "assinado")  # mudou a seleção: a verificação antiga não vale
    expect(pagina.botao("Excluir 3 documento(s)")).to_have_count(0)
    expect(page.locator("main")).not_to_contain_text("2. Conferir e confirmar")


def test_excluir_sem_cancelar_fluxos_avisa_que_vai_pular(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _tres_documentos(upstream)
    pagina.ir("Excluir em massa")
    page.fill("#ex-dir", "5")
    page.locator("label.sw", has_text="Cancelar antes os fluxos").click()  # desliga
    pagina.botao("Ver o que será excluído").click()
    expect(page.locator("main")).to_contain_text("serão PULADOS")
    expect(page.locator("main")).to_contain_text("2 documento(s) irão para a lixeira")
    page.fill("#ex-conf", "EXCLUIR 3")
    page.fill("#ex-int", "0")
    pagina.botao("Excluir 3 documento(s)").click()
    expect(page.locator("main")).to_contain_text("1 pulado(s)")
    assert not any(a.startswith("DELETE") for a in _alteracoes(upstream))  # nada foi cancelado


def test_excluir_em_perfil_somente_leitura_fica_bloqueado(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _tres_documentos(upstream)
    page.select_option("#perfil", "leitura")
    pagina.ir("Excluir em massa")
    page.fill("#ex-dir", "5")
    pagina.botao("Ver o que será excluído").click()  # ver é leitura: vale
    expect(page.locator("main")).to_contain_text("somente leitura")
    page.fill("#ex-conf", "EXCLUIR 3")
    expect(pagina.botao("Excluir 3 documento(s)")).to_be_disabled()
    assert _alteracoes(upstream) == []


def test_excluir_pasta_sem_documentos_nao_pede_confirmacao(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    upstream.respostas[("GET", "/api/documentos")] = (
        200,
        json.dumps({"total": 0, "temMais": False, "documentos": []}),
    )
    pagina.ir("Excluir em massa")
    page.fill("#ex-dir", "5")
    pagina.botao("Ver o que será excluído").click()
    expect(page.locator("main")).to_contain_text("Nenhum documento nesta seleção")
    expect(page.locator("#ex-conf")).to_have_count(0)
    expect(page.locator("main button.dng")).to_have_count(0)


def test_excluir_pasta_grande_le_tudo_antes_e_nao_tem_limite(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page

    def paginado(q: dict[str, list[str]]) -> tuple[int, str]:
        pg, tam = int(q["pagina"][0]), int(q["tamanho"][0])
        ini = pg * tam
        docs = [
            {"documentoId": i, "nomeArquivo": f"d{i}.pdf", "status": "sem_assinatura"}
            for i in range(ini + 1, min(ini + tam, 5400) + 1)
        ]
        return 200, json.dumps({"total": 5400, "temMais": ini + tam < 5400, "documentos": docs})

    upstream.dinamicas[("GET", "/api/documentos")] = paginado
    pagina.ir("Excluir em massa")
    page.fill("#ex-dir", "5")
    pagina.botao("Ver o que será excluído").click()
    expect(page.locator("main")).to_contain_text("5400 documento(s) nesta seleção")  # sem teto
    page.fill("#ex-conf", "EXCLUIR 5400")
    page.fill("#ex-int", "2000")  # devagar para dar tempo de parar
    pagina.botao("Excluir 5400 documento(s)").click()
    expect(page.locator("main")).to_contain_text("excluído(s)")
    pagina.botao("Parar").click()
    expect(page.locator("main")).to_contain_text("Cancelado")


def _pasta_lenta(upstream: Upstream, total: int, atraso: float) -> None:
    """Uma pasta com `total` documentos em que cada página de 200 leva `atraso` segundos."""
    import time

    def paginado(q: dict[str, list[str]]) -> tuple[int, str]:
        pg, tam = int(q["pagina"][0]), int(q["tamanho"][0])
        if tam > 1:
            time.sleep(atraso)
        ini = pg * tam
        docs = [
            {"documentoId": i, "nomeArquivo": f"d{i}.pdf", "status": "sem_assinatura"}
            for i in range(ini + 1, min(ini + tam, total) + 1)
        ]
        return 200, json.dumps({"total": total, "temMais": ini + tam < total, "documentos": docs})

    upstream.dinamicas[("GET", "/api/documentos")] = paginado


def test_excluir_mostra_o_progresso_da_leitura_e_deixa_cancelar(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _pasta_lenta(upstream, 6000, 1.5)  # ~8 s de leitura: tempo de sobra para ver e cancelar
    pagina.ir("Excluir em massa")
    page.fill("#ex-dir", "5")
    pagina.botao("Ver o que será excluído").click()
    expect(page.locator("main")).to_contain_text("Lendo a pasta:")
    expect(page.locator("main")).to_contain_text("de 6000 documento(s)")
    expect(page.locator("main [role=progressbar]").first).to_be_visible()
    pagina.botao("Cancelar leitura").click()
    expect(page.locator("main .dangerbox").last).to_contain_text("Leitura cancelada")
    expect(page.locator("main")).not_to_contain_text("2. Conferir e confirmar")
    assert _alteracoes(upstream) == []


def test_excluir_reaproveita_a_leitura_da_verificacao(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _pasta_lenta(upstream, 1000, 0.05)

    def paginas_cheias() -> int:
        return len(
            [
                c
                for c in upstream.recebidas
                if c["rota"] == "/api/documentos" and "tamanho=200" in c["path"]
            ]
        )

    pagina.ir("Excluir em massa")
    page.fill("#ex-dir", "5")
    pagina.botao("Ver o que será excluído").click()
    expect(page.locator("main")).to_contain_text("1000 documento(s) nesta seleção")
    lidas = paginas_cheias()
    assert lidas == 5
    page.fill("#ex-conf", "EXCLUIR 1000")
    page.fill("#ex-int", "2000")  # devagar: dá tempo de parar
    pagina.botao("Excluir 1000 documento(s)").click()
    expect(page.locator("main")).to_contain_text("excluído(s)")
    assert paginas_cheias() == lidas  # a exclusão não leu a pasta de novo
    pagina.botao("Parar").click()
    expect(page.locator("main")).to_contain_text("Cancelado")


def test_excluir_marcar_varias_pastas_de_uma_pasta_pai(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _arvore_de_pastas(upstream)
    _tres_documentos(upstream)
    pagina.ir("Excluir em massa")
    page.get_by_role("button", name="Marcar várias pastas de uma pasta pai…").click()
    modal = _seletor(page)
    expect(modal.get_by_role("button", name="Usar 0 pasta(s) marcada(s)")).to_be_disabled()
    modal.get_by_label("Marcar Contratos").check()
    modal.get_by_label("Marcar Atas").check()
    expect(modal).to_contain_text("2 marcada(s)")
    modal.get_by_role("button", name="Limpar", exact=True).click()
    expect(modal).to_contain_text("0 marcada(s)")
    modal.get_by_role("button", name="Marcar todas").click()
    modal.get_by_role("button", name="Usar 2 pasta(s) marcada(s)").click()
    expect(modal).to_have_count(0)
    expect(page.locator("main")).to_contain_text("2 pasta(s) marcada(s) em /Meus documentos")
    expect(page.locator("main")).to_contain_text("Atas")
    expect(page.locator("main")).to_contain_text("Contratos")
    pagina.botao("Ver o que será excluído").click()
    expect(page.locator("main")).to_contain_text("2. Conferir e confirmar")
    lidas = " ".join(c["path"] for c in upstream.recebidas if c["rota"] == "/api/documentos")
    assert "diretorioId=10" in lidas and "diretorioId=11" in lidas
    leituras = ("listarDiretoriosFilhos", "/api/sync/tree")
    assert [a for a in _alteracoes(upstream) if not any(x in a for x in leituras)] == []
    pagina.page.get_by_role("button", name="Limpar seleção").click()
    expect(page.locator("main")).not_to_contain_text("pasta(s) marcada(s) em")


# ---------------------------------------- árvore completa, nova pasta, pasta inteira ---

ARVORE_SYNC = ("POST", "/api/sync/tree")
CRIAR_PASTA = ("POST", "/api/sync/criarDiretorio")
EXCLUIR_PASTA = ("POST", "/api/sync/excluirDiretorio")
DOCUMENTOS = ("GET", "/api/documentos")


def _arvore_completa(upstream: Upstream) -> None:
    """Contratos (10) com 2024 (12) dentro, e Atas (11). Documentos: 1 em Contratos, 2 em 2024."""
    pastas = [
        (10, "Contratos", 0, "/Meus documentos/Contratos"),
        (11, "Atas", 0, "/Meus documentos/Atas"),
        (12, "2024", 10, "/Meus documentos/Contratos/2024"),
    ]
    dado = {
        "diretorios": [
            {"id": i, "nome": n, "diretorioPaiId": pai, "caminhoNomesPais": c, "lixeira": False}
            for i, n, pai, c in pastas
        ],
        "documentos": [
            {"id": k, "diretorioId": d, "lixeira": False} for k, d in ((1, 10), (2, 12), (3, 12))
        ],
    }
    upstream.respostas[OFICIAL_PASTAS] = (401, "")
    upstream.respostas[ARVORE_SYNC] = (200, json.dumps(dado))


def test_seletor_mostra_quantos_documentos_ha_em_cada_pasta(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _arvore_completa(upstream)
    pagina.ir("Excluir em massa")
    page.get_by_role("button", name="Escolher na estrutura de pastas…").click()
    modal = _seletor(page)
    expect(modal.locator(".pkcard")).to_have_count(2)
    expect(modal.locator(".pkcard").filter(has_text="Contratos")).to_contain_text("3 documento(s)")
    expect(modal.locator(".pkcard").filter(has_text="Atas")).to_contain_text("0 documento(s)")
    expect(modal).to_contain_text("lida de uma vez")


def test_criar_uma_pasta_pelo_seletor(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _arvore_completa(upstream)
    upstream.respostas[CRIAR_PASTA] = (200, '{"id": 99}')
    pagina.ir("Excluir em massa")
    page.get_by_role("button", name="Escolher na estrutura de pastas…").click()
    modal = _seletor(page)
    modal.get_by_role("button", name="Abrir Contratos").click()
    modal.get_by_role("button", name="Nova pasta aqui…").click()
    expect(modal.get_by_role("button", name="Criar", exact=True)).to_be_disabled()
    modal.get_by_placeholder("Nome da nova pasta").fill("Aditivos")
    modal.get_by_role("button", name="Criar", exact=True).click()
    expect(modal.get_by_placeholder("Nome da nova pasta")).to_have_count(0)  # fechou e recarregou
    chamada = [c for c in upstream.recebidas if (c["method"], c["rota"]) == CRIAR_PASTA]
    assert len(chamada) == 1
    assert "diretorioPaiId=10" in chamada[0]["path"] and "nome=Aditivos" in chamada[0]["path"]


def test_criar_pasta_mostra_o_erro_da_api(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _arvore_completa(upstream)
    upstream.respostas[CRIAR_PASTA] = (400, '{"retorno": "Já existe uma pasta com esse nome"}')
    pagina.ir("Excluir em massa")
    page.get_by_role("button", name="Escolher na estrutura de pastas…").click()
    modal = _seletor(page)
    modal.get_by_role("button", name="Nova pasta aqui…").click()
    modal.get_by_placeholder("Nome da nova pasta").fill("Atas")
    modal.get_by_role("button", name="Criar", exact=True).click()
    expect(modal.locator(".dangerbox")).to_contain_text("Já existe uma pasta com esse nome")


def _contagens(upstream: Upstream, total: int, pendentes: int) -> None:
    def resposta(q: dict[str, list[str]]) -> tuple[int, str]:
        n = pendentes if q.get("status") == ["pendente"] else total
        return 200, json.dumps({"total": n, "temMais": False, "documentos": []})

    upstream.dinamicas[DOCUMENTOS] = resposta


def test_excluir_a_pasta_inteira_so_depois_de_ver_e_confirmar(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _arvore_completa(upstream)
    _contagens(upstream, total=3, pendentes=0)
    upstream.respostas[EXCLUIR_PASTA] = (200, "{}")
    pagina.ir("Excluir em massa")
    expect(pagina.botao("Ver a pasta inteira")).to_be_disabled()  # sem pasta escolhida
    page.fill("#ex-dir", "10")
    pagina.botao("Ver a pasta inteira").click()
    expect(page.locator("main")).to_contain_text("A pasta 10 tem 3 documento(s) e 1 subpasta(s)")
    botao = pagina.botao("Excluir a pasta inteira")
    expect(botao).to_be_disabled()
    page.fill("#px-conf", "EXCLUIR PASTA 10")
    botao.click()
    expect(page.locator("main")).to_contain_text("Pasta 10 excluída inteira")
    envios = [c for c in upstream.recebidas if (c["method"], c["rota"]) == EXCLUIR_PASTA]
    assert len(envios) == 1 and "diretorioId=10" in envios[0]["path"]


def test_pasta_inteira_com_fluxo_ativo_fica_bloqueada(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _arvore_completa(upstream)
    _contagens(upstream, total=5, pendentes=2)
    pagina.ir("Excluir em massa")
    page.fill("#ex-dir", "10")
    pagina.botao("Ver a pasta inteira").click()
    expect(page.get_by_role("alert")).to_contain_text("2 documento(s) têm fluxo")
    expect(page.locator("#px-conf")).to_have_count(0)  # nem aparece o campo de confirmação
    assert [c for c in upstream.recebidas if (c["method"], c["rota"]) == EXCLUIR_PASTA] == []


def test_pasta_inteira_nao_vale_com_varias_pastas_marcadas(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _arvore_completa(upstream)
    pagina.ir("Excluir em massa")
    page.get_by_role("button", name="Marcar várias pastas de uma pasta pai…").click()
    modal = _seletor(page)
    modal.get_by_role("button", name="Marcar todas").click()
    modal.get_by_role("button", name="Usar 2 pasta(s) marcada(s)").click()
    expect(pagina.botao("Ver a pasta inteira")).to_be_disabled()


def test_pasta_inteira_tem_o_seletor_de_pastas_no_proprio_cartao(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _arvore_completa(upstream)
    _contagens(upstream, total=3, pendentes=0)
    pagina.ir("Excluir em massa")
    pagina.botao("Escolher a pasta na estrutura…").click()
    modal = _seletor(page)
    modal.get_by_role("button", name="Escolher Contratos").click()
    expect(modal).to_have_count(0)
    expect(page.locator("#ex-dir")).to_have_value("10")
    expect(pagina.botao("Ver a pasta inteira")).to_be_enabled()
    pagina.botao("Ver a pasta inteira").click()
    expect(page.locator("main")).to_contain_text("A pasta 10 tem 3 documento(s)")


# ------------------------------------- escolher o arquivo e posicionar a assinatura ---

IMPORTAR_API = ("POST", "/importar")


def _dialogo(page: Any, nome: str) -> Any:
    return page.locator(f"[role=dialog][aria-label*='{nome}']")


def _documentos_da_instancia(upstream: Upstream) -> None:
    docs = [
        {"documentoId": 501, "nomeArquivo": "Contrato.pdf", "diretorioId": 10},
        {"documentoId": 502, "nomeArquivo": "Aditivo.pdf", "diretorioId": 10},
        {"documentoId": 503, "nomeArquivo": "Ata.pdf", "diretorioId": 11},
    ]
    corpo = {"total": 3, "temMais": False, "documentos": docs}
    upstream.respostas[DOCUMENTOS] = (200, json.dumps(corpo))


def test_assinatura_escolher_o_documento_da_plataforma(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _documentos_da_instancia(upstream)
    pagina.ir("Solicitar assinatura")
    pagina.botao("Escolher o arquivo…").click()
    modal = _dialogo(page, "Escolher o arquivo")
    expect(modal.locator(".dprow")).to_have_count(3)
    consultas = [c["path"] for c in upstream.recebidas if c["rota"] == "/api/documentos"]
    assert "status=sem_assinatura" in consultas[-1]  # só os prontos para pedir assinatura
    modal.get_by_placeholder("Filtrar pelo nome do arquivo…").fill("adit")
    expect(modal.locator(".dprow")).to_have_count(1)
    modal.get_by_role("button", name="Usar Aditivo.pdf").click()
    expect(modal).to_have_count(0)
    expect(page.locator("#f-assinatura-documentoId")).to_have_value("502")


def test_assinatura_enviar_um_arquivo_do_computador(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _documentos_da_instancia(upstream)
    upstream.respostas[IMPORTAR_API] = (200, '{"documentoId": 777}')
    pagina.ir("Solicitar assinatura")
    pagina.botao("Escolher o arquivo…").click()
    modal = _dialogo(page, "Escolher o arquivo")
    modal.get_by_role("button", name="Do computador").click()
    modal.get_by_role("button", name="Enviar e usar este arquivo").click()
    expect(modal).to_contain_text("Escolha um arquivo do computador")
    modal.locator("#dp-arq").set_input_files(
        {"name": "contrato.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 teste"}
    )
    expect(modal.locator("#dp-nome")).to_have_value("contrato.pdf")
    modal.locator("#dp-pasta").fill("Contratos")
    modal.get_by_role("button", name="Enviar e usar este arquivo").click()
    expect(modal).to_have_count(0)
    expect(page.locator("#f-assinatura-documentoId")).to_have_value("777")
    envio = [c for c in upstream.recebidas if (c["method"], c["rota"]) == IMPORTAR_API]
    assert len(envio) == 1
    assert (
        "nomePasta=Contratos" in envio[0]["path"] and "nomeArquivo=contrato.pdf" in envio[0]["path"]
    )


def _um_assinante(page: Any) -> None:
    page.locator("input[id$='-nome']").first.fill("Ana Souza")


def test_assinatura_arrastar_o_carimbo_ate_a_posicao(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    pagina.ir("Solicitar assinatura")
    _um_assinante(page)
    pagina.botao("Posicionar as assinaturas na página…").click()
    modal = _dialogo(page, "Posicionar")
    expect(modal.locator(".plcarimbo")).to_have_count(0)
    expect(modal).to_contain_text("rodapé da última página")  # padrão, antes de posicionar
    modal.get_by_role("button", name="Colocar nesta página").click()
    carimbo = modal.locator(".plcarimbo")
    expect(carimbo).to_have_count(1)
    expect(carimbo).to_contain_text("Ana Souza")
    folha = modal.locator("#pl-folha").bounding_box()
    antes = carimbo.bounding_box()
    assert folha and antes
    # arrasta o carimbo para o meio da folha
    alvo_x = folha["x"] + folha["width"] * 0.5
    alvo_y = folha["y"] + folha["height"] * 0.4
    page.mouse.move(antes["x"] + antes["width"] / 2, antes["y"] + antes["height"] / 2)
    page.mouse.down()
    page.mouse.move(alvo_x, alvo_y, steps=8)
    page.mouse.up()
    depois = carimbo.bounding_box()
    assert depois
    fx = (depois["x"] - folha["x"]) / folha["width"]
    fy = (depois["y"] - folha["y"]) / folha["height"]
    assert 0.3 < fx < 0.5 and 0.25 < fy < 0.45  # saiu do rodapé esquerdo
    expect(modal).to_contain_text("Página última")
    modal.get_by_role("button", name="Concluir").click()
    expect(modal).to_have_count(0)
    page.get_by_role("button", name="Opções avançadas").first.click()
    x = float(page.locator("input[id$='-x']").first.input_value())
    y = float(page.locator("input[id$='-y']").first.input_value())
    assert abs(x - fx) < 0.02 and abs(y - fy) < 0.02  # a folha e o formulário concordam
    assert page.locator("input[id$='-pg']").first.input_value() == ""  # vazia = última página


def test_assinatura_carimbo_com_setas_pagina_e_tirar_posicao(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    pagina.ir("Solicitar assinatura")
    _um_assinante(page)
    pagina.botao("Posicionar as assinaturas na página…").click()
    modal = _dialogo(page, "Posicionar")
    modal.get_by_text("Última página", exact=True).click()  # o interruptor
    modal.locator("#pl-pag").fill("2")
    modal.get_by_role("button", name="Colocar nesta página").click()
    carimbo = modal.locator(".plcarimbo")
    expect(modal).to_contain_text("Página 2")
    carimbo.focus()
    page.keyboard.press("ArrowRight")
    expect(modal).to_contain_text("x 0.09")  # 0.08 + 0.01
    modal.locator("#pl-pag").fill("3")  # outra página: o carimbo da 2 não aparece aqui
    expect(modal.locator(".plcarimbo")).to_have_count(0)
    modal.get_by_role("button", name="Mover para esta página").click()
    expect(modal).to_contain_text("Página 3")
    modal.get_by_role("button", name="Tirar a posição de Ana Souza").click()
    expect(modal.locator(".plcarimbo")).to_have_count(0)
    expect(modal).to_contain_text("rodapé da última página")


def test_assinatura_posicao_sem_pagina_e_aceita_no_formulario(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    pagina.ir("Solicitar assinatura")
    _um_assinante(page)
    pagina.botao("Posicionar as assinaturas na página…").click()
    modal = _dialogo(page, "Posicionar")
    modal.get_by_role("button", name="Colocar nesta página").click()
    modal.get_by_role("button", name="Concluir").click()
    page.get_by_role("button", name="Opções avançadas").first.click()
    expect(page.locator("main")).not_to_contain_text("Preencha")  # página vazia vale a última


# ------------------------------------------------------------- marca branca ---


def _menciona_a_marca(page: Any) -> list[str]:
    """Tudo que a tela MOSTRA e cita a marca (textos, títulos, dicas, título da aba)."""
    achados: list[str] = page.evaluate(
        """() => {
        const re = /docnuvem/i, out = [];
        const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        for (let n = w.nextNode(); n; n = w.nextNode()) {
          const e = n.parentElement;
          const ok = e && !['SCRIPT','STYLE','TEXTAREA'].includes(e.tagName);
          if (ok && re.test(n.nodeValue)) out.push(n.nodeValue.trim());
        }
        document.querySelectorAll('[title],[aria-label],[placeholder],[alt]').forEach(el => {
          for (const a of ['title','aria-label','placeholder','alt']) {
            const v = el.getAttribute(a); if (v && re.test(v)) out.push(a + ': ' + v);
          }
        });
        if (re.test(document.title)) out.push('title: ' + document.title);
        return out;
      }"""
    )
    return [a for a in achados if "Modo marca branca" not in a]


def _ligar_marca_branca(pagina: Pagina, nome: str = "") -> None:
    page = pagina.page
    page.get_by_role("button", name="Modo marca branca").click()
    modal = _dialogo(page, "Modo marca branca")
    if nome:
        modal.locator("#mb-nome").fill(nome)
    modal.get_by_text("Ocultar as menções à marca").click()
    expect(modal).to_contain_text("Ligado")
    modal.get_by_role("button", name="Concluir").click()


def test_marca_branca_esconde_toda_mencao_ao_docnuvem(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    assert _menciona_a_marca(page)  # sem o modo, a marca aparece
    _ligar_marca_branca(pagina)
    assert _menciona_a_marca(page) == []
    expect(page.locator(".bname")).to_contain_text("Plataforma")
    for tela in ("Importar pasta", "Baixar pasta", "Excluir em massa", "Solicitar assinatura"):
        pagina.ir(tela)
        assert _menciona_a_marca(page) == [], tela
    pagina.ir("Importar pasta")
    expect(page.locator("main")).to_contain_text("Pasta na plataforma")  # a frase continua certa


def test_marca_branca_com_nome_proprio_e_que_persiste(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _ligar_marca_branca(pagina, "Acme Sign")
    expect(page.locator(".bname")).to_contain_text("Acme Sign")
    pagina.ir("Importar pasta")
    expect(page.locator("main")).to_contain_text("Pasta no Acme Sign")
    page.reload()
    expect(page.locator(".bname")).to_contain_text("Acme Sign")  # guardado no navegador
    assert _menciona_a_marca(page) == []
    assert "Acme Sign" in page.title()


def test_marca_branca_nao_mexe_no_que_e_enviado(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _documentos_da_instancia(upstream)
    _ligar_marca_branca(pagina, "Acme Sign")
    pagina.ir("Solicitar assinatura")
    pagina.botao("Escolher o arquivo…").click()
    modal = _dialogo(page, "Escolher o arquivo")
    expect(modal.locator(".dprow")).to_have_count(3)
    consultas = [c["path"] for c in upstream.recebidas if c["rota"] == "/api/documentos"]
    assert consultas and all("Acme" not in c for c in consultas)  # a API não vê a troca


def test_marca_branca_desligar_traz_a_marca_de_volta(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _ligar_marca_branca(pagina)
    page.get_by_role("button", name="Modo marca branca").click()
    modal = _dialogo(page, "Modo marca branca")
    modal.get_by_text("Ocultar as menções à marca").click()  # desliga: a página recarrega
    expect(page.locator(".bname")).to_contain_text("Docnuvem")
    assert _menciona_a_marca(page)


def test_marca_branca_pelo_endereco(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    page.goto(pagina.base + "/?marca=branca&nome=Minha%20Marca")
    expect(page.locator(".bname")).to_contain_text("Minha Marca")
    assert _menciona_a_marca(page) == []
    page.goto(pagina.base + "/?marca=normal")
    expect(page.locator(".bname")).to_contain_text("Docnuvem")


def test_assinatura_folha_muda_de_formato_e_o_filtro_de_status_funciona(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _documentos_da_instancia(upstream)
    pagina.ir("Solicitar assinatura")
    pagina.botao("Escolher o arquivo…").click()
    arquivo = _dialogo(page, "Escolher o arquivo")
    expect(arquivo.get_by_role("button", name="Sem assinatura")).to_have_attribute(
        "aria-pressed", "true"
    )
    arquivo.get_by_role("button", name="Pendente").click()
    expect(arquivo.get_by_role("button", name="Pendente")).to_have_attribute("aria-pressed", "true")
    consultas = [c["path"] for c in upstream.recebidas if c["rota"] == "/api/documentos"]
    assert "status=pendente" in consultas[-1]
    arquivo.get_by_role("button", name="Fechar").click()
    _um_assinante(page)
    pagina.botao("Posicionar as assinaturas na página…").click()
    modal = _dialogo(page, "Posicionar")
    folha = modal.locator("#pl-folha")
    retrato = folha.bounding_box()
    assert retrato and retrato["height"] > retrato["width"]
    modal.get_by_role("button", name="Paisagem").click()
    expect(modal.get_by_role("button", name="Paisagem")).to_have_attribute("aria-pressed", "true")
    paisagem = folha.bounding_box()
    assert paisagem and paisagem["width"] > paisagem["height"]
    modal.get_by_role("button", name="Carta").click()
    carta = folha.bounding_box()
    assert carta and abs(carta["width"] / carta["height"] - 279.4 / 215.9) < 0.02


def test_vigia_escolher_documentos_e_juntar_aos_ids_ja_digitados(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _documentos_da_instancia(upstream)
    pagina.ir("Vigia de assinaturas")
    page.fill("#vg-ids", "999")
    pagina.botao("Escolher documentos…").click()
    modal = _dialogo(page, "Escolher os documentos do vigia")
    expect(modal.locator(".dprow")).to_have_count(3)
    expect(modal.get_by_role("button", name="Do computador")).to_have_count(0)  # só da plataforma
    expect(modal.get_by_role("button", name="Qualquer")).to_have_attribute("aria-pressed", "true")
    consultas = [c["path"] for c in upstream.recebidas if c["rota"] == "/api/documentos"]
    assert "status=" not in consultas[-1]  # para o vigia, qualquer status
    expect(modal.get_by_role("button", name="Adicionar 0 documento(s) ao vigia")).to_be_disabled()
    modal.get_by_text("Contrato.pdf").click()  # a linha inteira não marca; só o interruptor
    modal.get_by_label("Marcar Contrato.pdf").check(force=True)
    modal.get_by_label("Marcar Ata.pdf").check(force=True)
    expect(modal).to_contain_text("2 marcado(s)")
    modal.get_by_label("Marcar Ata.pdf").uncheck(force=True)
    modal.get_by_label("Marcar Ata.pdf").check(force=True)
    modal.get_by_role("button", name="Adicionar 2 documento(s) ao vigia").click()
    expect(modal).to_have_count(0)
    expect(page.locator("#vg-ids")).to_have_value("999, 501, 503")


def test_vigia_nao_repete_documento_ja_listado(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _documentos_da_instancia(upstream)
    pagina.ir("Vigia de assinaturas")
    page.fill("#vg-ids", "501")
    pagina.botao("Escolher documentos…").click()
    modal = _dialogo(page, "Escolher os documentos do vigia")
    modal.get_by_label("Marcar Contrato.pdf").check(force=True)
    modal.get_by_label("Marcar Aditivo.pdf").check(force=True)
    modal.get_by_role("button", name="Adicionar 2 documento(s) ao vigia").click()
    expect(page.locator("#vg-ids")).to_have_value("501, 502")


def test_status_e_download_tambem_escolhem_o_arquivo(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _documentos_da_instancia(upstream)
    for tela, campo in (("Status do documento", "status"), ("Download", "download")):
        pagina.ir(tela)
        pagina.botao("Escolher o arquivo…").click()
        modal = _dialogo(page, "Escolher o arquivo")
        modal.get_by_role("button", name="Usar Ata.pdf").click()
        expect(page.locator(f"#f-{campo}-documentoId")).to_have_value("503")


# ------------------------------------------------------ cancelar fluxos em massa ---


def _fluxos_por_status(upstream: Upstream) -> None:
    """Dois pendentes, um expirado e um assinado; a API respeita o filtro de status."""
    todos = [
        (1, "a.pdf", "pendente", 101),
        (2, "b.pdf", "pendente", 102),
        (3, "c.pdf", "expirado", 103),
        (4, "d.pdf", "assinado", 104),
    ]

    def resposta(q: dict[str, list[str]]) -> tuple[int, str]:
        status = q.get("status", [""])[0]
        docs = [
            {"documentoId": i, "nomeArquivo": n, "statusAssinatura": st, "diretorioId": 5}
            for i, n, st, _ in todos
            if not status or st == status
        ]
        return 200, json.dumps({"total": len(docs), "temMais": False, "documentos": docs})

    upstream.dinamicas[DOCUMENTOS] = resposta
    for i, _, _, ass in todos:
        corpo = json.dumps({"documentoId": i, "assinaturaId": ass})
        upstream.respostas[("GET", f"/api/documento/{i}/status")] = (200, corpo)


def test_cancelar_fluxos_em_massa_so_depois_de_ver_e_confirmar(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _fluxos_por_status(upstream)
    pagina.ir("Cancelar fluxos em massa")
    expect(page.locator("main")).to_contain_text("Nenhum documento é excluído")
    expect(page.locator("#ex-st")).to_have_count(0)  # o status é sempre "pendente"
    expect(page.get_by_text("Ou excluir a pasta inteira")).to_have_count(0)
    expect(
        page.get_by_text("Incluir também as subpastas").locator("..").locator("input")
    ).to_be_checked()
    page.fill("#ex-dir", "5")
    pagina.botao("Ver o que será cancelado").click()
    expect(page.locator("main")).to_contain_text("2 documento(s) com fluxo ativo nesta seleção")
    expect(page.locator("main")).to_contain_text("2 fluxo(s) serão cancelados · nenhum documento")
    botao = pagina.botao("Cancelar 2 fluxo(s)")
    expect(botao).to_be_disabled()
    page.fill("#ex-conf", "EXCLUIR 2")  # a frase da exclusão não vale aqui
    expect(botao).to_be_disabled()
    page.fill("#ex-conf", "cancelar  2")
    expect(botao).to_be_enabled()
    assert [a for a in _alteracoes(upstream) if "DELETE" in a] == []
    page.fill("#ex-int", "0")
    botao.click()
    expect(page.locator("main")).to_contain_text("2 fluxo(s) cancelado(s)")
    expect(page.locator("main")).to_contain_text("Os documentos continuam na plataforma")
    envios = _alteracoes(upstream)
    assert sorted(a for a in envios if a.startswith("DELETE")) == [
        "DELETE /api/assinatura/101",
        "DELETE /api/assinatura/102",
    ]
    assert not any("excluirDocumento" in a for a in envios)  # nada foi para a lixeira
    consultas = [c["path"] for c in upstream.recebidas if c["rota"] == "/api/documentos"]
    assert all("status=pendente" in c and "incluirSubpastas=true" in c for c in consultas)


def test_cancelar_fluxos_incluindo_os_vencidos(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _fluxos_por_status(upstream)
    pagina.ir("Cancelar fluxos em massa")
    page.fill("#ex-dir", "5")
    page.get_by_text("Incluir também os fluxos vencidos (expirados)").click()
    pagina.botao("Ver o que será cancelado").click()
    expect(page.locator("main")).to_contain_text("3 documento(s) com fluxo ativo nesta seleção")
    expect(page.locator("main")).to_contain_text("expirado: 1")
    expect(page.locator("main")).to_contain_text("pendente: 2")
    expect(page.locator("main")).not_to_contain_text("assinado: 1")  # o assinado nunca entra


def test_os_dois_modos_nao_misturam_o_resultado(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _fluxos_por_status(upstream)
    pagina.ir("Cancelar fluxos em massa")
    page.fill("#ex-dir", "5")
    pagina.botao("Ver o que será cancelado").click()
    expect(page.locator("main")).to_contain_text("2. Conferir e confirmar")
    pagina.ir("Excluir em massa")
    expect(page.locator("main")).not_to_contain_text("2. Conferir e confirmar")  # outro modo
    expect(pagina.botao("Ver o que será excluído")).to_be_visible()
    expect(page.get_by_text("Ou excluir a pasta inteira")).to_be_visible()


def test_cancelar_fluxos_em_perfil_somente_leitura_fica_bloqueado(
    pagina: Pagina, upstream: Upstream
) -> None:
    page = pagina.page
    _fluxos_por_status(upstream)
    page.locator("select").first.select_option("leitura")
    pagina.ir("Cancelar fluxos em massa")
    page.fill("#ex-dir", "5")
    pagina.botao("Ver o que será cancelado").click()
    expect(page.locator("main")).to_contain_text("não dá para cancelar fluxos nele")
    expect(pagina.botao("Cancelar 2 fluxo(s)")).to_be_disabled()


# ------------------------------------------ funções básicas x fluxos completos (menu) ---


def _estrutura_do_menu(page: Any) -> list[tuple[str, str]]:
    itens: list[list[str]] = page.evaluate(
        """() => [...document.querySelectorAll('.navl .sec, .navl .grp, .navl .navb')].map(e => [
          e.classList.contains('sec') ? 'sec' : e.classList.contains('grp') ? 'grp' : 'item',
          e.innerText.trim()
        ])"""
    )
    return [(a, b) for a, b in itens]


def test_menu_separa_funcoes_basicas_de_fluxos_completos_e_mantem_as_subdivisoes(
    pagina: Pagina, upstream: Upstream
) -> None:
    menu = _estrutura_do_menu(pagina.page)
    esperado = [
        ("grp", "Começar"),
        ("item", "Início"),
        ("sec", "Funções básicas da API"),
        ("grp", "Enviar"),
        ("item", "Importar arquivo"),
        ("item", "Envio inteligente"),
        ("grp", "Modelos"),
        ("item", "Modelos"),
        ("item", "Criar de modelo"),
        ("grp", "Assinatura"),
        ("item", "Solicitar assinatura"),
        ("item", "Cancelar assinatura"),
        ("grp", "Consultar"),
        ("item", "Documentos"),
        ("item", "Pastas"),
        ("item", "Status do documento"),
        ("item", "Download"),
        ("grp", "Escola"),
        ("item", "Solicitar documentos"),
        ("item", "Status do aluno"),
        ("sec", "Fluxos completos"),
        ("grp", "Passo a passo"),
        ("item", "Fluxo completo"),
        ("grp", "Pastas em massa"),
        ("item", "Importar pasta"),
        ("item", "Baixar pasta"),
        ("item", "Excluir em massa"),
        ("grp", "Assinaturas"),
        ("item", "Cancelar fluxos em massa"),
        ("item", "Vigia de assinaturas"),
        ("grp", "Rotinas"),
        ("item", "Comparar instâncias"),
        ("item", "Lote para escolas"),
        ("item", "Teste de fumaça"),
        ("item", "Painel das instâncias"),
        ("grp", "Suporte"),
        ("item", "Diagnóstico"),
        ("item", "Perfis"),
    ]
    normal = [(a, b.upper() if a != "item" else b) for a, b in menu]  # títulos saem em caixa alta
    assert [(a, b.casefold()) for a, b in normal] == [(a, b.casefold()) for a, b in esperado]


def test_inicio_mostra_os_atalhos_em_duas_secoes(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    pagina.ir("Início")
    titulos = page.locator("main .sechead")
    expect(titulos).to_have_text(["Funções básicas da API", "Fluxos completos"])
    basicas, fluxos = page.locator("main .cards").nth(0), page.locator("main .cards").nth(1)
    expect(basicas).to_contain_text("Solicitar assinatura")
    expect(basicas).not_to_contain_text("Cancelar fluxos em massa")
    expect(fluxos).to_contain_text("Cancelar fluxos em massa")
    expect(fluxos).to_contain_text("Excluir em massa")
    expect(fluxos).not_to_contain_text("Importar arquivo")
    fluxos.get_by_role("button", name="Excluir em massa").click()
    expect(page.locator("main")).to_contain_text("Ação destrutiva")


# ----------------------------------------- status do aluno: aprovação dos documentos ---

CONSULTAR_ALUNO = ("GET", "/api/solicitacaoAluno/consultarStatus")


def _aluno_com_aprovacao(upstream: Upstream) -> None:
    doc = {"id": 1, "nome": "rg.pdf", "extensao": "pdf", "tamanho": 2048}
    corpo = {
        "codigoMatricula": "123",
        "nome": "Bia",
        "solicitacoes": [
            {
                "solicitacaoId": 9,
                "status": "PENDENTE",
                "quantidadeItens": 3,
                "quantidadeItensEnviados": 2,
                "quantidadeItensPendentes": 1,
                "itens": [
                    {
                        "id": 1,
                        "descricao": "RG",
                        "obrigatorio": True,
                        "status": "ENVIADO",
                        "aprovado": True,
                        "statusAprovacao": "APROVADO",
                        "dataAprovacao": "2026-10-20T14:30:00",
                        "documentos": [doc],
                    },
                    {
                        "id": 2,
                        "descricao": "CPF",
                        "status": "ENVIADO",
                        "aprovado": False,
                        "statusAprovacao": "AGUARDANDO_CONFERENCIA",
                        "documentos": [doc],
                    },
                    {"id": 3, "descricao": "Comprovante", "status": "PENDENTE"},
                ],
            }
        ],
    }
    upstream.respostas[CONSULTAR_ALUNO] = (200, json.dumps(corpo))


def test_status_do_aluno_mostra_a_aprovacao_dos_itens(pagina: Pagina, upstream: Upstream) -> None:
    page = pagina.page
    _aluno_com_aprovacao(upstream)
    pagina.ir("Status do aluno")
    page.fill("#f-alunoStatus-codigoMatricula", "123")
    pagina.botao("Consultar aluno").click()
    expect(page.locator("main")).to_contain_text("1 aprovado(s)")  # resumo da solicitação
    pagina.botao("Ver itens").click()
    linhas = page.locator("main table tbody tr")
    expect(linhas).to_have_count(3)
    expect(page.locator("main table thead")).to_contain_text("Aprovação")
    expect(linhas.nth(0)).to_contain_text("Aprovado em")  # aprovado: o status segue "Enviado"
    expect(linhas.nth(0)).to_contain_text(re.compile("enviado", re.IGNORECASE))
    expect(linhas.nth(1)).to_contain_text("Aguardando conferencia")
    expect(linhas.nth(2)).to_contain_text("Sem conferência")
