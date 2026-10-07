"""Cliente HTTP assíncrono para a API de Importação de Arquivos do Docnuvem."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
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


def _escapar_aspas(texto: str) -> str:
    return texto.replace("'", "'\\''")


@dataclass
class RequisicaoPrevista:
    """Requisição HTTP já montada (URL final, corpo), com token sempre mascarado."""

    method: str
    url: str
    auth_masked: str
    body: str | None = None
    arquivo_nome: str | None = None
    arquivo_caminho: str | None = None

    def texto(self) -> str:
        linhas = [f"{self.method} {self.url}", f"Authorization: {self.auth_masked}"]
        if self.body is not None:
            linhas += ["Content-Type: application/json", "", self.body]
        if self.arquivo_caminho:
            tamanho = ""
            try:
                tamanho = f" ({Path(self.arquivo_caminho).stat().st_size} bytes)"
            except OSError:
                pass
            linhas += [
                "Content-Type: multipart/form-data",
                "",
                f"file: {self.arquivo_nome} <- {self.arquivo_caminho}{tamanho}",
            ]
        return "\n".join(linhas)

    def curl(self) -> str:
        partes = [f"curl -X {self.method} '{_escapar_aspas(self.url)}'"]
        partes.append(f"-H 'Authorization: {self.auth_masked}'")
        if self.body is not None:
            compacto = json.dumps(json.loads(self.body), ensure_ascii=False)
            partes.append("-H 'Content-Type: application/json'")
            partes.append(f"-d '{_escapar_aspas(compacto)}'")
        if self.arquivo_caminho:
            partes.append(f"-F 'file=@{_escapar_aspas(self.arquivo_caminho)}'")
        return " \\\n  ".join(partes)


class PreviaCapturada(Exception):
    """Levantada no modo prévia: a requisição foi montada mas NÃO enviada."""

    def __init__(self, requisicao: RequisicaoPrevista) -> None:
        self.requisicao = requisicao
        super().__init__(requisicao.texto())


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
    response_text: str | None = None
    requisicao: RequisicaoPrevista | None = None
    favorito: bool = False
    # dados para reenviar a mesma chamada
    perfil: PerfilConfig | None = None
    path: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    json_body: dict[str, Any] | None = None


class DocnuvemAPIError(Exception):
    """Erro HTTP (4xx/5xx) ou de rede ao chamar a API do Docnuvem."""

    def __init__(
        self,
        method: str,
        url: str,
        status_code: int | None,
        body_text: str,
        parsed: dict[str, Any] | None = None,
        chamada: CallLogEntry | None = None,
    ) -> None:
        self.method = method
        self.url = url
        self.status_code = status_code
        self.body_text = body_text
        self.parsed = parsed or {}
        self.chamada = chamada
        super().__init__(f"{method} {url} -> {status_code}: {body_text}")


class DocnuvemClient:
    """Wrapper em torno de httpx.AsyncClient: monta URL/headers, trata erro e loga chamadas."""

    def __init__(
        self,
        on_call_logged: Callable[[CallLogEntry], None] | None = None,
        on_documento_id: Callable[[int], None] | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(timeout=TIMEOUT)
        self._on_call_logged = on_call_logged
        self._on_documento_id = on_documento_id
        self._previa = False

    async def aclose(self) -> None:
        await self._client.aclose()

    @contextmanager
    def modo_previa(self) -> Iterator[None]:
        """Dentro do bloco, qualquer chamada levanta PreviaCapturada em vez de enviar."""
        self._previa = True
        try:
            yield
        finally:
            self._previa = False

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
        params_originais = dict(params or {})
        params = self._clean_params({"instancia": perfil.instancia.lower(), **params_originais})
        headers = {"Authorization": f"Bearer {perfil.token}"}
        full_url = str(httpx.Request(method, url, params=params).url)

        arquivo_nome: str | None = None
        arquivo_caminho: str | None = None
        if files and "file" in files:
            arquivo_nome, fh = files["file"]
            arquivo_caminho = str(getattr(fh, "name", ""))
        requisicao = RequisicaoPrevista(
            method=method,
            url=full_url,
            auth_masked=mascarar_token(perfil.token),
            body=json.dumps(json_body, indent=2, ensure_ascii=False)
            if json_body is not None
            else None,
            arquivo_nome=arquivo_nome,
            arquivo_caminho=arquivo_caminho,
        )
        if self._previa:
            raise PreviaCapturada(requisicao)

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
            entry = self._registrar(
                requisicao,
                perfil,
                path,
                params_originais,
                json_body,
                None,
                False,
                str(exc),
                None,
                duration_ms,
            )
            raise DocnuvemAPIError(method, full_url, None, str(exc), chamada=entry) from exc

        duration_ms = (datetime.now() - started).total_seconds() * 1000
        ok = resp.status_code < 400
        entry = self._registrar(
            requisicao,
            perfil,
            path,
            params_originais,
            json_body,
            resp.status_code,
            ok,
            None if ok else resp.text,
            resp.text,
            duration_ms,
        )
        if not ok:
            parsed: dict[str, Any] | None = None
            try:
                parsed = resp.json()
            except ValueError:
                parsed = None
            raise DocnuvemAPIError(
                method, str(resp.url), resp.status_code, resp.text, parsed, chamada=entry
            )
        self._notificar_documento_id(resp)
        return resp

    def _notificar_documento_id(self, resp: httpx.Response) -> None:
        if self._on_documento_id is None:
            return
        try:
            dados = resp.json()
        except ValueError:
            return
        if isinstance(dados, dict) and isinstance(dados.get("documentoId"), int):
            self._on_documento_id(dados["documentoId"])

    def _registrar(
        self,
        requisicao: RequisicaoPrevista,
        perfil: PerfilConfig,
        path: str,
        params: dict[str, Any],
        json_body: dict[str, Any] | None,
        status_code: int | None,
        ok: bool,
        error_body: str | None,
        response_text: str | None,
        duration_ms: float,
    ) -> CallLogEntry:
        entry = CallLogEntry(
            timestamp=datetime.now(),
            method=requisicao.method,
            url=requisicao.url,
            status_code=status_code,
            ok=ok,
            auth_masked=requisicao.auth_masked,
            error_body=error_body,
            duration_ms=duration_ms,
            response_text=response_text,
            requisicao=requisicao,
            perfil=perfil,
            path=path,
            params=params,
            json_body=json_body,
        )
        if self._on_call_logged:
            self._on_call_logged(entry)
        return entry

    async def reenviar(self, entry: CallLogEntry) -> httpx.Response:
        """Repete uma chamada do log com os mesmos dados (reabre o arquivo, se houver)."""
        if entry.perfil is None:
            raise ValueError("Esta entrada do log não guarda dados para reenvio.")
        req = entry.requisicao
        if req is not None and req.arquivo_caminho:
            with Path(req.arquivo_caminho).open("rb") as fh:
                return await self._request(
                    entry.method,
                    entry.perfil,
                    entry.path,
                    params=entry.params,
                    files={"file": (req.arquivo_nome, fh)},
                )
        return await self._request(
            entry.method, entry.perfil, entry.path, params=entry.params, json_body=entry.json_body
        )

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
