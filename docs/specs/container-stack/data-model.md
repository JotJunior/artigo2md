# Data Model: Infraestrutura local em containers

Esta feature não cria tabelas nem filas (o schema é da `job-store`, a topologia é da `conversion-queue`). As entidades abaixo são de configuração e de estado em tempo de execução. Nomes de campo/variável em inglês; valores padrão são **[PROPOSTA]** (research, Decision 9).

## Entity: Stack

| Campo | Tipo | Origem | Notas |
|---|---|---|---|
| project_name | string | `stack/compose.yaml` (`name`) | fixo `artigo2md` |
| services | list[Service] | `stack/compose.yaml` | exatamente `postgres`, `rabbitmq`, `api` (FR-001) |
| volumes | list[string] | `stack/compose.yaml` | `pgdata`, `rabbitmqdata` (FR-003); nomes reais no Docker: `<projeto>_pgdata` e `<projeto>_rabbitmqdata` (observado: `artigo2md_pgdata`/`artigo2md_rabbitmqdata` com o `name:` do arquivo; research, Observações da integração real, item 6) |

**State transitions**:

```text
derrubada --up.sh--> subindo --todos healthy--> no ar (saudável)
subindo --timeout 120 s / serviço unhealthy--> falhou (exit 5; containers ficam para inspeção de logs)
no ar --up.sh (publicação atual do painel igual ao modo pedido)--> no ar ("já estava no ar", exit 0, `stack/up.sh:200-213`)
no ar --up.sh (publicação do painel diferente do modo pedido)--> subindo (o `up` é chamado e recria só o RabbitMQ; ver `docs/specs/desktop-stack-control/plan.md`, "Resultado de 11.4")
no ar --down.sh--> derrubada (volumes mantidos)
no ar|derrubada --down.sh --apagar-dados + confirmação--> derrubada sem volumes
```

## Entity: Service

| Campo | Tipo | Valores | Notas |
|---|---|---|---|
| name | string | `postgres` \| `rabbitmq` \| `api` | |
| image | string | `postgres:18-trixie` \| `rabbitmq:4.4-management` \| build local `stack/api/Dockerfile` | research Decisions 1–3 |
| container_ports | list[int] | postgres `5432`; rabbitmq `5672` (e `15672` só com o opt-in `--management-panel`, via `stack/compose.management.yaml`); api `8000` | portas dos serviços: research S01b, S07; 8000 é [PROPOSTA] |
| host_binding | string | sempre `127.0.0.1` | literal no compose, não parametrizável (FR-006) |
| host_port_var | string | ver `Parameters` | |
| healthcheck | string | research Decision 4 | FR-005 |
| volume | string? | `pgdata:/var/lib/postgresql` \| `rabbitmqdata:/var/lib/rabbitmq` \| nenhum | |
| health | enum | `starting` \| `healthy` \| `unhealthy` | estado reportado pelo Docker |

## Entity: Parameters (`stack/.env`)

Ver tabela completa em `contracts/stack-scripts.md`. Regras de validação no `up.sh`:

- credenciais (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`): não vazias; senhas ≠ `troque-esta-senha` e com 12+ caracteres (FR-004);
- recusados: chave duplicada, valor com `$`, `POSTGRES_HOST_AUTH_METHOD` presente; `export `, espaços e aspas envolventes são normalizados (research D11.1);
- portas (`ARTIGO2MD_*_PORT`): inteiro 1–65535; `ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT` só é validada com `up.sh --management-panel` (`stack/up.sh:129-131`; `contracts/stack-scripts.md`, passo 3);
- leitura linha a linha `CHAVE=valor`, ignorando comentários e linhas vazias; nada é executado (`eval`/`source` proibidos).

## Entity: LicenseRecord (`stack/THIRD_PARTY_LICENSES.md`)

| Campo | Tipo | Notas |
|---|---|---|
| component | string | serviço, runtime ou SO base |
| image | string | imagem em que aparece |
| license | string | como escrita na fonte oficial |
| principle_ii | enum | `permissiva` \| `copyleft fraco sem modificação` \| `documentado (SO base, dec-013)` \| `rejeitado` |
| source_url | string | URL oficial |
| checked_at | date | data da consulta |

Regra: nenhuma linha com `license` vazia ou "presumida" (SC-006); GPL/AGPL obrigatório → `rejeitado` + bloqueio humano (FR-011). Conteúdo inicial = tabela "Registro de licenças" do `research.md`.

## Entity: InfraReport (saída do `doctor`)

| Campo | Tipo | Notas |
|---|---|---|
| service | string | `postgres` \| `rabbitmq` \| `api` |
| host | string | `127.0.0.1` |
| port | int \| null | resolvida como em research D11.2; `null` quando `state = invalid_port` |
| state | enum | `reachable` \| `unreachable` \| `protocol_mismatch` \| `invalid_port` |
| message | string | texto em português exibido (`contracts/doctor-infra.md`) |

O `InfraReport` não altera o exit code do `doctor` (FR-009).
