"""Cliente HTTP assíncrono para a API de Importação de Arquivos do DocNuvem."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx

from docnuvem_tester.config import PerfilConfig
from docnuvem_tester.models import (
    DocumentoDownloadResponse,
    DocumentoFromTemplateRequest,
    DocumentoFromTemplateResponse,
    DocumentoStatusResponse,
    DocumentosPageResponse,
    FiltroDocumentos,
    ModelosResponse,
    OutputData,
    SolicitacaoAssinaturaRequest,
)

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def mascarar_token(token: str) -> str:
    if not token:
        return "Bearer ***"
    tail = token[-4:] if len(token) >= 4 else token
    return f"Bearer ***...{tail}"


@dataclass
class CallLogEntry:
    timestamp: datetime
    method: str
    url: str
    status_code: int | None
    ok: bool
    auth_masked: str = ""
    error_body: str | None = None
    duration_ms: float | None = None


class DocNuvemAPIError(Exception):
    """Erro HTTP (4xx/5xx) ou de rede ao chamar a API do DocNuvem."""

    def __init__(
        self,
        method: str,
        url: str,
        status_code: int | None,
        body_text: str,
        parsed: dict[str, Any] | None = None,
    ) -> None:
        self.method = method
        self.url = url
        self.status_code = status_code
        self.body_text = body_text
        self.parsed = parsed or {}
        super().__init__(f"{method} {url} -> {status_code}: {body_text}")


class DocNuvemClient:
    """Wrapper em torno de httpx.AsyncClient: monta URL/headers, trata erro e loga chamadas."""

    def __init__(self, on_call_logged: Callable[[CallLogEntry], None] | None = None) -> None:
        self._client = httpx.AsyncClient(timeout=TIMEOUT)
        self._on_call_logged = on_call_logged

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
        """Remove params None/"" para nunca enviar query vazia (ex.: status=)."""
        limpo = {}
        for k, v in params.items():
            if v is None:
                continue
            if isinstance(v, str) and v.strip() == "":
                continue
            limpo[k] = v
        return limpo

    async def _request(
        self,
        method: str,
        perfil: PerfilConfig,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = perfil.baseUrl.rstrip("/") + path
        params = self._clean_params({"instancia": perfil.instancia.lower(), **(params or {})})
        headers = {"Authorization": f"Bearer {perfil.token}"}
        started = datetime.now()
        try:
            resp = await self._client.request(
                method,
                url,
                params=params,
                json=json_body,
                files=files,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            duration_ms = (datetime.now() - started).total_seconds() * 1000
            full_url = str(httpx.Request(method, url, params=params).url)
            self._log(method, full_url, None, False, str(exc), duration_ms, perfil.token)
            raise DocNuvemAPIError(method, full_url, None, str(exc)) from exc

        duration_ms = (datetime.now() - started).total_seconds() * 1000
        full_url = str(resp.url)
        ok = resp.status_code < 400
        self._log(
            method,
            full_url,
            resp.status_code,
            ok,
            None if ok else resp.text,
            duration_ms,
            perfil.token,
        )
        if not ok:
            parsed: dict[str, Any] | None = None
            try:
                parsed = resp.json()
            except ValueError:
                parsed = None
            raise DocNuvemAPIError(method, full_url, resp.status_code, resp.text, parsed)
        return resp

    def _log(
        self,
        method: str,
        url: str,
        status_code: int | None,
        ok: bool,
        error_body: str | None,
        duration_ms: float,
        token: str = "",
    ) -> None:
        entry = CallLogEntry(
            timestamp=datetime.now(),
            method=method,
            url=url,
            status_code=status_code,
            ok=ok,
            auth_masked=mascarar_token(token),
            error_body=error_body,
            duration_ms=duration_ms,
        )
        if self._on_call_logged:
            self._on_call_logged(entry)

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    async def importar(
        self,
        perfil: PerfilConfig,
        *,
        nome_arquivo: str,
        nome_pasta: str,
        nome_pasta_pai: str | None,
        caminho_arquivo: Path,
    ) -> OutputData:
        with caminho_arquivo.open("rb") as fh:
            resp = await self._request(
                "POST",
                perfil,
                "/importar",
                params={
                    "nomeArquivo": nome_arquivo,
                    "nomePasta": nome_pasta,
                    "nomePastaPai": nome_pasta_pai,
                },
                files={"file": (nome_arquivo, fh)},
            )
        return OutputData.model_validate(resp.json())

    async def enviar_para_envio_inteligente(
        self, perfil: PerfilConfig, *, nome_arquivo: str, caminho_arquivo: Path
    ) -> OutputData:
        with caminho_arquivo.open("rb") as fh:
            resp = await self._request(
                "POST",
                perfil,
                "/enviarParaEnvioInteligente",
                params={"nomeArquivo": nome_arquivo},
                files={"file": (nome_arquivo, fh)},
            )
        return OutputData.model_validate(resp.json())

    async def documento_from_template(
        self, perfil: PerfilConfig, req: DocumentoFromTemplateRequest
    ) -> DocumentoFromTemplateResponse:
        resp = await self._request(
            "POST",
            perfil,
            "/api/documento/from-template",
            json_body=req.model_dump(exclude_none=True),
        )
        return DocumentoFromTemplateResponse.model_validate(resp.json())

    async def get_modelos(self, perfil: PerfilConfig) -> ModelosResponse:
        resp = await self._request("GET", perfil, "/api/modelos")
        return ModelosResponse.model_validate(resp.json())

    async def criar_assinatura(
        self, perfil: PerfilConfig, req: SolicitacaoAssinaturaRequest
    ) -> DocumentoStatusResponse:
        resp = await self._request(
            "POST",
            perfil,
            "/api/assinatura",
            json_body=req.model_dump(exclude_none=True),
        )
        return DocumentoStatusResponse.model_validate(resp.json())

    async def cancelar_assinatura(
        self, perfil: PerfilConfig, assinatura_id: str, motivo: str | None
    ) -> DocumentoStatusResponse:
        resp = await self._request(
            "DELETE",
            perfil,
            f"/api/assinatura/{assinatura_id}",
            params={"motivo": motivo},
        )
        return DocumentoStatusResponse.model_validate(resp.json())

    async def listar_documentos(
        self, perfil: PerfilConfig, filtro: FiltroDocumentos
    ) -> DocumentosPageResponse:
        resp = await self._request(
            "GET",
            perfil,
            "/api/documentos",
            params={
                "diretorioId": filtro.diretorioId,
                "incluirSubpastas": filtro.incluirSubpastas,
                "status": filtro.status,
                "dataInicio": filtro.dataInicio,
                "dataFim": filtro.dataFim,
                "pagina": filtro.pagina,
                "tamanho": filtro.tamanho,
            },
        )
        return DocumentosPageResponse.model_validate(resp.json())

    async def get_status(
        self, perfil: PerfilConfig, documento_id: int
    ) -> DocumentoStatusResponse:
        resp = await self._request(
            "GET", perfil, f"/api/documento/{documento_id}/status"
        )
        return DocumentoStatusResponse.model_validate(resp.json())

    async def get_download(
        self, perfil: PerfilConfig, documento_id: int
    ) -> DocumentoDownloadResponse:
        resp = await self._request(
            "GET", perfil, f"/api/documento/{documento_id}/download"
        )
        return DocumentoDownloadResponse.model_validate(resp.json())
