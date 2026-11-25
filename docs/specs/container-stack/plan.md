# Implementation Plan: Infraestrutura local em containers

**Feature**: `container-stack` | **Date**: 2026-11-24 | **Spec**: [spec.md](spec.md)

## Summary

Entregar a base de infraestrutura da versão desktop (roadmap item 1): um `stack/compose.yaml` com Postgres (`postgres:18-trixie`), RabbitMQ com painel (`rabbitmq:4.4-management`) e uma API base FastAPI construída do código do projeto sobre `python:3.13-slim-trixie`, que só expõe `GET /health`. Todas as portas são publicadas só em `127.0.0.1`, com healthchecks, volumes nomeados, `stack/.env.example` e scripts `sh` de subir/derrubar com mensagens em português. O `artigo2md doctor` ganha uma seção de infraestrutura que testa cada serviço por handshake de protocolo sem credenciais e sem dependência nova; o `convert` não muda. A licença de cada serviço, runtime e SO base foi confirmada em fonte oficial nesta etapa (research, "Registro de licenças") e vira `stack/THIRD_PARTY_LICENSES.md`.

## Technical Context

**Language/Version**: Python >= 3.10 no projeto (`pyproject.toml:7`); Python 3.13 dentro da imagem da API (research D3); `sh` POSIX nos scripts
**Primary Dependencies**: Docker Compose (v5.6.1 observado localmente, research S30); FastAPI + uvicorn já existentes (`pyproject.toml:12-13`) via novo dependency group `api`; uv 0.13.9 no build da imagem; nenhuma dependência obrigatória nova
**Storage**: volumes nomeados `pgdata` (Postgres, `/var/lib/postgresql`) e `rabbitmqdata` (`/var/lib/rabbitmq`); nenhum schema nesta feature
**Testing**: pytest (`pyproject.toml:33-34`); integração atrás de `ARTIGO2MD_STACK=1`
**Target Platform**: macOS (app desktop) com runtime Docker; scripts também em Linux (CI `ubuntu-latest`/`macos-latest`, `.github/workflows/tests.yml`)
**Project Type**: cli + serviços locais em containers
**Performance Goals**: stack saudável em até 2 min com imagens baixadas (SC-001); `doctor` < 5 s com a stack fora (SC-005)
**Constraints**: só loopback; nenhuma telemetria/serviço remoto; licenças Princípio II; mensagens em português; não importar docling/pymupdf4llm no processo principal nem na API
**Scale/Scope**: stack monousuário local; ~10 arquivos novos, 2 alterados (`cli.py`, `pyproject.toml` + `uv.lock`), README

## Constitution Check

*GATE: Deve passar antes do Phase 0. Re-checar apos Phase 1.*

| Principio | Status | Notas |
|-----------|--------|-------|
| I. Processamento 100% Local | PASS | portas literais `127.0.0.1:` (D5); sem OAuth/serviço remoto; nenhum recurso de telemetria encontrado nas docs oficiais (D6); imagens só com `--pull missing` (1ª execução) ou `--atualizar-imagens` |
| II. Licença Permissiva | PASS (com pendência rastreada) | PostgreSQL License (permissiva), RabbitMQ MPL-2.0 sem modificação (copyleft fraco), Erlang/OTP e OpenSSL 3 Apache-2.0, Python PSF-2, uv MIT/Apache; SO base (Debian/Ubuntu) só documentado (dec-013); nenhuma dependência nova no `pyproject.toml` além do grupo `api` com pacotes já existentes; licenças das dependências transitivas do grupo `api` (pré-existentes) entram no registro final com fonte oficial (task) |
| III. Mensagens em Português | PASS | scripts e seção do `doctor` em português (contracts) |
| IV. Testes Antes do Commit | PASS | suíte sem Docker + integração com `ARTIGO2MD_STACK=1` (D10) |
| V. Veracidade de Dados | PASS | todo fato externo com fonte S01–S36 datada; escolhas próprias marcadas [PROPOSTA] |

**Pendência de governança (não bloqueia o plan)**: a interpretação de dec-013 (SO base só documentado) ainda precisa de ratificação do operador por nota/emenda na `docs/constitution.md` — esta feature não edita a constitution (spec, Clarifications).

## Project Structure

### Documentation (this feature)

```
docs/specs/container-stack/
├── spec.md
├── plan.md            # este arquivo
├── research.md        # decisões D1–D10, registro de licenças, fontes S01–S36
├── data-model.md
├── quickstart.md
└── contracts/
    ├── api-health.md
    ├── doctor-infra.md
    └── stack-scripts.md
```

### Source Code (repository root)

