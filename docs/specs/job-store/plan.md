# Implementation Plan: Persistência da versão desktop (job-store)

**Feature**: `job-store` | **Date**: 2026-11-25 | **Spec**: [spec.md](spec.md)

## Summary

Entregar a persistência da versão desktop (roadmap item 3): o modelo de dados de projetos, fontes, documentos,
jobs, tentativas por motor, resultados com qualidade e eventos de histórico no Postgres da `container-stack`;
migrações versionadas e só de avanço; e uma camada de repositório assíncrona (`artigo2md.store`) usada só pela
futura `project-api`. Driver `asyncpg` 0.32.0 (Apache-2.0) e runner de migração próprio em SQL puro, com
`schema_migrations` (versão, instante, checksum SHA-256) e `pg_advisory_lock` — escolhas do operador no block-005
(dec-024). As regras críticas (um job não final por documento, nome único entre projetos ativos, transições de
job, resultado só para job concluído, integridade entre projeto/documento/job) ficam garantidas pelo próprio
banco. `convert`, `doctor` e `serve` não mudam e não importam a camada.

## Technical Context

**Language/Version**: Python >= 3.10 no projeto (`pyproject.toml:7`); Python 3.13 na imagem da API (`stack/api/Dockerfile:4`); SQL do PostgreSQL 18 (`postgres:18-trixie`, `stack/compose.yaml`)
**Primary Dependencies**: `asyncpg>=0.32.0,<0.33` (nova; grupo `api` + extra `dev`; research D1, D2); biblioteca padrão para configuração (reuso de `infra.parse_env_file`)
**Storage**: PostgreSQL da stack, volume `pgdata`; 7 tabelas + `schema_migrations` (data-model)
**Testing**: pytest; testes de contrato sem banco sempre; integração atrás de `ARTIGO2MD_DB_TESTS=1` com banco temporário (research D9)
**Target Platform**: macOS (host) e container `artigo2md-api:local` (Linux, Python 3.13), conforme o briefing 2.0 (fonte: `docs/briefing.md`, versão desktop macOS) e `stack/compose.yaml`; CI `ubuntu-latest`/`macos-latest` com Python 3.12 (fonte: `.github/workflows/tests.yml`)
**Project Type**: biblioteca interna (camada de acesso) dentro do pacote `artigo2md`
**Performance Goals**: migrações em banco vazio ≤ 10 s (SC-001); `list_documents` com 1.000 documentos ≤ 2 s (SC-003)
**Constraints**: só loopback ou serviço interno `postgres` (este só se resolver para endereço loopback/privado, e a conexão usa o endereço validado); nenhuma telemetria; senha nunca em mensagem/log; SQL só com parâmetros `$n`; sintaxe em inglês e mensagens em português; imagem da API sem Docling/pymupdf4llm
**Scale/Scope**: banco local monousuário; só 1.000 documentos por projeto (SC-003) tem meta, os demais volumes e tempos estão declarados como não especificados (decisão do operador, block-006); ~8 arquivos novos em `src/artigo2md/store/`, ~4 arquivos de teste, `pyproject.toml` + `uv.lock`, `stack/THIRD_PARTY_LICENSES.md`, README

## Constitution Check

*GATE: Deve passar antes do Phase 0. Re-checar apos Phase 1.*

| Principio | Status | Notas |
|-----------|--------|-------|
| I. Processamento 100% Local | PASS | conexão só a `127.0.0.1`/`::1`/`localhost`/`postgres` (lista fechada, research D4); nenhum serviço remoto, nenhuma telemetria; nenhum documento sai da máquina (só caminhos e métricas vão ao banco local) |
| II. Licença Permissiva | PASS | asyncpg 0.32.0 `Apache-2.0` (S01, S03); transitiva condicional `async-timeout` `Apache 2` (S04), só em Python < 3.11; registrado em `stack/THIRD_PARTY_LICENSES.md`; psycopg (LGPL) rejeitado |
| III. Mensagens em Português | PASS | todas as exceções da camada e do runner com texto em português (contracts) |
| IV. Testes Antes do Commit | PASS | `uv run pytest` verde sem Docker; integração atrás de `ARTIGO2MD_DB_TESTS=1`, rodada antes do merge (feature toca serviço) |
| V. Veracidade de Dados | PASS | fatos externos com fonte S01–S10 datada; schema, nomes e variáveis novas marcados [PROPOSTA]; itens não lidos viram verificação V-01/V-02 (research) |

