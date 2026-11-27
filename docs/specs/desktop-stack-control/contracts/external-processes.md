# Contracts: processos e serviços existentes usados pelo app

Contratos **existentes** consumidos por esta feature. Nada aqui é proposto: cada linha cita a fonte.
O app nunca analisa o texto em português dos scripts; usa só códigos de saída e saídas estruturadas.

## stack/up.sh (sem opções)

Invocação: `/bin/sh <repo>/stack/up.sh`, `current_dir` = `<repo>` (o script resolve o próprio
diretório por `$0`, `stack/up.sh:18`, `STACK_DIR=`). Lê `<repo>/stack/.env` (`stack/up.sh:19`, `ENV_FILE=`).

| Exit | Significado (fonte `stack/up.sh:13-14`, comentário "Códigos de saída") | Mensagem do app (pt-BR, resumo) |
|------|------------------------------------------|---------------------------------|
| 0 | ok (inclui "já estava no ar", `up.sh:200-213`, bloco `ALL_RUNNING`) | "Infraestrutura no ar" |
| 1 | falha no `cd` do script (`up.sh:18`, `|| exit 1`) | "Não foi possível abrir a pasta da stack" |
| 2 | parâmetros inválidos | o app valida o `.env` antes com as regras de `up.sh:49-154` (passos 1 a 3b: `.env`, credenciais, portas e `ARTIGO2MD_DATA_DIR`) e nomeia a variável pela própria checagem; se o script ainda sair com 2, mensagem genérica com detalhes técnicos sob demanda |
| 3 | Docker indisponível | "Docker indisponível: inicie o runtime" |
| 4 | porta ocupada | "A porta <N> do <serviço> está em uso por outro programa" |
| 5 | serviço não saudável no prazo (`--wait-timeout 120`, `up.sh:261`) | "O <serviço> não ficou saudável; veja os detalhes técnicos" |

