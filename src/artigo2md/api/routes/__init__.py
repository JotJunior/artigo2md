"""Rotas de negócio da API sob o prefixo `/v1` (`contracts/http-api.md`, "Versionamento").

`v1_router` é o único ponto de registro: cada grupo de recurso (projetos, documentos, jobs, eventos) inclui o seu
roteador aqui nas fases seguintes. O `/health` NÃO fica neste roteador (sem prefixo, inalterado — FR-033).
"""
from __future__ import annotations

from fastapi import APIRouter

V1_PREFIX = "/v1"


def build_v1_router() -> APIRouter:
    """Roteador `/v1` com os grupos de recurso já incluídos. Importação preguiçosa: os módulos de rota carregam
    `deps`/`errors` (e, via eles, o pacote `store`), o que `artigo2md.api.app` não pode fazer no import."""
    from . import documents, events, jobs, projects, results, status

    router = APIRouter(prefix=V1_PREFIX)
    router.include_router(projects.router)
    router.include_router(documents.router)
    router.include_router(jobs.router)
    router.include_router(events.router)
    router.include_router(results.router)
    router.include_router(status.router)
    return router