**Re-check pós-design (Etapa 7)**: PASS. O design não acrescenta serviço, processo nem porta; a única dependência
nova é o driver decidido pelo operador; gatilhos no banco são defesa em profundidade de regras já exigidas pela
spec (FR-004, FR-006, FR-008, FR-010), não complexidade nova.

## Project Structure

### Documentation (this feature)

```text
docs/specs/job-store/
├── spec.md
├── plan.md              # este arquivo
├── research.md          # decisões D1–D10, fontes S01–S10, registro de licenças
├── data-model.md        # schema [PROPOSTA]
├── quickstart.md        # cenários de teste
└── contracts/
    ├── repository.md    # interface Python da camada, exceções
    └── migrations.md    # runner, arquivos, comportamento
```

### Source Code (repository root)

```text
src/artigo2md/
├── store/                           # NOVO — só a project-api importa (FR-017)
│   ├── __init__.py                  # exporta Store, DbSettings, apply_migrations, erros
│   ├── config.py                    # DbSettings.from_env (reusa infra.parse_env_file)
│   ├── errors.py                    # hierarquia StoreError
│   ├── models.py                    # dataclasses de registro + QualityRecord
│   ├── repository.py                # Store / StoreSession (asyncpg)
│   ├── migrator.py                  # apply_migrations, known_migrations, advisory lock
│   └── migrations/
│       └── 0001_initial.sql         # schema completo (data-model)
├── infra.py                         # inalterado (só importado por store/config.py)
├── cli.py, web.py, worker.py,
│   router.py, engines.py, pdfscan.py, api/app.py   # inalterados; não importam store (FR-021)
tests/
├── test_store_config.py             # NOVO — sem banco: parâmetros, host permitido, senha oculta
├── test_store_contract.py           # NOVO — sem banco: QualityRecord ↔ router.Quality, ENGINES ↔ CHECK do SQL, nomes dos arquivos de migração, checksum
├── test_store_boundary.py           # NOVO — sem banco: fronteira de imports (FR-017, SC-004)
└── test_store_integration.py        # NOVO — ARTIGO2MD_DB_TESTS=1: cenários US1–US5 em banco temporário
pyproject.toml                       # asyncpg no grupo `api` e no extra `dev`
uv.lock                              # regenerado
stack/THIRD_PARTY_LICENSES.md        # linha do asyncpg (feito nesta etapa)
README.md                            # seção curta: testes de integração do banco
```

**Structure Decision**: subpacote `artigo2md/store/` em vez de módulo único — separa configuração (sem driver),
runner de migração e repositório, e permite que o teste de fronteira trate `artigo2md.store` como uma unidade.
`store/migrations.py` foi evitado como nome de módulo para não colidir com o diretório `migrations/`; o runner
fica em `migrator.py`. Os arquivos `.sql` entram no wheel (hatch empacota `src/artigo2md` inteiro,
`pyproject.toml`, `[tool.hatch.build.targets.wheel]`) e na imagem (`COPY src/artigo2md/`).

## Convencoes de Borda

| Camada | Case style | Validacao | Fonte da verdade |
|--------|------------|-----------|------------------|
| Tabelas, colunas, índices, gatilhos (PostgreSQL) | snake_case, inglês | `CHECK`, FKs, índices únicos, gatilhos | `src/artigo2md/store/migrations/*.sql` |
| Valores enumerados (status, kind, mode, event_type) | snake_case, inglês | `CHECK (... IN (...))` + validação prévia na camada | `0001_initial.sql` e constantes em `store/models.py` (teste confere igualdade) |
| Registros Python (dataclasses) | snake_case | tipos + validação de faixa antes do SQL | `src/artigo2md/store/models.py` |
| Métricas de qualidade | mesmos 6 nomes de `router.Quality` | teste de igualdade de campos | `src/artigo2md/router.py:22-29` |
| Variáveis de ambiente | UPPER_SNAKE | `DbSettings.from_env` | `stack/.env.example` + `ARTIGO2MD_POSTGRES_HOST` (nova) |

**Mapper layer (DB ↔ registro)**: `store/repository.py`, funções privadas `_row_to_<entity>`; sem ORM (asyncpg
devolve `Record`; research D1). Não há DTO de API nem payload HTTP nesta feature: a `project-api` define os seus.

## Fluxo principal (US2)

