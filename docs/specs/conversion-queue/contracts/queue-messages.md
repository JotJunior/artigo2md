# Contrato de mensagens da fila — versão de esquema 1

**[PROPOSTA — contrato novo desta feature, a validar na implementação]**. Não existe contrato anterior:
nomes de campos, códigos e topologia são decisões de design. Os campos que **repetem tipos já existentes
no código** citam a origem e copiam os nomes exatamente como estão lá.

Todas as mensagens são JSON UTF-8 (`content_type = application/json`), objeto na raiz, mensagens
persistentes (`delivery_mode = 2`). Campos desconhecidos na raiz de um **job** tornam a mensagem inválida
(evita aceitar silenciosamente uma versão futura mal rotulada). Nomes em inglês, `snake_case`; textos
voltados ao usuário (`message`, `reason`) em português.

## Campos comuns

| Campo | Tipo | Obrigatório | Regra |
|-------|------|-------------|-------|
| `schema_version` | integer | sim | versões suportadas pelo worker: `[1]`; outra → rejeição `unsupported_schema_version` |
| `type` | string | sim | `job` \| `progress` \| `result` \| `error` |
| `job_id` | string | sim | `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$` (FR-022) |

Não há tipo de cancelamento (FR-021): ele chega numa versão futura do esquema, e o worker desta versão a
rejeita como versão desconhecida.

## `job` — entrada (exchange `artigo2md.jobs`, routing key `convert`)

| Campo | Tipo | Obrigatório | Regra / origem |
|-------|------|-------------|----------------|
| `schema_version` | integer | sim | `1` |
| `type` | string | sim | `"job"` |
| `job_id` | string | sim | ver campos comuns |
| `pdf_path` | string | sim | caminho **absoluto** de um arquivo no disco do host (FR-007) |
| `engine` | string | não (padrão `"auto"`) | um de `router.ENGINES` = `auto`, `docling`, `pymupdf`, `mineru`, `markitdown` (fonte: `src/artigo2md/router.py:13`) |
| `attempt` | integer | não (padrão `1`) | `>= 1`; incrementado pelo worker a cada retry |

**Limites de entrada** (política **[PROPOSTA]**, tarefa 1.5; nenhum número vem de fonte externa):

- Corpo do job: no máximo **16384 bytes** (16 KiB). Justificativa: o job de exemplo tem cerca de 200 bytes; o
  maior caminho legítimo é `PC_PATH_MAX` = 1024 bytes (lido com `os.pathconf('/', 'PC_PATH_MAX')` no macOS da
  execução, em 2026-11-25), que na pior codificação JSON (`\uXXXX`, 6 bytes por caractere) ocupa 6144 bytes;
  somados `job_id` (até 128) e os demais campos, o pior caso legítimo cabe com folga em 16 KiB.
- Profundidade de aninhamento JSON: no máximo **8** níveis. O job legítimo é um objeto plano (profundidade 1);
  a checagem lê o corpo contando `{`/`[` fora de strings **antes** de `json.loads`, para que JSON aninhado demais
  nunca chegue ao analisador (evita `RecursionError`).
- Corpo acima do limite ou profundidade acima do máximo → `invalid_message` (definitiva). O corpo não é
  analisado. Para a fila de mortas o corpo original é preservado **integralmente**, byte a byte (sem exceção a
  FR-011: o broker já o entregou e a republicação não o interpreta).

**`pdf_path` especial** (`pdf_not_found`, definitiva): o caminho é avaliado com `os.stat` (segue symlink,
**sem abrir** o arquivo). Só é aceito se resolver para arquivo regular (`S_ISREG`). Symlink para arquivo
regular é aceito; symlink pendente (destino inexistente) ou para diretório, FIFO, dispositivo ou socket →
`pdf_not_found`. FIFO e dispositivos nunca são abertos (um `open` de FIFO bloquearia o slot).

Exemplo:

