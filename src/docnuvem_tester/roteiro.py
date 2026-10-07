"""Teste de fumaça de um cliente novo: importar -> assinar -> ver status -> link -> cancelar.

ATENÇÃO: ao contrário do resto da ferramenta, este roteiro ESCREVE na instância: importa um
PDF de teste e cria (e cancela) uma solicitação de assinatura, sem enviar e-mail. A API não tem
exclusão de documento, então o PDF de teste fica na pasta de teste.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.download import _mensagem

PASTA_TESTE = "Teste de fumaça Docnuvem API Tester"
# CPF fictício com dígitos verificadores válidos (o mesmo usado nos exemplos da página).
CPF_TESTE = "52998224725"


def pdf_minimo() -> bytes:
    """Um PDF de uma página em branco, válido (com tabela xref)."""
    objetos = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>",
    ]
    corpo = b"%PDF-1.4\n"
    posicoes = []
    for i, obj in enumerate(objetos, start=1):
        posicoes.append(len(corpo))
        corpo += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(corpo)
    corpo += f"xref\n0 {len(objetos) + 1}\n".encode() + b"0000000000 65535 f \n"
    for pos in posicoes:
        corpo += f"{pos:010d} 00000 n \n".encode()
    corpo += (
        f"trailer\n<</Size {len(objetos) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return corpo


def _passo(passos: list[dict[str, Any]], id_: str, titulo: str, funcao: Callable[[], str]) -> bool:
    """Roda um passo, mede o tempo e registra ok/erro. Devolve se deu certo."""
    inicio = time.perf_counter()
    try:
        detalhe = funcao()
        nivel = "ok"
    except AssertionError as exc:
        detalhe, nivel = str(exc), "erro"
    except httpx.HTTPError as exc:
        detalhe, nivel = f"falha de conexão: {exc}", "erro"
    passos.append(
        {
            "id": id_,
            "titulo": titulo,
            "nivel": nivel,
            "detalhe": detalhe,
            "ms": round((time.perf_counter() - inicio) * 1000),
        }
    )
    return nivel == "ok"


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        dado = resp.json()
    except ValueError:
        return {}
    return dado if isinstance(dado, dict) else {}


def rodar_fumaca(cliente: httpx.Client, perfil: PerfilConfig) -> dict[str, Any]:
    from docnuvem_tester.web import diagnosticar  # evita import circular

    base = perfil.baseUrl.rstrip("/")
    auth = {"Authorization": f"Bearer {perfil.token}"}
    inst = {"instancia": perfil.instancia.lower()}
    passos: list[dict[str, Any]] = []
    estado: dict[str, Any] = {}
    carimbo = time.strftime("%Y%m%d-%H%M%S")

    def diagnostico() -> str:
        d = diagnosticar(cliente, perfil)
        itens = {i["id"]: i for i in d["itens"]}
        assert itens["api"]["nivel"] == "ok", "A API não respondeu: " + itens["api"]["detalhe"]
        assert itens["token"]["nivel"] == "ok", "Token recusado: " + itens["token"]["detalhe"]
        return "API acessível e token aceito."

    def importar() -> str:
        r = cliente.post(
            base + "/importar",
            params={
                **inst,
                "nomeArquivo": f"fumaca-{carimbo}.pdf",
                "nomePasta": PASTA_TESTE,
            },
            files={"file": (f"fumaca-{carimbo}.pdf", pdf_minimo(), "application/pdf")},
            headers=auth,
            timeout=60,
        )
        assert r.status_code in (200, 201), f"HTTP {r.status_code}. {_mensagem(r)}".strip()
        doc = _json(r).get("documentoId")
        assert doc, "A resposta não trouxe documentoId."
        estado["documento"] = doc
        return f"Documento {doc} importado para a pasta “{PASTA_TESTE}”."

    def assinar() -> str:
        corpo = {
            "documentoId": estado["documento"],
            "signatarios": [
                {
                    "nome": "Teste de Fumaça",
                    "email": "fumaca.teste@exemplo.com.br",
                    "cpf": CPF_TESTE,
                    "ordem": 0,
                }
            ],
            "consideraOrdem": False,
            "prazoDias": 1,
            "enviarConvite": False,
        }
        r = cliente.post(
            base + "/api/assinatura", params=inst, json=corpo, headers=auth, timeout=60
        )
        assert r.status_code in (200, 201), f"HTTP {r.status_code}. {_mensagem(r)}".strip()
        dado = _json(r)
        assert dado.get("assinaturaId"), "A resposta não trouxe assinaturaId."
        estado["assinatura"] = dado["assinaturaId"]
        assert dado.get("conviteEnviado") is not True, "Atenção: a API enviou convite por e-mail."
        return f"Solicitação {dado['assinaturaId']} criada, sem convite por e-mail."

    def status() -> str:
        r = cliente.get(
            f"{base}/api/documento/{estado['documento']}/status",
            params=inst,
            headers=auth,
            timeout=30,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}. {_mensagem(r)}".strip()
        dado = _json(r)
        assert len(dado.get("signatarios") or []) == 1, "Esperava 1 signatário no status."
        return f"Status: {dado.get('statusDescricao') or dado.get('status')}, 1 signatário."

    def link() -> str:
        r = cliente.get(
            f"{base}/api/documento/{estado['documento']}/download",
            params=inst,
            headers=auth,
            timeout=30,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}. {_mensagem(r)}".strip()
        url = _json(r).get("urlDownload")
        assert isinstance(url, str) and url.startswith(("http://", "https://")), (
            "A API não devolveu um link de download válido."
        )
        return "Link temporário de download gerado."

    def cancelar() -> str:
        r = cliente.delete(
            f"{base}/api/assinatura/{estado['assinatura']}",
            params={**inst, "motivo": "Teste de fumaça (Docnuvem API Tester)"},
            headers=auth,
            timeout=30,
        )
        assert r.status_code in (200, 204), f"HTTP {r.status_code}. {_mensagem(r)}".strip()
        estado["cancelada"] = True
        return f"Solicitação {estado['assinatura']} cancelada."

    sequencia: list[tuple[str, str, Callable[[], str], str | None]] = [
        ("diagnostico", "API e token", diagnostico, None),
        ("importar", "Importar PDF de teste", importar, None),
        ("assinatura", "Solicitar assinatura (sem e-mail)", assinar, "documento"),
        ("status", "Consultar status do documento", status, "assinatura"),
        ("download", "Gerar link de download", link, "assinatura"),
    ]
    seguir = True
    for id_, titulo, funcao, precisa in sequencia:
        if not seguir or (precisa and precisa not in estado):
            passos.append(
                {
                    "id": id_,
                    "titulo": titulo,
                    "nivel": "pulado",
                    "detalhe": "Pulado: um passo anterior falhou.",
                    "ms": 0,
                }
            )
            continue
        seguir = _passo(passos, id_, titulo, funcao)
    # Limpeza: se a assinatura foi criada, ela é cancelada mesmo que um passo tenha falhado.
    if "assinatura" in estado:
        _passo(passos, "cancelar", "Cancelar a solicitação de teste", cancelar)
    else:
        passos.append(
            {"id": "cancelar", "titulo": "Cancelar a solicitação de teste", "nivel": "pulado",
             "detalhe": "Nada a cancelar: a solicitação não foi criada.", "ms": 0}
        )  # fmt: skip
    ok = all(p["nivel"] == "ok" for p in passos)
    return {
        "verificadoEm": int(time.time() * 1000),
        "instancia": perfil.instancia.lower(),
        "ok": ok,
        "passos": passos,
        "documentoId": estado.get("documento"),
        "assinaturaId": estado.get("assinatura"),
        "pasta": PASTA_TESTE,
    }
