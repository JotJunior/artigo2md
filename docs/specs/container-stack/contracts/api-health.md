# Contracts: API base — endpoint de saúde

> **[PROPOSTA — a validar na implementação]**: contrato novo, desenhado nesta feature (não existe API anterior).
> As features seguintes (`project-api`) acrescentam rotas sobre esta base.

## GET /health

**Method**: GET /health
**Auth**: nenhuma
**Host/porta**: dentro do container, `0.0.0.0:8000`; no host, só `127.0.0.1:${ARTIGO2MD_API_PORT}` (padrão `8778`)

### Request

Sem corpo, sem querystring.

### Response (200)

`Content-Type` JSON (conferido no teste de contrato; não afirmado aqui como comportamento do framework)

| Field | Type | Description |
|-------|------|-------------|
| status | string | sempre `"ok"` quando o processo responde |
| service | string | sempre `"artigo2md-api"` |
| version | string | valor de `artigo2md.__version__` (hoje `"0.1.0"`, `src/artigo2md/__init__.py:2`) |

Exemplo: `{"status": "ok", "service": "artigo2md-api", "version": "0.1.0"}`

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 404 | — | qualquer outra rota: a API base não expõe nada além de `/health` (US4 cenário 2); o status é verificado por teste, o corpo não faz parte do contrato |
| 405 | — | `/health` com método diferente de GET (POST, PUT, DELETE, PATCH, HEAD, OPTIONS): nunca 2xx. Observado em 2026-11-24 com FastAPI/Starlette do `uv.lock` (Starlette 1.10.0) numa aplicação de teste equivalente: 405 para os seis métodos; o status é fixado por teste de contrato (tarefa 2.2.4), o corpo não faz parte do contrato |

### Regras

- `TrustedHostMiddleware` com `allowed_hosts=["127.0.0.1", "localhost"]`: `Host` diferente recebe 400 (fonte: https://fastapi.tiangolo.com/advanced/middleware/, research S38).
- **`Host` com porta**: o header `Host: 127.0.0.1:<porta>` (forma enviada pelo `doctor` via `http.client` e pelo healthcheck interno, `127.0.0.1:8000`) é aceito, porque o middleware compara só o host depois de separar a porta (fonte: código instalado, `.venv/lib/python3.12/site-packages/starlette/middleware/trustedhost.py`, `parse_host_header` + `host = parsed_host.host.lower()`; observado também numa aplicação de teste: `127.0.0.1:8778` e `localhost:1` → 200, `evil.com` e `evil.com:80` → 400, 2026-11-24). A regra é fixada por teste de contrato (tarefa 2.2.4); `Host` ausente ou malformado recebe 400.

- A aplicação é criada com `docs_url=None`, `redoc_url=None` e `openapi_url=None`, para que `/docs`, `/redoc` e `/openapi.json` também respondam 404 (fonte: https://fastapi.tiangolo.com/tutorial/metadata/, consultada em 2026-11-24 — research S34).
- O módulo `artigo2md.api.app` importa só `fastapi` e `artigo2md` (`__version__`); importar o módulo NÃO pode colocar `docling` nem `pymupdf4llm` em `sys.modules` (FR-008, verificado por teste em subprocesso).
- Nomes de chave em inglês, `snake_case` de uma palavra (regra global de sintaxe).

## Versionamento e compatibilidade do contrato

Decisão do operador (block-004, item 3): a estratégia é definida nesta feature, não adiada para a `project-api`. Regras [PROPOSTA — validadas por teste de contrato na implementação, tarefa 2.2.6]:

- **`/health` é estável e sem prefixo de versão**: o caminho `/health` e os campos `status`, `service` e `version` (nome, tipo e significado) NÃO são removidos, renomeados nem mudam de tipo enquanto este contrato vigorar. `status` continua `"ok"` e `service` continua `"artigo2md-api"`.
- **Mudanças compatíveis (aditivas)**: acrescentar campos novos ao JSON é permitido sem mudar o contrato; todo consumidor (o `doctor`, o healthcheck do compose, a `project-api`, o app desktop) MUST ignorar chaves desconhecidas e depender só de `status == "ok"` (o `doctor` já o faz: contracts/doctor-infra.md).
- **Mudança incompatível** (remover/renomear/retipar campo, mudar o caminho ou o status de sucesso): exige novo contrato em `contracts/` com nova spec da feature responsável, e o `/health` atual continua respondendo no formato antigo durante a transição. Rotas de negócio da `project-api` entram sem alterar `/health`; se ela adotar prefixo de versão (ex.: `/v1/...`), o prefixo vale só para as rotas dela — o prefixo é decisão da `project-api`, não desta feature.
- **`version` é a versão do pacote** (`artigo2md.__version__`), NÃO a versão do contrato; consumidores não decidem compatibilidade pelo valor de `version`.
- **Mudança de comportamento** (ex.: nova resposta a outros métodos HTTP, regra de `Host`) passa por revisão deste arquivo antes do código.

