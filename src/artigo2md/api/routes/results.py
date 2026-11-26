"""Resultado, Markdown, histórico e exportação (`contracts/http-api.md`, "Resultado, Markdown, histórico e exportação";
FR-021 a FR-025, US4).

"Resultado vigente" = `last_result` de `list_documents`: o resultado do job `succeeded` mais recente do documento. Um job
mais novo que falhou não apaga o resultado vigente anterior. Documento sem resultado vigente responde `404 no_result`
SEM acessar o disco.

O Markdown é lido só pela abertura confinada de `api/paths.py` (via `api/export.py`), nunca por `open()` direto no caminho
gravado. A exportação em zip (`api/export.py`) é uma por vez: a segunda responde `409 export_in_progress` enquanto a
primeira não terminou de ser entregue (o arquivo temporário ocupa o tmpfs até lá).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import IO, Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from .. import export as export_module
from ..deps import Services, get_services
from ..errors import ApiError
from .jobs import attempt_to_json, quality_to_json

logger = logging.getLogger("artigo2md.api")

MAX_ID = 2**63 - 1  # bigint do banco
MAX_PAGE_LIMIT = 500  # `MAX_EVENTS_PAGE` do store
MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"
ZIP_MEDIA_TYPE = "application/zip"

DocumentId = Annotated[int, Path(gt=0, le=MAX_ID)]
JobId = Annotated[int, Path(gt=0, le=MAX_ID)]
ProjectId = Annotated[int, Path(gt=0, le=MAX_ID)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]
Offset = Annotated[int, Query(ge=0, le=MAX_ID)]

router = APIRouter(tags=["results"])


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def result_to_json(result: Any) -> dict[str, Any]:
    return {
        "job_id": result.job_id,
        "markdown_path": result.markdown_path,
        "engine": result.engine,
        "quality": quality_to_json(result.quality),
        "warnings": list(result.warnings),
        "created_at": _iso(result.created_at),
    }


def history_event_to_json(event: Any) -> dict[str, Any]:
    return {
        "id": event.id,
        "project_id": event.project_id,
        "document_id": event.document_id,
        "job_id": event.job_id,
        "event_type": event.event_type,
        "occurred_at": _iso(event.occurred_at),
        "message": event.message,
    }


async def _status_of(session: Any, document: Any) -> Any:
    """`DocumentStatus` do documento (mesma transação que o leu)."""
    for item in await session.list_documents(document.project_id):
        if item.document_id == document.id:
            return item
    raise ApiError("not_found", "Documento não encontrado.")


# ── resultado ───────────────────────────────────────────────────────────────────
@router.get("/documents/{document_id}/result")
async def get_document_result(document_id: DocumentId, services: Services = Depends(get_services)) -> dict[str, Any]:
    store = await services.get_store()
    async with store.transaction() as session:
        document = await session.get_document(document_id)  # 404 not_found
        last = (await _status_of(session, document)).last_result
        if last is None:
            raise ApiError("no_result")
        result = await session.get_result(last.job_id)
        if result is None:  # não deveria ocorrer: `last_result` vem de `job_results`
            raise ApiError("no_result")
        attempts = await session.list_attempts(last.job_id)
    return {"result": result_to_json(result), "attempts": [attempt_to_json(item) for item in attempts]}


@router.get("/jobs/{job_id}/result")
async def get_job_result(job_id: JobId, services: Services = Depends(get_services)) -> dict[str, Any]:
    store = await services.get_store()
    result = await store.get_result(job_id)  # 404 not_found se o job não existe
    if result is None:
        raise ApiError("no_result")
    return result_to_json(result)


# ── histórico ───────────────────────────────────────────────────────────────────
@router.get("/projects/{project_id}/history")
async def project_history(
    project_id: ProjectId, limit: Limit = 100, offset: Offset = 0, services: Services = Depends(get_services)
) -> dict[str, Any]:
    store = await services.get_store()
    async with store.transaction() as session:
        await session.get_project(project_id)  # 404: `list_events` não distingue projeto inexistente de sem eventos
        events = await session.list_events(project_id=project_id, limit=limit, offset=offset)
    return {"events": [history_event_to_json(item) for item in events]}


@router.get("/documents/{document_id}/history")
async def document_history(
    document_id: DocumentId, limit: Limit = 100, offset: Offset = 0, services: Services = Depends(get_services)
) -> dict[str, Any]:
    store = await services.get_store()
    async with store.transaction() as session:
        await session.get_document(document_id)  # 404
        events = await session.list_events(document_id=document_id, limit=limit, offset=offset)
    return {"events": [history_event_to_json(item) for item in events]}


# ── Markdown ────────────────────────────────────────────────────────────────────
@router.get("/documents/{document_id}/markdown")
async def get_markdown(document_id: DocumentId, services: Services = Depends(get_services)) -> Response:
    store = await services.get_store()
    async with store.transaction() as session:
        document = await session.get_document(document_id)
        last = (await _status_of(session, document)).last_result
    if last is None:
        raise ApiError("no_result")  # sem resultado: nada é aberto no disco
    settings = services.settings
    content = await asyncio.to_thread(
        export_module.read_markdown, last.markdown_path, settings.data_dir_host, settings.data_dir_mount
    )
    return Response(content=content, media_type=MARKDOWN_MEDIA_TYPE)


# ── exportação ──────────────────────────────────────────────────────────────────
async def _stream_file(handle: IO[bytes], release: Any) -> AsyncIterator[bytes]:
    try:
        while True:
            chunk = await asyncio.to_thread(handle.read, export_module.CHUNK_BYTES)
            if not chunk:
                return
            yield chunk
    finally:
        release()


@router.get("/projects/{project_id}/export")
async def export_project(project_id: ProjectId, services: Services = Depends(get_services)) -> StreamingResponse:
    store = await services.get_store()
    async with store.transaction() as session:
        await session.get_project(project_id)
        statuses = await session.list_documents(project_id)
    items = [
        export_module.Exportable(item.document_id, item.path, item.last_result.markdown_path, item.last_result.created_at)
        for item in statuses
        if item.last_result is not None
    ]
    if not items:
        raise ApiError("no_result", "O projeto ainda não tem nenhum documento convertido para exportar.")
    settings = services.settings
    if not settings.data_dir_host or not settings.data_dir_mount:
        raise ApiError("data_dir_unavailable")
    if services.export_active:
        raise ApiError("export_in_progress")
    services.export_active = True  # sem `await` entre a conferência e a marca: atômico no laço da API
    handle: IO[bytes] | None = None
    try:
        handle, size = await asyncio.to_thread(
            export_module.build_zip, items, settings.data_dir_host, settings.data_dir_mount
        )
    except BaseException:
        services.export_active = False
        raise

    released = False

    def release() -> None:
        """Fecha o arquivo (devolve o espaço do tmpfs) e libera a próxima exportação; idempotente."""
        nonlocal released
        if released:
            return
        released = True
        try:
            handle.close()  # type: ignore[union-attr]
        finally:
            services.export_active = False

    headers = {
        "Content-Disposition": f'attachment; filename="projeto-{project_id}.zip"',
        "Content-Length": str(size),
    }
    return StreamingResponse(
        _stream_file(handle, release), media_type=ZIP_MEDIA_TYPE, headers=headers, background=BackgroundTask(release)
    )
