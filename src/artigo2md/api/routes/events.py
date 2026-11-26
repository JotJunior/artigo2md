"""`GET /v1/projects/{project_id}/events`: fluxo ao vivo SSE por projeto (`contracts/live-events.md`; FR-019, FR-020).

Projeto inexistente = `404 not_found` JSON ANTES de abrir o fluxo; acima de 32 conexões = `503 too_many_streams`. Mesmas
regras de `Host` e `Origin` das demais rotas (o `OriginGuard` de `api/security.py` vale para todo `/v1`). O corpo é o
gerador de `LiveHub.stream` (`api/live.py`): `event:` + `data:` JSON numa linha, comentário `: keep-alive` a cada 15 s,
sem `id:`/`Last-Event-ID` (ao reconectar, o cliente relê `GET /v1/projects/{id}/documents`).

O assinante é removido do difusor no `finally` do gerador e, como rede de segurança (cliente que sai antes do primeiro
quadro, o gerador nunca chega a rodar), por uma tarefa de fundo da resposta; `unsubscribe` é idempotente.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from ..deps import Services, get_services

MAX_ID = 2**63 - 1  # bigint do banco

ProjectId = Annotated[int, Path(gt=0, le=MAX_ID)]

router = APIRouter(tags=["events"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get("/projects/{project_id}/events")
async def project_events(project_id: ProjectId, services: Services = Depends(get_services)) -> StreamingResponse:
    store = await services.get_store()
    await store.get_project(project_id)  # 404 JSON antes de abrir o fluxo (projeto arquivado existe: o fluxo abre)
    hub = services.live
    subscriber = hub.subscribe(project_id)  # 503 too_many_streams acima do teto
    return StreamingResponse(
        hub.stream(subscriber),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
        background=BackgroundTask(hub.unsubscribe, subscriber),
    )
