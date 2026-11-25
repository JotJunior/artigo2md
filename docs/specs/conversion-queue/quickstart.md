# Quickstart / Cenários de teste: Fila de conversão e worker nativo

**Feature**: `conversion-queue` | **Date**: 2026-11-24

Pré-requisitos comuns: `uv sync --extra dev --extra queue`. Cenários marcados **[stack]** exigem
`sh stack/up.sh` e rodam na suíte só com `ARTIGO2MD_STACK=1` (FR-019, mesma variável de
`tests/test_stack_integration.py`). Os demais rodam em `uv run pytest` sem Docker, com um canal AMQP falso.

**Padrões finais do worker** (opções completas e códigos de saída em `contracts/worker-cli.md`):
`--workers 4`, `--min-free-memory 4822` (ambos derivados de [`ram-measurement.md`](ram-measurement.md), medidos
com um lote sintético de 24 páginas, motor `auto`, num único Mac de 64 GiB; máquinas com menos RAM devem
ajustá-los), `--retry-delay 30`, `--max-attempts 3`, `--idle-timeout 300`, `--shutdown-timeout 30`,
`--conversion-timeout 0`. Os cenários abaixo fixam valores menores (`--workers 2`, `--min-free-memory 0`,
`--retry-delay 1`, `--shutdown-timeout 1`) só para o teste ser rápido e independente da RAM da máquina.
Execução comum, com os padrões: `uv run artigo2md worker --data-dir <pasta>`.

## Cenário 1 — Happy path [stack]

1. Subir a stack; criar `/tmp/a2m-dados`.
2. `uv run artigo2md worker --data-dir /tmp/a2m-dados --workers 2 --min-free-memory 0`
3. Publicar em `artigo2md.jobs` (routing key `convert`) um job válido com `pdf_path` absoluto de um PDF de teste.
4. Consumir `artigo2md.events.all`.

**Expected**: `/tmp/a2m-dados/<job_id>/<stem>.md` existe; eventos `progress` (português) e um `result` com
o mesmo `job_id`, `markdown_path` e `report` no formato de `ConversionResult.report()`; todos passam no
validador do contrato; job sumiu de `artigo2md.jobs.convert`.

## Cenário 2 — Concorrência limitada [sem Docker]

1. Pool de N=2 slots com conversor falso que dorme e registra início/fim.
2. Enfileirar 5 jobs no broker falso.

**Expected**: máximo de 2 conversões simultâneas; nunca mais de 1 entrega sem ack por slot.

## Cenário 3 — Falha passageira com retry [sem Docker + stack]

1. Conversor falso levanta `WorkerCrashed` na 1ª tentativa e converte na 2ª; `--retry-delay 1 --max-attempts 3`.

**Expected**: evento `error` com `code=worker_crashed`, `retryable=true`; cópia com `attempt=2` publicada em
`artigo2md.retry`; ack do original só após o confirm. [stack]: a mensagem reaparece em
`artigo2md.jobs.convert` só após ≥ 1 s e termina com `result` (confirma a semântica de expiração + DLX).

## Cenário 4 — Falha definitiva vai direto para mensagens mortas [sem Docker]

Para cada caso: PDF inexistente; PDF protegido por senha/inválido (`InvalidPdfError`); `engine` inválido;
corpo que não é JSON; `schema_version: 2`; `job_id` `"../x"`; PDF com nome `.md` oculto.

**Expected**: 0 tentativas de conversão nos casos validáveis no processo principal; uma publicação em
`artigo2md.dead` com o corpo original byte a byte e cabeçalhos de motivo/código/tentativas; evento `error`
`retryable=false` (exceto sem `job_id` válido); nada gravado fora de `data_dir`; ack do original.

## Cenário 5 — Esgotamento de tentativas [sem Docker]

Conversor falso sempre falha passageiramente, `--max-attempts 3`.

