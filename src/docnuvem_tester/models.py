"""Schemas pydantic dos requests/responses da API de Importação de Arquivos."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

METODOS_VALIDOS = {"eletronica", "digital", "birdid"}
STATUS_FILTRO_VALIDOS = {
    "pendente",
    "assinado",
    "expirado",
    "cancelado",
    "sem_assinatura",
}


def validar_cpf(cpf: str) -> bool:
    """Valida dígitos verificadores de um CPF (aceita com ou sem pontuação)."""
    digitos = re.sub(r"\D", "", cpf or "")
    if len(digitos) != 11 or digitos == digitos[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(digitos[num]) * ((i + 1) - num) for num in range(i))
        dv = ((soma * 10) % 11) % 10
        if dv != int(digitos[i]):
            return False
    return True


def tem_extensao(nome_arquivo: str) -> bool:
    nome = (nome_arquivo or "").strip()
    if "." not in nome:
        return False
    _, _, ext = nome.rpartition(".")
    return bool(ext)


def validar_data_br(valor: str) -> bool:
    """Valida o formato dd/MM/yyyy esperado pela API para campos DATA."""
    try:
        datetime.strptime(valor, "%d/%m/%Y")
    except ValueError:
        return False
    return True


_NUMERO_BR_RE = re.compile(r"^-?(\d{1,3}(\.\d{3})*|\d+)(,\d+)?$")


def validar_numero_br(valor: str) -> bool:
    """Valida o formato 1.234,56 (sem símbolo de moeda) esperado pela API."""
    return bool(_NUMERO_BR_RE.match(valor))


class ApiModel(BaseModel):
    """Base para modelos de resposta: tolera campos extras/nulos vindos da API."""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# /importar, /enviarParaEnvioInteligente
# ---------------------------------------------------------------------------


class OutputData(ApiModel):
    retorno: str | None = None
    documentoId: int
    diretorioId: int | None = None


# ---------------------------------------------------------------------------
# /api/documento/from-template, /api/modelos
# ---------------------------------------------------------------------------


class DocumentoFromTemplateRequest(BaseModel):
    modelo: str
    nomeArquivo: str | None = None
    nomePasta: str
    nomePastaPai: str | None = None
    referenciaExterna: str | None = Field(default=None, max_length=64)
    loginSolicitante: str | None = None
    variaveis: dict[str, str] = Field(default_factory=dict)


class DocumentoFromTemplateResponse(ApiModel):
    documentoId: int
    referenciaExterna: str | None = None
    nomeArquivo: str | None = None
    diretorioId: int | None = None


class ErroVariaveisResponse(ApiModel):
    retorno: str | None = None
    variaveisFaltantes: list[str] = Field(default_factory=list)


class VariavelModeloDTO(ApiModel):
    chave: str
    rotulo: str | None = None
    tipo: str | None = None
    obrigatorio: bool | None = None
    aceitaPorApi: bool | None = None
    preenchidoPeloDestinatario: bool | None = None
    opcoes: list[str] | None = None


class ModeloDTO(ApiModel):
    id: int | str | None = None
    codigo: str | None = None
    nome: str | None = None
    descricao: str | None = None
    geravelPorApi: bool | None = None
    variaveis: list[VariavelModeloDTO] = Field(default_factory=list)


class ModelosResponse(ApiModel):
    modelos: list[ModeloDTO] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /api/assinatura
# ---------------------------------------------------------------------------


class SignatarioRequest(BaseModel):
    nome: str
    email: str
    cpf: str
    telefone: str | None = None
    ordem: int | None = None

    @field_validator("cpf")
    @classmethod
    def _cpf_valido(cls, v: str) -> str:
        if not validar_cpf(v):
            raise ValueError(f"CPF inválido: {v!r}")
        return v


class SolicitacaoAssinaturaRequest(BaseModel):
    documentoId: int
    signatarios: list[SignatarioRequest] = Field(min_length=1)
    consideraOrdem: bool = False
    dataValidade: str | None = None
    prazoDias: int | None = None
    metodos: list[str] = Field(default_factory=lambda: ["eletronica"])
    textoEmail: str | None = None
    loginSolicitante: str | None = None
    referenciaExterna: str | None = Field(default=None, max_length=64)
    lembretes: list[int] | None = None

    @field_validator("metodos")
    @classmethod
    def _metodos_validos(cls, v: list[str]) -> list[str]:
        invalidos = sorted(set(v) - METODOS_VALIDOS)
        if invalidos:
            raise ValueError(
                f"Métodos inválidos: {', '.join(invalidos)} "
                f"(válidos: {', '.join(sorted(METODOS_VALIDOS))})"
            )
        return v

    @model_validator(mode="after")
    def _ordem_contigua_se_considera_ordem(self) -> "SolicitacaoAssinaturaRequest":
        if self.consideraOrdem:
            if any(s.ordem is None for s in self.signatarios):
                raise ValueError(
                    "consideraOrdem=true exige 'ordem' preenchida em todos os signatários"
                )
            ordens: list[int] = [s.ordem for s in self.signatarios if s.ordem is not None]
            esperado = list(range(len(self.signatarios)))
            if sorted(ordens) != esperado:
                raise ValueError(
                    "consideraOrdem=true exige 'ordem' 0-based e contígua entre os "
                    f"signatários (esperado {esperado}, recebido {sorted(ordens)})"
                )
        return self


# ---------------------------------------------------------------------------
# /api/documento/{id}/status, /api/assinatura (response), DELETE /api/assinatura/{id}
# ---------------------------------------------------------------------------


class SignatarioStatus(ApiModel):
    nome: str | None = None
    email: str | None = None
    telefone: str | None = None
    cpf: str | None = None
    ordem: int | None = None
    status: str | None = None
    statusDescricao: str | None = None
    dataAssinatura: str | None = None
    visualizou: bool | None = None
    dataVisualizacao: str | None = None
    linkAssinatura: str | None = None


class DocumentoStatusResponse(ApiModel):
    documentoId: int | None = None
    nomeArquivo: str | None = None
    diretorioId: int | None = None
    assinaturaId: int | str | None = None
    referenciaExterna: str | None = None
    status: str | None = None
    statusDescricao: str | None = None
    dataSolicitacao: str | None = None
    dataValidade: str | None = None
    dataUltimaAlteracao: str | None = None
    consideraOrdem: bool | None = None
    lembretes: list[int] = Field(default_factory=list)
    dataUltimoLembrete: str | None = None
    signatarios: list[SignatarioStatus] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# GET /api/documentos
# ---------------------------------------------------------------------------


class DocumentoListaItemDTO(ApiModel):
    documentoId: int | None = None
    nomeArquivo: str | None = None
    diretorioId: int | None = None
    assinaturaId: int | str | None = None
    referenciaExterna: str | None = None
    status: str | None = None
    statusDescricao: str | None = None
    dataSolicitacao: str | None = None
    dataUltimaAlteracao: str | None = None


class DocumentosPageResponse(ApiModel):
    total: int | None = None
    pagina: int | None = None
    tamanho: int | None = None
    temMais: bool | None = None
    documentos: list[DocumentoListaItemDTO] = Field(default_factory=list)


class FiltroDocumentos(BaseModel):
    diretorioId: int | None = None
    incluirSubpastas: bool = False
    status: str | None = None
    dataInicio: str | None = None
    dataFim: str | None = None
    pagina: int = 0
    tamanho: int = 50

    @field_validator("status")
    @classmethod
    def _status_valido(cls, v: str | None) -> str | None:
        if v and v not in STATUS_FILTRO_VALIDOS:
            raise ValueError(
                f"Status inválido: {v!r} (válidos: {', '.join(sorted(STATUS_FILTRO_VALIDOS))})"
            )
        return v

    @field_validator("tamanho")
    @classmethod
    def _tamanho_max(cls, v: int) -> int:
        if v > 200:
            raise ValueError("tamanho máximo permitido é 200")
        return v


# ---------------------------------------------------------------------------
# GET /api/documento/{id}/download
# ---------------------------------------------------------------------------


class DocumentoDownloadResponse(ApiModel):
    documentoId: int | None = None
    nomeArquivo: str | None = None
    extensao: str | None = None
    contentType: str | None = None
    tamanho: int | None = None
    urlDownload: str | None = None
    expiraEm: str | None = None
    statusAssinatura: str | None = None
    relatorioAssinatura: str | None = None