```json
{"schema_version": 1, "type": "job", "job_id": "3f2b9c1e-7a4d-4e1b-9c0a-2d5e6f7a8b9c",
 "pdf_path": "/Users/exemplo/artigos/estudo.pdf", "engine": "auto", "attempt": 1}
```

## Eventos (exchange `artigo2md.events`, `topic`)

**Ordem de entrega** (consumidor): os eventos de um mesmo job **e** da mesma tentativa saem em sequência de um
único slot, cada um publicado com confirmação do broker antes do seguinte: `progress` (zero ou mais) antes de
`result` ou `error`. O consumidor **não** pode presumir ordem entre jobs diferentes nem entre tentativas
(`attempt`) diferentes do mesmo job. Em condições normais há um único evento terminal (`result` ou `error`)
por (`job_id`, `attempt`); a reentrega at-least-once (§Garantias de entrega) pode repeti-lo, e o consumidor
deduplica por (`job_id`, `attempt`).

**Campos desconhecidos**: o consumidor de eventos MUST ignorar campos desconhecidos (ver §Evolução).

Todo evento tem os campos comuns mais `attempt` (integer, a tentativa que o gerou) e `emitted_at`
(string, ISO 8601 UTC com sufixo `Z`). Reentregas geram eventos com o mesmo `job_id` (FR-012); o
consumidor deduplica por `job_id` (+ `attempt`).

### `progress` — routing key `job.progress`

| Campo | Tipo | Regra / origem |
|-------|------|----------------|
| `message` | string | texto em português vindo do callback de progresso do roteador (`"Inspecionando o PDF"`, `"Convertendo com {name}"`, ...; fonte: `src/artigo2md/router.py`) ou do `ConversionWorker` (`"Carregando o conversor"`; fonte: `src/artigo2md/worker.py`) |

### `result` — routing key `job.result`

| Campo | Tipo | Regra / origem |
|-------|------|----------------|
| `markdown_path` | string | caminho absoluto `<data_dir>/<job_id>/<stem>.md` gravado (FR-022) |
| `engine` | string | `ConversionResult.engine` (fonte: `router.py`) |
| `report` | object | exatamente `ConversionResult.report()` (fonte: `router.py`): `source` (string), `engine` (string), `analysis` (object = `Analysis.to_dict()`: `pages`, `text_pages`, `scanned_pages`, `total_chars`, `table_hints`, `formula_score`, `article_score` — integers; `fully_scanned` — boolean; fonte: `pdfscan.py`), `attempts` (array de `Attempt`: `engine` string, `seconds` number, `quality` object\|null, `error` string\|null; `quality` = `Quality`: `score` number, `accepted` boolean, `tables_found` integer, `tables_expected` integer, `chars_per_page` number, `issues` array de string em português; fonte: `router.py`) |

O Markdown em si **não** trafega na fila; só o caminho.

### `error` — routing key `job.error`

| Campo | Tipo | Regra |
|-------|------|-------|
| `code` | string | um dos códigos abaixo |
| `reason` | string | motivo em português; para PDF inválido é a mensagem de `InvalidPdfError` (fonte: `pdfscan.py`) |
| `retryable` | boolean | `true` = haverá nova tentativa após o atraso; `false` = definitivo (mensagem vai/foi para `artigo2md.jobs.dead`) |
| `max_attempts` | integer | valor configurado no worker |

Mensagens sem `job_id` válido não geram evento (não há a quem atribuí-lo): vão só para mensagens mortas e
para o log.

## Códigos de erro

