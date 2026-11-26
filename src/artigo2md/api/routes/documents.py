"""Rotas de fontes e documentos (`contracts/http-api.md`, "Fontes e documentos"; FR-005 a FR-009, US1).

A API NUNCA lista, lê nem verifica pastas ou arquivos do host (FR-007): os caminhos são texto validado e gravado como
informado. Regras de caminho (FR-006, FR-008, FR-025) vivem em `api/paths.py` (`check_document_path`,
`check_copied_path`, `check_source_path`); aqui só se decide o que vira `rejected` (por documento) e o que recusa o
pedido inteiro (fonte inválida: nada é gravado).

A fonte e todos os documentos válidos de um pedido são gravados NUMA transação: recusa de domínio ou erro do banco
não deixa fonte sem documentos (FR-031). Os caminhos inválidos de uma lista não impedem os válidos (US1-8).
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, ConfigDict

from ...store.models import SOURCE_KINDS, SOURCE_MODES
from ..deps import Services, get_services
from ..errors import ApiError
from ..paths import (
    MAX_PATHS_PER_REQUEST,
    check_copied_path,
    check_document_path,
    check_source_path,
)

MAX_ID = 2**63 - 1  # bigint do banco

ProjectId = Annotated[int, Path(gt=0, le=MAX_ID)]
SourceId = Annotated[int, Path(gt=0, le=MAX_ID)]
DocumentId = Annotated[int, Path(gt=0, le=MAX_ID)]

router = APIRouter(tags=["documents"])


class SourceBody(BaseModel):
    """`kind`/`mode` chegam como texto: valor fora da lista é recusa de DOMÍNIO (422, com a lista válida na
    mensagem), não de formato."""

    model_config = ConfigDict(extra="forbid")
    kind: str
    mode: str
    path: str
    documents: list[str] = []


class DocumentsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: list[str]


# ── serialização explícita (nenhum campo interno vaza) ──────────────────────────
def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def source_to_json(source: Any) -> dict[str, Any]:
    return {
        "id": source.id,
        "project_id": source.project_id,
        "kind": source.kind,
        "mode": source.mode,
        "path": source.path,
        "created_at": _iso(source.created_at),
    }


def document_to_json(document: Any) -> dict[str, Any]:
    return {
        "id": document.id,
        "project_id": document.project_id,
        "source_id": document.source_id,
        "path": document.path,
        "created_at": _iso(document.created_at),
    }


def result_summary_to_json(summary: Any) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "job_id": summary.job_id,
        "markdown_path": summary.markdown_path,
        "engine": summary.engine,
        "score": summary.score,
        "accepted": summary.accepted,
        "created_at": _iso(summary.created_at),
    }


def document_status_to_json(item: Any) -> dict[str, Any]:
    return {
        "document_id": item.document_id,
        "project_id": item.project_id,
        "source_id": item.source_id,
        "path": item.path,
        "status": item.status,
        "latest_job_id": item.latest_job_id,
        "latest_job_requested_at": _iso(item.latest_job_requested_at),
        "last_result": result_summary_to_json(item.last_result),
    }


# ── regras de pedido ────────────────────────────────────────────────────────────
def _check_limit(documents: list[str]) -> None:
    if len(documents) > MAX_PATHS_PER_REQUEST:
        raise ApiError(
            "payload_too_large",
            f"O pedido traz mais de {MAX_PATHS_PER_REQUEST} caminhos; envie em lotes menores.",
        )


def _check_choice(field: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ApiError("validation_failed", f"O campo {field} precisa ser um de: {', '.join(allowed)}.")


def _require_single_document(source_path: str, documents: list[str]) -> None:
    """`kind = file`: `documents` é exatamente `[path]` (combinação de campos inválida = 400, nada é gravado)."""
    if documents != [source_path]:
        raise ApiError(
            "invalid_request",
            "Para uma fonte do tipo file, o campo documents deve conter exatamente o caminho da fonte.",
        )


def _split_documents(
    documents: list[str], mode: str, data_dir_host: str | None, project_id: int
) -> tuple[list[str], list[dict[str, str]]]:
    """Separa os caminhos válidos dos recusados (`rejected`), preservando a ordem. Sem acesso a disco."""
    valid: list[str] = []
    rejected: list[dict[str, str]] = []
    for path in documents:
        message = check_document_path(path)
        if message is None and mode == "copied":
            message = check_copied_path(path, data_dir_host, project_id)
        if message is None:
            valid.append(path)
        else:
            rejected.append({"path": path, "message": message})
    return valid, rejected


async def _register_documents(
    session: Any, project_id: int, source_id: int, valid: list[str], rejected: list[dict[str, str]]
) -> dict[str, Any]:
    created: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    for path in valid:
        document, is_new = await session.add_document(project_id, source_id, path)
        (created if is_new else existing).append(document_to_json(document))
    return {"created": created, "existing": existing, "rejected": rejected}


# ── fontes ──────────────────────────────────────────────────────────────────────
@router.get("/projects/{project_id}/sources")
async def list_sources(project_id: ProjectId, services: Services = Depends(get_services)) -> dict[str, Any]:
    store = await services.get_store()
    return {"sources": [source_to_json(item) for item in await store.list_sources(project_id)]}


@router.post("/projects/{project_id}/sources", status_code=201)
async def add_source(
    project_id: ProjectId, body: SourceBody, services: Services = Depends(get_services)
) -> dict[str, Any]:
    _check_limit(body.documents)
    if body.kind == "file":
        _require_single_document(body.path, body.documents)
    store = await services.get_store()
    host = services.settings.data_dir_host
    async with store.transaction() as session:
        await session.get_project(project_id)  # 404 antes de qualquer recusa de domínio
        _check_choice("kind", body.kind, SOURCE_KINDS)
        _check_choice("mode", body.mode, SOURCE_MODES)
        message = check_source_path(body.path)
        if message is None and body.kind == "file":
            message = check_document_path(body.path)  # a fonte `file` É o documento: o mesmo rigor, sem gravar nada
        if message is None and body.mode == "copied":
            message = check_copied_path(body.path, host, project_id)
        if message is not None:
            raise ApiError("validation_failed", message)
        valid, rejected = _split_documents(body.documents, body.mode, host, project_id)
        source = await session.add_source(project_id, body.kind, body.mode, body.path)
        outcomes = await _register_documents(session, project_id, source.id, valid, rejected)
    return {"source": source_to_json(source), "documents": outcomes}


@router.post("/projects/{project_id}/sources/{source_id}/documents")
async def add_documents(
    project_id: ProjectId, source_id: SourceId, body: DocumentsBody, services: Services = Depends(get_services)
) -> dict[str, Any]:
    _check_limit(body.documents)
    store = await services.get_store()
    host = services.settings.data_dir_host
    async with store.transaction() as session:
        await session.get_project(project_id)
        source = await session.get_source(source_id)
        if source.project_id != project_id:
            raise ApiError("not_found", "Fonte não encontrada.")  # fonte de outro projeto não existe para este
        if source.kind == "file":
            _require_single_document(source.path, body.documents)
        valid, rejected = _split_documents(body.documents, source.mode, host, project_id)
        return await _register_documents(session, project_id, source.id, valid, rejected)


# ── documentos ──────────────────────────────────────────────────────────────────
@router.get("/projects/{project_id}/documents")
async def list_documents(project_id: ProjectId, services: Services = Depends(get_services)) -> dict[str, Any]:
    store = await services.get_store()
    return {"documents": [document_status_to_json(item) for item in await store.list_documents(project_id)]}


@router.get("/documents/{document_id}")
async def get_document(document_id: DocumentId, services: Services = Depends(get_services)) -> dict[str, Any]:
    store = await services.get_store()
    async with store.transaction() as session:  # leitura consistente do documento e do seu status derivado
        document = await session.get_document(document_id)
        for item in await session.list_documents(document.project_id):
            if item.document_id == document.id:
                return document_status_to_json(item)
    raise ApiError("not_found", "Documento não encontrado.")
