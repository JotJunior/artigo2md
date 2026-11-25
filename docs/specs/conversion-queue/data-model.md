# Data Model: Fila de conversão e worker nativo

**Feature**: `conversion-queue` | **Date**: 2026-11-24

Sem banco de dados (FR-008: o worker não acessa o Postgres). As entidades são mensagens, configuração em
memória e arquivos no disco. Campos de mensagem: `contracts/queue-messages.md` (fonte única).

## Entity: Job

Mensagem `type = "job"`. Campos: `schema_version`, `job_id`, `pdf_path`, `engine`, `attempt`.

**Estados (vistos pelo worker):**

```
recebido ──validação──► rejeitado (definitivo) ──► mensagens mortas + evento error(retryable=false) ──► ack
    │
    └─► em conversão ──ok──► Markdown gravado ──► evento result ──► ack
             │
             ├─ falha definitiva ──► mensagens mortas + error(false) ──► ack
             ├─ falha passageira, attempt < max ──► cópia attempt+1 em retry + error(true) ──► ack
             ├─ falha passageira, attempt = max ──► mensagens mortas + error(false) ──► ack
             └─ conexão perdida / encerramento sem concluir ──► sem ack ──► broker reentrega
```

## Entity: Event

Mensagens `progress`, `result`, `error` (contrato). Sempre com `job_id` e `attempt`. Imutáveis; não são
armazenadas pelo worker.

## Entity: DeadLetter

Corpo original do job + cabeçalhos `x-artigo2md-code`, `x-artigo2md-reason`, `x-artigo2md-attempts`,
`x-artigo2md-failed-at`. Vive só na fila `artigo2md.jobs.dead`.

## Entity: WorkerSettings (em memória)

| Campo | Tipo | Origem |
|-------|------|--------|
| `data_dir` | `Path` (resolvido) | `--data-dir` |
| `workers` | int | `--workers` |
| `min_free_memory_mb` | int | `--min-free-memory` |
| `retry_delay` | float | `--retry-delay` |
| `max_attempts` | int | `--max-attempts` |
| `idle_timeout` | float | `--idle-timeout` |
| `shutdown_timeout` | float | `--shutdown-timeout` |
| `host` | str | constante `infra.HOST` |
| `port` | int | `infra.resolve_ports()["rabbitmq"]` |
| `conversion_timeout` | float | `--conversion-timeout` (0 = sem limite) |
| `credentials` | objeto com `user`/`password` e `origin` | ambiente → `ARTIGO2MD_STACK_ENV` → `stack/.env`; `__repr__` mascara a senha; depois de lidas, `RABBITMQ_DEFAULT_USER`/`RABBITMQ_DEFAULT_PASS` são removidas de `os.environ` antes de qualquer filho `spawn` (FR-024) |

## Entity: Slot (pool)

Um por unidade de `workers`. Thread + conexão AMQP + canal (`prefetch_count=1`) + `ConversionWorker`.

| Estado | Consome? | Transições |
|--------|----------|------------|
| `cold` | não (sem `basic_consume`) | memória livre ≥ mínimo → `ready`; senão aviso e nova checagem em 10 s |
| `ready` | sim | entrega recebida → `busy`; conversor descarregado por ociosidade → `cold` (cancela o consumidor) |
| `busy` | não recebe outra (prefetch 1) | término → `ready`; perda de conexão → `reconnecting`; sinal → `draining` |
| `reconnecting` | não | backoff 1 s → 15 s; conectou e declarou topologia → `cold` |
| `draining` | não | job concluído ou prazo esgotado (filho encerrado, sem ack) → `stopped` |

**Gate de RAM (tarefa 1.7, FR-005)**:

- O gate é **único por processo** e serializado por lock. Um slot que passa no gate **reserva** memória (valor de
  `--min-free-memory`) até o conversor terminar de carregar ou falhar; os demais slots frios **descontam** essa
  reserva da memória livre (`available`) ao avaliar. Assim, N slots frios simultâneos com memória para só um
  conversor resultam em um único conversor carregando.
- **Interação com `reconnecting`**: o slot em reconexão **não avalia** o gate; ao reconectar vai a `cold` e aplica
  o gate normalmente. Conversor já vivo não reavalia o gate.
- **Limite por conversão**: estourado `--conversion-timeout`, o filho é encerrado e o job segue como falha
  passageira (`conversion_timeout`).

Na entrega, se o conversor do slot não está vivo e a memória caiu abaixo do mínimo desde a checagem,
o slot faz `basic_nack(requeue=True)` **sem** incrementar `attempt`, cancela o consumidor e volta a `cold`.

## Entity: MarkdownOutput (disco)

`<data_dir>/<job_id>/<stem>.md`, gravado de forma atômica (temporário oculto no mesmo diretório +
`fsync` + `os.replace`). Regravado em reprocessamento. Sem assets nesta feature (FR-022).

## Entity: RamMeasurementReport (repositório)

`docs/specs/conversion-queue/ram-measurement.md`: máquina (modelo, memória total, versão do macOS), lote de
PDFs (nome, páginas, perfil, SHA-256), tamanhos de pool (≥ 3), por processo filho: USS/RSS ocioso,
carregado e pico; memória disponível ao longo do lote; adiamentos do gate de RAM; derivação dos padrões.
