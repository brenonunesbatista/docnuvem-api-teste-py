"""Comparar duas instâncias (perfis) só com leituras: modelos, pastas ou diagnóstico."""

from __future__ import annotations

from typing import Any

import httpx

from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.download import ErroDownload, mapa_pastas

TIPOS = ("modelos", "pastas", "diagnostico")


def _modelos(cliente: httpx.Client, perfil: PerfilConfig) -> dict[str, dict[str, str]]:
    r = cliente.get(
        perfil.baseUrl.rstrip("/") + "/api/modelos",
        params={"instancia": perfil.instancia.lower()},
        headers={"Authorization": f"Bearer {perfil.token}"},
        timeout=30,
    )
    if r.status_code != 200:
        raise ErroDownload(f"A API respondeu HTTP {r.status_code} em /api/modelos.")
    try:
        lista = r.json().get("modelos") or []
    except (ValueError, AttributeError) as exc:
        raise ErroDownload("Resposta inesperada da API em /api/modelos.") from exc
    modelos: dict[str, dict[str, str]] = {}
    for m in lista:
        if not isinstance(m, dict):
            continue
        variaveis = sorted(
            str(v.get("chave")) for v in m.get("variaveis") or [] if isinstance(v, dict)
        )
        modelos[str(m.get("nome") or f"(modelo {m.get('id')})")] = {
            "código": str(m.get("codigo") or ""),
            "gerável por API": "sim" if m.get("geravelPorApi") else "não",
            "variáveis": ", ".join(variaveis) or "(nenhuma)",
        }
    return modelos


def _pastas(cliente: httpx.Client, perfil: PerfilConfig) -> dict[str, dict[str, str]]:
    mapa = mapa_pastas(
        cliente,
        perfil.baseUrl.rstrip("/"),
        {"Authorization": f"Bearer {perfil.token}"},
        perfil.instancia.lower(),
    )
    return {caminho: {} for caminho in mapa.values()}


def comparar_mapas(a: dict[str, dict[str, str]], b: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Diferenças entre dois conjuntos indexados por chave (nome do modelo, caminho…)."""
    diferentes = []
    iguais = 0
    for chave in sorted(a.keys() & b.keys()):
        campos = [
            {"campo": c, "a": a[chave].get(c, ""), "b": b[chave].get(c, "")}
            for c in sorted(a[chave].keys() | b[chave].keys())
            if a[chave].get(c, "") != b[chave].get(c, "")
        ]
        if campos:
            diferentes.append({"chave": chave, "campos": campos})
        else:
            iguais += 1
    return {
        "apenasA": sorted(a.keys() - b.keys()),
        "apenasB": sorted(b.keys() - a.keys()),
        "diferentes": diferentes,
        "iguais": iguais,
    }


def _diagnostico(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Compara só a situação (ok/aviso/erro…) de cada verificação: tempos sempre variam."""
    por_a = {i["id"]: i for i in a["itens"]}
    por_b = {i["id"]: i for i in b["itens"]}
    diferentes = []
    iguais = 0
    for id_, ia in por_a.items():
        ib = por_b.get(id_)
        if ib is None:
            continue
        if ia["nivel"] != ib["nivel"]:
            diferentes.append(
                {
                    "chave": ia["titulo"],
                    "campos": [
                        {
                            "campo": "situação",
                            "a": f"{ia['nivel']}: {ia['detalhe']}",
                            "b": f"{ib['nivel']}: {ib['detalhe']}",
                        }
                    ],
                }
            )
        else:
            iguais += 1
    return {"apenasA": [], "apenasB": [], "diferentes": diferentes, "iguais": iguais}


def comparar(
    cliente: httpx.Client,
    tipo: str,
    a: tuple[str, PerfilConfig],
    b: tuple[str, PerfilConfig],
) -> dict[str, Any]:
    from docnuvem_tester.web import diagnosticar  # evita import circular

    lados: dict[str, dict[str, Any]] = {}
    dados: dict[str, Any] = {}
    for rotulo, (nome, perfil) in (("a", a), ("b", b)):
        lados[rotulo] = {"perfil": nome, "total": None, "erro": ""}
        try:
            if tipo == "modelos":
                dados[rotulo] = _modelos(cliente, perfil)
                lados[rotulo]["total"] = len(dados[rotulo])
            elif tipo == "pastas":
                dados[rotulo] = _pastas(cliente, perfil)
                lados[rotulo]["total"] = len(dados[rotulo])
            else:
                dados[rotulo] = diagnosticar(cliente, perfil)
        except ErroDownload as exc:
            lados[rotulo]["erro"] = str(exc)
        except httpx.HTTPError as exc:
            lados[rotulo]["erro"] = f"Falha de conexão: {exc}"
    resultado: dict[str, Any] = {"tipo": tipo, "a": lados["a"], "b": lados["b"]}
    if lados["a"]["erro"] or lados["b"]["erro"]:
        resultado.update(
            apenasA=[], apenasB=[], diferentes=[], iguais=0, mesmo=False, comparou=False
        )
        return resultado
    corpo = (
        _diagnostico(dados["a"], dados["b"])
        if tipo == "diagnostico"
        else comparar_mapas(dados["a"], dados["b"])
    )
    resultado.update(corpo)
    resultado["comparou"] = True
    resultado["mesmo"] = not (corpo["apenasA"] or corpo["apenasB"] or corpo["diferentes"])
    return resultado
