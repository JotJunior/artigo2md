# Quickstart: Infraestrutura local em containers

Cenários que validam a implementação ponta a ponta. Os cenários com Docker correspondem a `tests/test_stack_integration.py` (só com `ARTIGO2MD_STACK=1`); os demais rodam em `uv run pytest` sem Docker.

## Scenario 1: Subir do zero (happy path — US1/US4)

1. `cp stack/.env.example stack/.env` e trocar as duas senhas `troque-esta-senha`
2. `sh stack/up.sh`
3. **Expected**: exit 0 em até 2 min (SC-001); três linhas `saudável`; linha `Painel do RabbitMQ: não publicado (use --management-panel)` e nenhuma URL do painel. Com `sh stack/up.sh --management-panel`: linha com `http://127.0.0.1:15672/` (opt-in, block-004/dec-023/dec-025)
4. `curl -s http://127.0.0.1:8778/health`
5. **Expected**: `{"status":"ok","service":"artigo2md-api","version":"0.1.0"}`; `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8778/docs` → `404`

## Scenario 2: Roundtrip real do contrato de saúde

1. Com a stack no ar, `GET /health` real (sem mock) pelo teste de integração
2. **Expected**: chaves exatamente `status`, `service`, `version`, todas `string`, iguais a `contracts/api-health.md`

## Scenario 3: Idempotência e persistência (US1 cenários 2–4, SC-004)

1. Gravar uma linha no Postgres (`docker compose -p artigo2md exec postgres psql …`) e declarar uma fila no RabbitMQ
2. `sh stack/up.sh` de novo → **Expected**: "já estava no ar", exit 0, mesmos IDs de container
3. `sh stack/down.sh` → **Expected**: exit 0; `docker volume ls` ainda lista os volumes `pgdata` e `rabbitmqdata` do projeto (o nome exato com prefixo do projeto é conferido na implementação)
4. Repetir subir/derrubar 3 vezes → **Expected**: linha e fila continuam lá

## Scenario 4: Só loopback (US2, SC-002)

1. `docker compose -p artigo2md port postgres 5432` (e equivalentes para 5672 e 8000)
2. **Expected**: todas começam com `127.0.0.1:`
3. `docker compose -p artigo2md port rabbitmq 15672` sem o opt-in → **Expected**: sem endereço publicado (a porta não é publicada); depois de `sh stack/up.sh --management-panel` → começa com `127.0.0.1:`
4. Teste estático (sem Docker): toda entrada `ports` de `stack/compose.yaml` e de `stack/compose.management.yaml` começa com `"127.0.0.1:`

## Scenario 5: Erros de pré-condição (sem Docker)

1. Sem `stack/.env`: `sh stack/up.sh` → **Expected**: exit 2, mensagem com `cp stack/.env.example stack/.env`, nenhum comando `docker` executado (verificado com `docker` falso no `PATH`)
2. `.env` com `POSTGRES_PASSWORD=troque-esta-senha` (também entre aspas, com `export ` na frente ou duplicado), senha curta ou com `$` → **Expected**: exit 2 citando `POSTGRES_PASSWORD`, sem imprimir o valor
3. `docker` falso que falha em `info` → **Expected**: exit 3, "Docker não está disponível"

## Scenario 6: Porta ocupada (FR-013)

1. Abrir um servidor em `127.0.0.1:5432` fora do projeto (ou usar um Postgres existente)
2. `sh stack/up.sh` → **Expected**: exit 4, cita a porta 5432 e `ARTIGO2MD_POSTGRES_PORT`; o servidor externo continua no ar

## Scenario 7: `doctor` e `convert` sem a stack (US3, SC-005)

1. Stack derrubada: `time uv run artigo2md doctor` → **Expected**: seção "Infraestrutura" com os três `inacessível` e a instrução de subir; exit code igual ao de antes; < 5 s
2. Stack parcial (só `postgres` no ar): **Expected**: `postgres acessível`, os outros `inacessível`
3. `uv run artigo2md convert <um-pdf.pdf>` sem Docker (nos testes: o `fake_convert` de `tests/test_cli.py`) → **Expected**: mesmo Markdown de antes, nenhuma menção à infraestrutura (teste: `socket.create_connection` monitorado não é chamado para as portas da stack)

## Scenario 8: Sem saída para a internet (US2 cenários 2–3, SC-003)

1. Imagens já baixadas (inclusive `artigo2md-api:local`); desligar a rede da máquina; `sh stack/up.sh` → **Expected**: sobe saudável (`--pull missing` + `--no-build`: nada é baixado nem reconstruído)
2. Com rede, observar 10 min de operação ociosa (ex.: `nettop`/`lsof -i` no host, ou captura na interface da rede do compose) → **Expected**: zero conexões de saída dos containers. Verificação manual documentada (não automatizada no pytest)