```
stack/                               # NOVO
├── compose.yaml                     # postgres, rabbitmq, api; name: artigo2md
├── .env.example                     # parâmetros comentados em pt, sem credenciais reais
├── up.sh                            # validações + docker compose up --wait
├── down.sh                          # down; --apagar-dados com confirmação
├── THIRD_PARTY_LICENSES.md          # registro FR-011 (conteúdo em pt)
└── api/
    └── Dockerfile                   # python:3.13-slim-trixie + uv sync --only-group api
.dockerignore                        # NOVO, raiz, allowlist: só pyproject.toml, uv.lock, src/artigo2md/ (research D11.4)
src/artigo2md/
├── api/                             # NOVO pacote (a project-api estende)
│   ├── __init__.py
│   └── app.py                       # FastAPI(docs_url=None, redoc_url=None, openapi_url=None) + TrustedHost; GET /health
├── infra.py                         # NOVO: resolução de portas (env → stack/.env), probes postgres/rabbitmq/api (stdlib, sem proxy) + format em pt
└── cli.py                           # ALTERADO: cmd_doctor imprime a seção (import local de .infra)
tests/
├── test_infra.py                    # NOVO (sem Docker)
├── test_api_base.py                 # NOVO (sem Docker; TestClient + subprocesso sys.modules)
├── test_stack_files.py              # NOVO (estático: compose/.env.example/.gitignore)
├── test_stack_scripts.py            # NOVO (up.sh/down.sh com docker falso no PATH)
└── test_stack_integration.py        # NOVO (ARTIGO2MD_STACK=1)
pyproject.toml                       # ALTERADO: [dependency-groups] api = ["fastapi>=0.115", "uvicorn>=0.30"]
uv.lock                              # REGERADO (uv lock) — o CI usa --locked
.gitignore                           # ALTERADO: stack/.env
README.md                            # ALTERADO: seção "Infraestrutura local (versão desktop)"
```

**Structure Decision**: tudo da stack fica em `stack/` (um lugar para o app desktop chamar depois, `desktop-stack-control`). A API vira pacote `artigo2md.api` para a `project-api` crescer sem mover arquivos. O módulo `infra.py` é importado só dentro de `cmd_doctor` (import local), mantendo `convert` sem nenhum contato com a stack (FR-010). A imagem da API copia `src/artigo2md/` e roda com `PYTHONPATH=/app/src`, usuário não-root `10001`, sistema de arquivos só leitura, sem capabilities (research D11.5), `uvicorn artigo2md.api.app:app --host 0.0.0.0 --port 8000` (sintaxe `uvicorn [OPTIONS] APP`, `--host`, `--port`: research S36; porta 8000 [PROPOSTA]) — `0.0.0.0` é a interface do container; a única saída para o host é `127.0.0.1:${ARTIGO2MD_API_PORT}` (D5).

## Convencoes de Borda

| Camada | Case style | Validacao | Fonte da verdade |
|--------|------------|-----------|------------------|
| Payload `/health` | `snake_case` (chaves de uma palavra) | teste de contrato (`tests/test_api_base.py`) + roundtrip na integração | `contracts/api-health.md` |
| Variáveis de ambiente | `UPPER_SNAKE_CASE`; host ports com prefixo `ARTIGO2MD_`; credenciais com os nomes exigidos pelas imagens (`POSTGRES_*`, `RABBITMQ_DEFAULT_*`, research S03/S11) | `up.sh` + `${VAR:?}` no compose | `contracts/stack-scripts.md` |
| Nomes de serviço/volume | `lowercase` | teste estático | `stack/compose.yaml` |
| Rotas | `/health` (kebab-case para futuras) | 404 para o resto | `contracts/api-health.md` |

**Mapper layer (DB ↔ DTO)**: N/A nesta feature (sem schema). **Validação Zod**: N/A (sem frontend).

## Ordem de implementação sugerida

1. `pyproject.toml` grupo `api` + `uv lock`; `src/artigo2md/api/app.py` + `tests/test_api_base.py`
2. `src/artigo2md/infra.py` + `cli.py` + `tests/test_infra.py` (inclui teste de que `convert` não importa `infra`)
3. `stack/compose.yaml`, `stack/.env.example`, `stack/api/Dockerfile`, `.dockerignore`, `.gitignore` + `tests/test_stack_files.py`
4. `stack/up.sh`, `stack/down.sh` + `tests/test_stack_scripts.py`
5. `tests/test_stack_integration.py` (rodar com `ARTIGO2MD_STACK=1` antes do merge — Princípio IV); validar as hipóteses marcadas no research (pg_isready por TCP durante init, `nc` em Linux, idempotência do `up`, nome dos volumes)
6. `stack/THIRD_PARTY_LICENSES.md` (inclui licenças das dependências transitivas do grupo `api`, cada uma com fonte oficial consultada na implementação) + README

## Riscos e pontos a validar na implementação

- Hipóteses sem fonte direta, marcadas no research: `pg_isready -h 127.0.0.1` não fica saudável durante o init; `up` não recria containers inalterados; `nc -z -w` só verificado no macOS. Resultado da integração real (research, "Observações da integração real"): as duas primeiras CONFIRMADAS por observação; `nc -z -w` observado também num container Ubuntu 24.04 com `netcat-openbsd`, mas o runner real do CI só será conferido na primeira execução do CI (tarefa 6.2.3 segue `[~]`).
- Endurecimento e riscos aceitos dos gates de segurança e documentação: research Decision 11.
- Porta 5432 já ocupada é comum em máquinas de desenvolvimento (FR-013 cobre com mensagem e parâmetro).
- O fechamento do socket pelo `doctor` logo após o handshake gera linhas de log nos servidores — aceitável, documentar.
- `uv.lock` precisa ser regenerado junto com o grupo `api`, ou o CI (`uv sync --locked`) quebra.

## Complexity Tracking

Sem violações de constitution — seção não aplicável.
