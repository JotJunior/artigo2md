"""`GET /v1/status`: sinal mínimo de consumidor parado (`contracts/http-api.md`, "Estado do consumidor"; FR-017, FR-033).

Lê SÓ estado em memória das threads de fundo (nenhuma consulta ao banco ou à fila) e por isso responde mesmo com o banco
e o broker fora. `GET /health` NÃO muda: segue só dizendo que o processo está vivo.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import Services, get_services

router = APIRouter(tags=["status"])


@router.get("/status")
async def get_status(services: Services = Depends(get_services)) -> dict[str, Any]:
    consumer, publisher = services.consumer, services.broker
    return {
        "consumer": {
            "connected": bool(getattr(consumer, "connected", False)),
            "last_event_at": getattr(consumer, "last_event_at", None),
        },
        "publisher": {"connected": bool(getattr(publisher, "connected", False))},
    }
