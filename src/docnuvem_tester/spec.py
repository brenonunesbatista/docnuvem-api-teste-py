"""Verificador de contrato: compara o OpenAPI da API com o que a ferramenta cobre.

Uso:
    docnuvem-spec                 # usa a baseUrl do primeiro perfil do config.json
    docnuvem-spec --url http://host:8083
    docnuvem-spec --arquivo openapi.json   # sem rede

Sai com 0 se está tudo conhecido, 1 se a API mudou e 2 se não deu para ler a especificação.
Ao mudar de propósito, atualize COBERTOS/CORPOS abaixo depois de tratar a novidade na página.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from docnuvem_tester.config import ConfigError, load_config

# (método, caminho) -> parâmetros de query e de caminho conhecidos (sem o cabeçalho Authorization).
COBERTOS: dict[tuple[str, str], frozenset[str]] = {
    ("POST", "/importar"): frozenset(
        {"instancia", "nomeArquivo", "nomePasta", "nomePastaPai", "tipo"}
    ),
    ("POST", "/enviarParaEnvioInteligente"): frozenset({"instancia", "nomeArquivo"}),
    ("GET", "/api/modelos"): frozenset({"instancia"}),
    ("POST", "/api/documento/from-template"): frozenset({"instancia"}),
    ("POST", "/api/assinatura"): frozenset({"instancia"}),
    ("DELETE", "/api/assinatura/{assinaturaId}"): frozenset(
        {"assinaturaId", "instancia", "motivo"}
    ),
    ("GET", "/api/documentos"): frozenset(
        {
            "dataFim",
            "dataInicio",
            "diretorioId",
            "incluirSubpastas",
            "instancia",
            "pagina",
            "status",
            "tamanho",
        }
    ),
    ("GET", "/api/diretorios"): frozenset(
        {"caminho", "diretorioPaiId", "instancia", "nome", "pagina", "tamanho"}
    ),
    ("GET", "/api/documento/{documentoId}/status"): frozenset({"documentoId", "instancia"}),
    ("GET", "/api/documento/{documentoId}/download"): frozenset({"documentoId", "instancia"}),
    ("POST", "/api/solicitacaoAluno/solicitarEnvioDocumentos"): frozenset({"instancia"}),
    ("GET", "/api/solicitacaoAluno/consultarStatus"): frozenset({"codigoMatricula", "instancia"}),
}

# Esquemas de corpo conhecidos -> propriedades conhecidas.
CORPOS: dict[str, frozenset[str]] = {
    "SolicitacaoAssinaturaRequest": frozenset(
        {
            "consideraOrdem",
            "dataValidade",
            "documentoId",
            "enviarConvite",
            "lembretes",
            "loginSolicitante",
            "metodos",
            "prazoDias",
            "referenciaExterna",
            "signatarios",
            "textoEmail",
        }
    ),
    "SignatarioAssinaturaRequest": frozenset(
        {
            "cnpj",
            "cpf",
            "email",
            "nome",
            "ordem",
            "otp",
            "posicao",
            "semCarimbo",
            "solicitarSelfie",
            "telefone",
        }
    ),
    "PosicaoAssinaturaRequest": frozenset({"altura", "largura", "pagina", "x", "y"}),
    "DocumentoFromTemplateRequest": frozenset(
        {
            "loginSolicitante",
            "modelo",
            "nomeArquivo",
            "nomePasta",
            "nomePastaPai",
            "referenciaExterna",
            "variaveis",
        }
    ),
    "SolicitacaoEnvioDocumentoAlunoRequest": frozenset(
        {
            "codigoMatricula",
            "cpf",
            "email",
            "emailResponsavel",
            "nome",
            "telefone",
            "tipoSolicitacao",
        }
    ),
}

# Fora do escopo da ferramenta (outro produto): o cliente de sincronização.
IGNORADOS_PREFIXOS = ("/api/sync/",)

METODOS_HTTP = {"get", "post", "put", "delete", "patch"}


def _params(operacao: dict[str, Any]) -> set[str]:
    return {p["name"] for p in operacao.get("parameters", []) if p.get("in") in ("query", "path")}


def comparar(spec: dict[str, Any]) -> dict[str, Any]:
    """Compara o OpenAPI com o que a ferramenta conhece. `ok` é True se nada mudou."""
    paths: dict[str, Any] = spec.get("paths", {})
    existentes: dict[tuple[str, str], dict[str, Any]] = {
        (metodo.upper(), caminho): op
        for caminho, ops in paths.items()
        for metodo, op in ops.items()
        if metodo in METODOS_HTTP
    }
    novos = sorted(
        f"{m} {p}"
        for (m, p) in existentes
        if (m, p) not in COBERTOS and not p.startswith(IGNORADOS_PREFIXOS)
    )
    sumidos = sorted(f"{m} {p}" for (m, p) in COBERTOS if (m, p) not in existentes)
    parametros: dict[str, dict[str, list[str]]] = {}
    for chave, conhecidos in COBERTOS.items():
        op = existentes.get(chave)
        if op is None:
            continue
        atuais = _params(op)
        novos_p, sumidos_p = sorted(atuais - conhecidos), sorted(conhecidos - atuais)
        if novos_p or sumidos_p:
            parametros[f"{chave[0]} {chave[1]}"] = {"novos": novos_p, "removidos": sumidos_p}
    esquemas = spec.get("components", {}).get("schemas", {})
    corpos: dict[str, dict[str, list[str]]] = {}
    for nome, conhecidos in CORPOS.items():
        esq = esquemas.get(nome)
        if esq is None:
            corpos[nome] = {"novos": [], "removidos": sorted(conhecidos)}
            continue
        atuais = set(esq.get("properties", {}))
        novos_c, sumidos_c = sorted(atuais - conhecidos), sorted(conhecidos - atuais)
        if novos_c or sumidos_c:
            corpos[nome] = {"novos": novos_c, "removidos": sumidos_c}
    ok = not (novos or sumidos or parametros or corpos)
    return {
        "ok": ok,
        "versao": spec.get("info", {}).get("version"),
        "endpointsNovos": novos,
        "endpointsSumidos": sumidos,
        "parametros": parametros,
        "corpos": corpos,
    }


def buscar_spec(base_url: str, cliente: httpx.Client | None = None) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/v3/api-docs"
    proprio = cliente is None
    cliente = cliente or httpx.Client(timeout=20)
    try:
        r = cliente.get(url)
        r.raise_for_status()
        dado = r.json()
    finally:
        if proprio:
            cliente.close()
    if not isinstance(dado, dict):
        raise ValueError("A resposta de /v3/api-docs não é um objeto JSON.")
    return dado


def relatorio_texto(rel: dict[str, Any]) -> str:
    if rel["ok"]:
        return f"Contrato em dia (versão {rel['versao']}): nada novo na API."
    linhas = [f"A API mudou (versão {rel['versao']}):"]
    for titulo, chave in (
        ("Endpoints novos", "endpointsNovos"),
        ("Endpoints que sumiram", "endpointsSumidos"),
    ):
        if rel[chave]:
            linhas.append(f"- {titulo}:")
            linhas += [f"    {e}" for e in rel[chave]]
    for titulo, chave in (("Parâmetros", "parametros"), ("Corpos", "corpos")):
        for onde, d in rel[chave].items():
            if d["novos"]:
                linhas.append(f"- {titulo} novos em {onde}: {', '.join(d['novos'])}")
            if d["removidos"]:
                linhas.append(f"- {titulo} removidos de {onde}: {', '.join(d['removidos'])}")
    return "\n".join(linhas)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="docnuvem-spec",
        description="Compara a especificação OpenAPI da API com o que a ferramenta cobre.",
    )
    ap.add_argument("--url", help="baseUrl da API (padrão: primeiro perfil do config.json)")
    ap.add_argument("--arquivo", type=Path, help="lê o OpenAPI de um arquivo, sem rede")
    ap.add_argument("--json", action="store_true", help="imprime o relatório em JSON")
    args = ap.parse_args(argv)
    try:
        if args.arquivo:
            spec = json.loads(args.arquivo.read_text(encoding="utf-8"))
        else:
            url = args.url
            if not url:
                cfg = load_config()
                url = next(iter(cfg.perfis.values())).baseUrl
            spec = buscar_spec(url)
    except (ConfigError, OSError, ValueError, httpx.HTTPError) as exc:
        print(f"Não foi possível ler a especificação: {exc}", file=sys.stderr)
        sys.exit(2)
    rel = comparar(spec)
    print(json.dumps(rel, ensure_ascii=False, indent=2) if args.json else relatorio_texto(rel))
    sys.exit(0 if rel["ok"] else 1)


if __name__ == "__main__":
    main()