```mermaid
sequenceDiagram
    participant API as project-api (futura)
    participant S as artigo2md.store
    participant PG as Postgres (stack)
    API->>S: create_job(document_id, "auto")
    S->>PG: INSERT ... ON CONFLICT (document_id) WHERE status IN (pending, running) DO NOTHING RETURNING
    PG-->>S: linha ou nada
    S-->>API: Job | ActiveJobExistsError
    API->>S: start_job / record_attempt (n vezes)
    API->>S: complete_job(job_id, markdown_path, engine, quality, message)
    S->>PG: BEGIN; UPDATE jobs (running→succeeded); INSERT job_results; INSERT history_events; COMMIT
    Note over PG: gatilho diferido confere job succeeded com resultado
    S-->>API: (Job, Result, HistoryEvent) | InvalidJobTransitionError
```

## Riscos e mitigação

| Risco | Mitigação |
|-------|-----------|
| Porta 5432 ocupada por outro Postgres local (achado histórico, não autoritativo) | porta vem de `ARTIGO2MD_POSTGRES_PORT`, já configurável na stack; teste de integração só usa o banco temporário |
| Usuário da stack sem permissão de `CREATE DATABASE` | V-02: fixture falha com mensagem em português explicando |
| Lista de `event_type` crescer em features futuras | `CHECK` trocado por migração nova (só avanço) |
| Ambiente local em Python 3.10 traz `async-timeout` | licença permissiva confirmada (S04); V-01 confere a versão do lock |
| `postgres` resolvido por DNS para máquina remota (gate owasp M-01) | resolução prévia; só endereços loopback/privados; conexão no endereço validado, sem nova resolução (research D4) |
| L-04: host de rede local privada respondendo por `postgres` passa no critério "privado", pois `stack/compose.yaml` não declara sub-rede | residual documentado, não bloqueante: lista fechada de hosts e `ARTIGO2MD_POSTGRES_HOST` definido pelo próprio usuário; revisitar se a stack passar a declarar rede nomeada com sub-rede |
| Texto livre (`error`, `error_summary`, `issues`, `message`) pode conter trecho de documento | permanece só no banco local, nunca enviado a fora (Princípio I; spec FR-005/FR-007); exceções lançadas `from None` e sem `detail` do servidor |
| Job órfão em andamento bloqueia novo job do mesmo documento | aceito pelo operador (block-006): sem expiração automática; a `project-api` chama `fail_job`/`cancel_job` e depois `create_job` (contrato) |
| `pgdata` cresce sem limite (retenção indeterminada) | aceito pelo operador (block-006): o usuário exclui projetos quando quiser (FR-011) |
| Avisos de segurança conhecidos do `asyncpg` | aceito pelo operador (block-006): sem acompanhamento além da verificação de licença (FR-018) |
| Teardown de teste apagar o banco real (gate owasp M-02) | guarda de nome `artigo2md_test_<16 hex>` ≠ `POSTGRES_DB` (research D9) |
| Camada conecta com o `POSTGRES_USER` da imagem, sem papel de menor privilégio (gate owasp L-01) | risco aceito pelo operador (block-006): banco local monousuário; papel dedicado pode vir numa migração futura |
| Sem TLS na conexão (gate owasp L-02) | risco aceito pelo operador (block-006): tráfego só em loopback ou na rede interna da stack (Princípio I) |
| Texto sem limite de tamanho em nomes, caminhos e mensagens (gate owasp L-03) | risco aceito pelo operador (block-006): único usuário é o próprio dono dos dados; `limit` da linha do tempo já tem teto de 500 |

## Complexity Tracking

Nenhuma violação de constitution; seção não aplicável.

## Reconciliação com a implementação final (tarefa 8.3.4)

Conferido contra o código em `src/artigo2md/store/` e `src/artigo2md/store/migrations/0001_initial.sql`: as sete
tabelas, os índices, os gatilhos, a árvore de exceções, a assinatura de `Store.open` (pool 1..5; prazos de conexão 10 s,
comando 30 s e aquisição 10 s) e as constantes do runner de migrações (lock com repetição a cada 0,2 s até 60 s)
batem com `data-model.md`, `contracts/repository.md` e `contracts/migrations.md`. **Nenhuma divergência** em relação
às decisões marcadas [PROPOSTA]; elas passam a valer como o desenho implementado.

Observação operacional (não é divergência de desenho): uma imagem `artigo2md-api:local` construída antes da
inclusão do `asyncpg` no grupo `api` não o contém; `sh stack/up.sh` reaproveita a imagem existente, então é preciso
reconstruí-la (`sh stack/up.sh --atualizar-imagens`) para que o driver apareça na imagem.