| `code` | Classe | Quando |
|--------|--------|--------|
| `invalid_message` | definitiva | corpo não é JSON, não é objeto, falta campo, tipo errado, campo extra no job |
| `unsupported_schema_version` | definitiva | `schema_version` ≠ suportadas; `reason` cita a recebida e as suportadas |
| `invalid_engine` | definitiva | `engine` fora de `router.ENGINES`; `reason` lista os válidos |
| `invalid_path` | definitiva | `job_id`/nome derivado escaparia de `data_dir` ou nome inválido/longo demais |
| `pdf_not_found` | definitiva | `pdf_path` relativo, inexistente ou não é arquivo regular |
| `invalid_pdf` | definitiva | `InvalidPdfError` (senha, formato, ilegível) |
| `worker_crashed` | passageira | processo de conversão morreu (possível falta de memória) |
| `conversion_failed` | passageira | todos os motores falharam (`RuntimeError` do roteador) ou outra exceção |
| `write_failed` | passageira | erro de E/S temporário ao gravar (disco cheio, permissão) |
| `conversion_timeout` | passageira | a conversão excedeu `--conversion-timeout` (o filho foi encerrado; mesmo tratamento de retry) |

Passageira na última tentativa (`attempt == max_attempts`) → `retryable=false` e vai para mensagens mortas.

## Mensagens mortas (exchange `artigo2md.dead`, routing key `dead` → fila `artigo2md.jobs.dead`)

Corpo = **bytes originais** do job recebido (FR-011), mesmas `content_type`; cabeçalhos AMQP:

| Cabeçalho | Tipo | Conteúdo |
|-----------|------|----------|
| `x-artigo2md-code` | string | código da tabela acima |
| `x-artigo2md-reason` | string | motivo em português |
| `x-artigo2md-attempts` | integer | tentativas feitas |
| `x-artigo2md-failed-at` | string | ISO 8601 UTC |

Reenvio manual: republicar o corpo em `artigo2md.jobs` com routing key `convert` (ajustando `attempt`
se desejado).

## Garantias de entrega

1. Consumo com `prefetch_count = 1` por slot; `ack` só depois de (a) Markdown gravado de forma atômica e
   evento `result` confirmado pelo broker, ou (b) evento `error` definitivo + cópia em mensagens mortas
   confirmados, ou (c) cópia de retry confirmada em `artigo2md.retry` + evento `error` passageiro.
2. Eventos e cópias são publicados com confirmação do broker e `mandatory`; se não confirmar, o job não
   recebe `ack` e será reentregue (FR-003).
3. Queda de conexão antes do `ack` → o broker devolve o job à fila (at-least-once, FR-012).
4. **Janela de `ack` perdido após a cópia de retry** (decisão da tarefa 1.2): se a cópia `attempt+1` já foi
   confirmada em `artigo2md.retry` e a conexão cai antes do `ack`, o original volta com o `attempt` antigo e
   existe também a cópia `attempt+1`. Isto é **aceito como limitação do at-least-once**, com estes limites:
   (a) o worker só publica cópia de retry se `attempt < max_attempts`, logo **nenhuma mensagem tem `attempt`
   maior que `max_attempts`** e cada cadeia termina em `result` ou nas mensagens mortas; (b) a duplicação
   cria no máximo uma cadeia extra por `ack` perdido, não uma explosão; (c) a saída é idempotente (Markdown
   regravado no mesmo destino, FR-012). O consumidor dos eventos deduplica por (`job_id`, `attempt`) e trata o
   primeiro `result` como final. Alternativas rejeitadas: deduplicação com estado persistente no worker (viola
   FR-012, "sem estado próprio") e descartar entregas com `redelivered=true` (poderia perder o job se a cópia
   nunca tiver sido publicada, violando "nenhum job se perde").

## Evolução

Mudança incompatível → `schema_version` 2. O worker rejeita versões que não suporta sem executar; uma
versão nova do worker pode suportar `[1, 2]` simultaneamente.

**O que é compatível** (decisão da tarefa 1.1):

- Acrescentar campo **opcional** a um **evento** (`progress`, `result`, `error`) é mudança **compatível**:
  mantém a mesma `schema_version`, e os consumidores de eventos MUST ignorar campos desconhecidos.
- O **job** continua rejeitando campos extras (§Campos comuns): campo novo no job exige `schema_version` nova.
- Remover ou renomear um campo, mudar seu tipo ou tornar obrigatório um campo opcional é incompatível e exige
  `schema_version` nova, em qualquer tipo de mensagem.