**Expected**: tentativas 1 e 2 → retry; tentativa 3 → mensagens mortas, `error` com `retryable=false`.

## Cenário 5b — `ack` perdido após a cópia de retry [sem Docker]

Conversor falso falha passageiramente na tentativa 1; o canal falso confirma a cópia em `artigo2md.retry` e
**perde a conexão antes do `ack`** do original.

**Expected**: o original é reentregue com `attempt=1` e existe a cópia `attempt=2`; nenhuma mensagem tem
`attempt > max_attempts`; cada cadeia termina em `result` ou mensagens mortas; o consumidor reconhece a repetição
por (`job_id`, `attempt`) (queue-messages §Garantias de entrega, item 4).

## Cenário 6 — Idempotência [sem Docker]

Processar o mesmo job duas vezes (conteúdos diferentes).

**Expected**: um único arquivo no destino, com o conteúdo da 2ª execução; nenhum temporário restante;
dois `result` com o mesmo `job_id`.

## Cenário 7 — Gate de RAM [sem Docker]

`psutil.virtual_memory` simulado abaixo de `--min-free-memory`.

**Expected**: slot fica `cold`, sem `basic_consume`; aviso em português; 0 conversores iniciados (SC-005);
ao simular memória suficiente, passa a consumir.

**Caso 7b — N slots frios simultâneos com memória para só um**: N = 3 slots frios, memória livre simulada
suficiente para exatamente um conversor (`min_free_memory` ≤ livre < 2 × `min_free_memory`).
**Expected**: só um slot passa no gate (reserva compartilhada, serializada por lock); os outros dois ficam
`cold` com aviso; ao a carga do primeiro conversor terminar ou falhar, a reserva é liberada e os demais
reavaliam. **Caso 7c — slot em `reconnecting`**: memória baixa durante a reconexão; o slot não avalia o gate e,
ao reconectar, vai a `cold` e aplica o gate normalmente.

## Cenário 8 — Encerramento [sem Docker + stack]

1. Job em conversão (conversor falso lento); enviar SIGTERM com `--shutdown-timeout 1`.

**Expected**: consumidores cancelados; prazo esgotado → filho encerrado, job sem ack (volta à fila); 0
processos filhos vivos após a saída (SC-008); código de saída 0.

## Cenário 9 — Broker cai e volta [stack]

Com o worker ocioso, `docker compose ... restart rabbitmq` (via scripts da stack).

**Expected**: aviso em português durante a queda, sem traceback e sem credencial; consumo retomado em até
1 min após o broker voltar (SC-007).

## Cenário 9b — Ociosidade sem espera ativa [sem Docker + stack]

Worker com fila vazia. [sem Docker]: canal falso conta as iterações do laço do slot num intervalo fixo; cada
iteração bloqueia em `process_data_events(time_limit>0)`, sem laço sem espera. [stack]: CPU do processo do
worker ocioso, medida com `psutil` numa janela fixa, próxima de zero.

**Expected**: contagem de iterações limitada por `intervalo / time_limit`; CPU do worker ocioso próxima de zero.

## Cenário 10 — `convert` intocado [sem Docker]

Em subprocesso: `artigo2md convert` num PDF de teste e inspeção de `sys.modules`.

**Expected**: `pika` e `artigo2md.jobqueue` ausentes de `sys.modules` (`psutil` não entra na checagem:
já é carregado transitivamente pelos motores, via `accelerate` no `uv.lock`); saída
igual à atual (FR-017, SC-009). Mesmo teste para `doctor`.

## Cenário 11 — Isolamento de rede [stack]

Durante o cenário 1, listar as conexões do processo (`lsof -a -i -p <pid>`).

**Expected**: só `127.0.0.1:<porta do RabbitMQ>` (SC-010).

## Roundtrip End-to-End

O cenário 1 é o roundtrip real: eventos capturados do broker real (não de mocks) são validados contra o
contrato — expõe divergência entre o que o worker publica e o documento.
