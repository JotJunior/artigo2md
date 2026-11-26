"""Rotas de projetos (`contracts/http-api.md`, "Projetos"; FR-001 a FR-003, US1).

Serialização explícita (`project_to_json`): nenhum campo interno vaza e as datas saem em ISO 8601 com fuso.
Regras de nome (unicidade entre ativos por NFC + `casefold`: maiúsculas/minúsculas colidem, mas uma letra com acento
não colide com a sem acento; nome vazio) ficam no `store`; o erro volta
traduzido por `api/errors.py` (409 `project_name_taken`, 422 `validation_failed`, 404 `not_found`).

`DELETE /v1/projects/{id}` (FR-004): numa transação lê os jobs, confere a permissão de remoção dos diretórios
derivados (`api/files.py`, sem `chmod`) e exclui o projeto; só DEPOIS do COMMIT os diretórios são removidos.

Fluxo ao vivo (`live-events.md`): `archive` e `DELETE` emitem um `job_state cancelled` por job não final cancelado,
DEPOIS do COMMIT (o evento nunca precede a gravação); o `DELETE` ainda encerra os fluxos do projeto. Os jobs são lidos
na MESMA transação que cancela (`list_open_jobs` trava o projeto), então a lista é exatamente a que foi cancelada.
"""
from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ..deps import Services, get_services, get_store
from ..files import check_removable, plan_removal, remove_planned
from ..live import MSG_JOB_CANCELLED_ARCHIVE, MSG_JOB_CANCELLED_DELETE

MAX_ID = 2**63 - 1  # bigint do banco

ProjectId = Annotated[int, Path(gt=0, le=MAX_ID)]

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectBody(BaseModel):
    """`{"name": str}`. Vazio e nome em uso são recusas de DOMÍNIO (422/409), decididas pelo `store`."""

    model_config = ConfigDict(extra="forbid")
    name: str


def project_to_json(project: Any) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "status": project.status,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


@router.get("")
async def list_projects(include_archived: bool = Query(False), store: Any = Depends(get_store)) -> dict[str, Any]:
    projects = await store.list_projects(include_archived=include_archived)
    return {"projects": [project_to_json(item) for item in projects]}


@router.post("", status_code=201)
async def create_project(body: ProjectBody, store: Any = Depends(get_store)) -> dict[str, Any]:
    return project_to_json(await store.create_project(body.name))


@router.get("/{project_id}")
async def get_project(project_id: ProjectId, store: Any = Depends(get_store)) -> dict[str, Any]:
    return project_to_json(await store.get_project(project_id))


@router.patch("/{project_id}")
async def rename_project(project_id: ProjectId, body: ProjectBody, store: Any = Depends(get_store)) -> dict[str, Any]:
    return project_to_json(await store.rename_project(project_id, body.name))


@router.post("/{project_id}/archive")
async def archive_project(project_id: ProjectId, services: Services = Depends(get_services)) -> JSONResponse:
    store = await services.get_store()
    async with store.transaction() as session:
        open_jobs = await session.list_open_jobs(project_id)  # 404 se não existe; trava o projeto
        outcome = await session.archive_project(project_id)
    for job in open_jobs:  # COMMIT feito; o fluxo continua aberto (o projeto pode ser reativado)
        services.live.job_state(project_id, job.id, job.document_id, "cancelled", MSG_JOB_CANCELLED_ARCHIVE)
    return JSONResponse(
        {"project": project_to_json(outcome.project), "cancelled_job_ids": list(outcome.cancelled_job_ids)}
    )


@router.post("/{project_id}/unarchive")
async def unarchive_project(project_id: ProjectId, store: Any = Depends(get_store)) -> dict[str, Any]:
    return project_to_json(await store.unarchive_project(project_id))


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: ProjectId, services: Services = Depends(get_services)) -> Response:
    store = await services.get_store()
    mount = services.settings.data_dir_mount
    async with store.transaction() as session:
        job_ids = await session.list_project_job_ids(project_id)  # 404 se o projeto não existe
        open_jobs = await session.list_open_jobs(project_id)  # cancelados pela exclusão; viram eventos ao vivo
        plan = await asyncio.to_thread(plan_removal, mount, project_id, job_ids)
        # Antes do COMMIT: se o usuário do container não puder remover os diretórios, nada muda no banco (503).
        await asyncio.to_thread(check_removable, plan)
        await session.delete_project(project_id)
    # COMMIT feito: avisa os fluxos (um `cancelled` por job não final) e os encerra; só então os arquivos.
    for job in open_jobs:
        services.live.job_state(project_id, job.id, job.document_id, "cancelled", MSG_JOB_CANCELLED_DELETE)
    services.live.close_project(project_id)
    # Arquivos: falha de remoção vai ao log e não desfaz a exclusão.
    await asyncio.to_thread(remove_planned, plan, project_id)
    return Response(status_code=204)