Mudança decidida nesta feature (`plan.md`, seção "Painel de gestão do RabbitMQ fora da publicação
padrão", dec-025), já implementada no `up.sh`: sem opções, ele não publica a porta 15672 e não valida nem testa como
ocupada a `ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT` (`up.sh:129-131` e `236-239`, só com `[ "$MANAGEMENT_PANEL" = 1 ]`);
isso vale só com `--management-panel` (opção em `up.sh:44`), que o app nunca usa.
Os códigos de saída acima não mudam. A validação prévia do `.env` pelo app segue a regra nova.

## stack/down.sh (sem opções)

| Exit | Significado (fonte `stack/down.sh:14,76,82`) |
|------|----------------------------------------------|
| 0 | ok |
| 1 | exclusão não confirmada ou `compose down` falhou (sem opções, só o segundo caso) |
| 2 | opções inválidas (não deve ocorrer: o app não passa opções; se ocorrer, tratamento genérico `scriptFailed`, com a saída técnica sob demanda) |
| 3 | Docker indisponível |
| qualquer outro | código não documentado: `scriptFailed` (mesmo tratamento genérico) |

Mapeamento completo de saída para código de erro do app: `contracts/tauri-commands.md`, "Error Responses".

## Docker CLI

Todos com `--context <em uso>` e sem `DOCKER_HOST`/`DOCKER_CONTEXT` no ambiente (research, Decision 2).
Compose sempre com `--project-directory <repo>/stack --env-file <repo>/stack/.env -f
<repo>/stack/compose.yaml` (`stack/up.sh:31-37`, função `dc`).

| Comando | Uso | Fonte |
|---------|-----|-------|
| `docker context ls --format '{{json .}}'` | contextos e o corrente (`Current`, `Name`, `DockerEndpoint`) | observado, 2026-11-26 |
| `docker --context <c> info --format '{{json .}}'` | daemon acessível (exit 0) ou não (exit 1) | observado, 2026-11-27 (Docker Desktop, contexto `desktop-linux`): `docker --context desktop-linux info --format '{{.ServerVersion}}'` → exit 0; `DOCKER_HOST=unix:///nonexistent.sock docker info` → exit 1 e `failed to connect to the docker API at unix:///nonexistent.sock; check if the path is correct and if the daemon is running: dial unix /nonexistent.sock: connect: no such file or directory` (a mesma mensagem foi registrada em `research.md`, Decision 2, 2026-11-26) |
| `docker --context <c> compose version` | plugin do Compose presente | observado, 2026-11-27: `docker --context desktop-linux compose version` → `Docker Compose version v5.6.1`, exit 0 (e em 2026-11-26, `research.md`, Decision 2); `stack/up.sh:161` (`docker compose version`) |
| `compose … config --images` | lista de imagens da stack | observado, 2026-11-27: `docker --context desktop-linux compose --project-directory stack --env-file <env de teste> -f stack/compose.yaml config --images` → `rabbitmq:4.4-management`, `artigo2md-api:local`, `postgres:18-trixie` (as mesmas três imagens em `research.md`, Decision 8, 2026-11-26) |
| `docker image inspect <img>` | imagem presente | `stack/up.sh:251` (`docker image inspect "$API_IMAGE"`, bloco `PULL`/`BUILD` em 248-257) |
| `compose … --progress json pull --ignore-buildable` | etapa imagens | `--progress` em `docker compose --help` e `--ignore-buildable` em `pull --help` (Compose v5.6.1; `research.md`, Decision 8, 2026-11-26); a saída de um `pull` real foi observada em 2026-11-27 com `alpine:3.20` (abaixo) |
| `compose … --progress json build api` | imagem local da API | `stack/compose.yaml:49-52`; a opção global `--progress` existe (`docker compose --help`, Compose v5.6.1, `research.md`, Decision 8, 2026-11-26); o `build api` com `--progress json` **não foi executado nem teve a saída capturada** nesta feature: formato das linhas de `build` não verificado |
| `compose … ps --status running --services` | serviços em execução | `stack/up.sh:165` (`RUNNING=$(dc ps --status running --services`) |
| `compose … ps --all --format '{{.Service}} {{.State}} {{.Health}}'` | serviço com problema após exit 5 | `stack/up.sh:267` (`dc ps --all --format`, dentro do laço de 265-272) |
| `/usr/sbin/lsof -nP -iTCP:<porta> -sTCP:LISTEN -Fpcn` | dono do socket da porta do RabbitMQ (M2), só leitura | observado, 2026-11-27 (Docker Desktop) |

Linha JSON do `--progress json` observada: `{"id":"Image postgres:18-trixie","status":"Working","text":"Pulling"}`;
erro: `{"error":true,"message":"..."}`.

Observado em 2026-11-27 (Docker Compose v5.6.1, `pull` de `alpine:3.20` num projeto descartável; as linhas saem em
**stderr**): a linha de uma camada tem `id` (id da camada), `parent_id` (`Image <nome>`), `status`
(`Working`/`Done`), `text` (`Pulling fs layer`, `Downloading`, `Download complete`, `Extracting`, `Pull complete`) e
`details` (texto de tamanho). Só as linhas `Downloading` trazem bytes: `"current":2097152,"total":4092319,"percent":51`;
a linha `Extracting` traz `current` mas não é download; camadas que concluem rápido aparecem só como
`Download complete` (`percent":100`, sem `total`). Conclusão: os bytes são **parciais, por camada**; o total geral não
é conhecido. O app soma os `current` das linhas `Downloading` (mínimo observado) e não calcula percentual geral.
Fixture: `desktop/src-tauri/src/stack/fixtures/compose-pull-progress.jsonl`. Não observado: a cadência das linhas
`Downloading` em camadas de centenas de MB (as imagens da stack não foram baixadas na verificação).

`docker compose … port rabbitmq 15672` (observado em 2026-11-27, Compose v5.6.1, num projeto Compose de teste; `plan.md`, "Resultado de 11.4"): porta publicada = exit 0 e `127.0.0.1:<porta>`;
não publicada = exit 1, stdout vazio e stderr `no port 15672/tcp for container <nome>: 5672/tcp`.

`lsof -nP -iTCP:<porta> -sTCP:LISTEN -Fpcn` (observado, macOS, Docker Desktop): linhas `p<pid>`, `c<comando>`, `f<fd>`,
`n127.0.0.1:<porta>`; o dono da porta publicada observada é `com.docker.backend` (saída capturada de uma porta em `socket_owner.rs:127`; as portas 5672 e 5432 não constam dessa captura). Usado só para a checagem M2 (somente leitura).

## artigo2md worker

Invocação: `<uv> run --no-sync --project <repo> artigo2md worker --data-dir <dir> --workers <N>
--min-free-memory <MB> --shutdown-timeout 30` (`src/artigo2md/cli.py:251-274`, `sub.add_parser("worker"`;
`docs/specs/conversion-queue/contracts/worker-cli.md:11-20`).
Ambiente: `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`, `ARTIGO2MD_RABBITMQ_PORT`
(`src/artigo2md/jobqueue/settings.py:18-20,89-132`) e `HF_HUB_OFFLINE=1` (com ele, `engines.py`
recusa baixar modelos e levanta `EngineUnavailable`, `src/artigo2md/engines.py:134-194`). Log em stderr, português (`jobqueue/slot.py:76-77`).

| Exit | Significado (`jobqueue/settings.py:40-45`; `worker-cli.md:40-49`) | `WorkerExitReason` |
|------|-----------------------------------------------------------------------|--------------------|
| 0 | encerrado por sinal (ok) | `stopped` |
| 1 | falha (inclui todos os conversores parados, `service.py:97-98`) | `failure` |
| 2 | uso inválido | `usage` |
| 3 | extra `queue` ausente (pika ou psutil) | `queueExtraMissing` |
| 4 | credenciais ausentes ou recusadas | `credentials` |
| 5 | topologia incompatível | `topology` |

RabbitMQ fora do ar não encerra o worker: ele registra e tenta de novo (`worker-cli.md:51`; backoff
1–15 s, `service.py:5-6`). Sinais: SIGINT e SIGTERM pedem encerramento calmo; prazo
`--shutdown-timeout` (padrão 30 s) e mais 10 s de junção (`service.py:24,60-65,111-125`).

## uv run

| Comportamento | Fonte |
|---------------|-------|
| sem `--no-sync`, sincroniza o ambiente antes de rodar | https://docs.astral.sh/uv/concepts/projects/run/ |
| `--no-sync` evita sincronizar e implica `--frozen` | https://docs.astral.sh/uv/reference/cli/#uv-run; `uv run --help` (uv 0.13.9) |
| repassa a maioria dos sinais ao filho no Unix (exceto SIGKILL, SIGCHLD, SIGIO, SIGPOLL); SIGINT único do mesmo grupo não é repassado | https://docs.astral.sh/uv/concepts/projects/run/, "Signal handling" |
| devolve o código de saída do filho | observado, 2026-11-27 (uv 0.13.9): `uv run --no-sync python -c 'import sys; sys.exit(4)'` → exit 4; `uv run --no-sync python <script com handler de SIGTERM que chama sys.exit(0)>` seguido de `kill -TERM` no processo `uv` → exit 0 (também em `research.md`, Decision 12, 2026-11-26) |

## Sondagens de saúde (loopback, sem credenciais)

| Serviço | Sondagem | Fonte |
|---------|----------|-------|
| Postgres | SSLRequest `!II (8, 80877103)`; resposta `S`, `N` ou `E` | `src/artigo2md/infra.py:38,165-177` |
| RabbitMQ | `AMQP\x00\x00\x09\x01`; quadro tipo 1 no canal 0 | `infra.py:40,180-193` |
| API | `GET /health` → 200, `{"status":"ok",...}` | `infra.py:196-210`; `src/artigo2md/api/app.py:75-78` |
| API (consumidor) | `GET /v1/status` → `{"consumer":{"connected":bool,"last_event_at":str\|null},"publisher":{"connected":bool}}` | `src/artigo2md/api/routes/status.py:17-26`; `docs/specs/project-api/contracts/http-api.md:170-176` |

Tempos: 1 s para conectar e 1 s para ler (`infra.py:22-26`). Endereço sempre `127.0.0.1`.
