"""Aplicação FastAPI base: um único endpoint, ``GET /health``.

Contrato em ``docs/specs/container-stack/contracts/api-health.md``. Este módulo importa só
``fastapi`` e ``artigo2md`` — nunca Docling nem pymupdf4llm (FR-008) — para a imagem da API
continuar mínima. As rotas de negócio ficam para a feature ``project-api``.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from artigo2md import __version__

# Documentação automática desligada: /docs, /redoc e /openapi.json respondem 404 (a API base
# não expõe nada além de /health).
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# Defesa contra DNS rebinding a partir do navegador: só aceita Host 127.0.0.1 ou localhost
# (a porta no header é ignorada pelo middleware). Qualquer outro recebe 400.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.get("/health")
def health() -> dict[str, str]:
    """Indica que o processo da API está vivo."""
    return {"status": "ok", "service": "artigo2md-api", "version": __version__}
