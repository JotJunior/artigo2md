# Imagem da API base do artigo2md. Instala SÓ o grupo de dependências `api` (fastapi, uvicorn):
# nada de Docling nem de motores de conversão. Contexto de build = raiz do repositório
# (ver `build.context` em stack/compose.yaml; `.dockerignore` em modo allowlist).
FROM python:3.13-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

# Camada das dependências: só muda quando pyproject.toml ou uv.lock mudam.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --only-group api

# Código da API (o pacote não é instalado: só src/artigo2md entra, via PYTHONPATH).
COPY src/artigo2md/ src/artigo2md/

USER 10001:10001

EXPOSE 8000
CMD ["uvicorn", "artigo2md.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
