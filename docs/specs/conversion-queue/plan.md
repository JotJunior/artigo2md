# Implementation Plan: Fila de conversão e worker nativo

**Feature**: `conversion-queue` | **Date**: 2026-11-24 | **Spec**: [spec.md](./spec.md)

## Summary

Novo subcomando `artigo2md worker`, nativo no macOS, que consome jobs do RabbitMQ local
(`127.0.0.1`, stack da `container-stack`), converte cada PDF reaproveitando `pdfscan` + `router.convert`
dentro do processo auxiliar já existente (`worker.ConversionWorker`, `spawn` + descarga por ociosidade),
grava o Markdown de forma atômica em `<data_dir>/<job_id>/<stem>.md` e publica eventos de progresso,
resultado e erro. Pool de N slots (thread + conexão AMQP com `prefetch_count=1` + conversor próprio),
gate de RAM com `psutil`, retry com atraso por expiração de mensagem + dead-letter de volta à fila de jobs,
fila de mensagens mortas com corpo original e motivo, contrato JSON versionado (`schema_version: 1`)
derivado dos tipos reais do roteador. Detalhes e fontes: [research.md](./research.md).

## Pendências que bloqueiam o fechamento do plan

Nenhuma. A antiga P1 (licença do `pika`) foi resolvida pelo operador em resposta ao bloqueio humano
`block-001`: **BSD-3-Clause**, conforme os metadados oficiais do PyPI (`https://pypi.org/pypi/pika/json`,
campo `license`, versão 1.5.0, consultado pelo operador em 2026-11-24) e o arquivo LICENSE do repositório
oficial (`https://github.com/pika/pika`). Detalhes e restrição de versão: research Decision 1.

Itens **[A VERIFICAR]** do research (API do `pika`, semântica de TTL/DLX do RabbitMQ 4.4, vhost padrão)
não bloqueiam o plan: viram critério de aceite das tasks (leitura da fonte oficial na adoção e testes
`ARTIGO2MD_STACK=1` que comprovam o comportamento no broker real).

## Premissas e riscos aceitos (revisão do operador)
Itens `{humano}` dos checklists. Não geram tarefa de implementação: valem como **premissa ou risco aceito** até o operador discordar. Recusar qualquer linha abre uma tarefa nova (coluna "Se recusada"). Os itens seguem `[ ]` nos checklists até a revisão do operador (tarefa 1.9).

| ID | Item (checklist) | Premissa / risco aceito | Se recusada |
|----|------------------|--------------------------|-------------|
| P-01 | api CHK007 | O contrato v1 e os nomes de exchanges/filas (marcados PROPOSTA) ainda não foram aceitos pelos consumidores futuros (`project-api`, app desktop). Aceito como v1; mudança incompatível posterior vira `schema_version` 2. Risco: retrabalho se os consumidores pedirem outros nomes. | Antes de `execute-task`, validar o contrato com os donos das features futuras e revisar `contracts/queue-messages.md`. |
| P-02 | security CHK004 | Quem tem as credenciais do broker (loopback `127.0.0.1`, usuário único) é confiável: pode pedir ao worker que leia qualquer PDF absoluto legível pelo usuário e grave o Markdown no diretório de dados. Sem allowlist de pastas de PDF. | Acrescentar opção de linha de comando com pastas permitidas para `pdf_path` e código de erro novo. |
| P-03 | security CHK012 | Além das credenciais (proibidas), caminhos de PDF e trechos de mensagens de exceção de bibliotecas de terceiros podem aparecer em `reason`, em eventos e em logs. Aceito por ser máquina local de um só usuário. | Definir filtragem/truncamento de `reason` e dos logs. |
| P-04 | security CHK013 | Os Markdown gravados e as mensagens mortas (que guardam o corpo original) herdam a `umask` do usuário; nenhuma permissão especial é imposta. | Definir modo de arquivo/diretório (por exemplo, só o dono) em `output.py` e na documentação. |
| P-05 | security CHK014 e performance CHK015 | A fila `artigo2md.events.all` (sem consumidor até a `project-api`) e a fila de mortas não têm limite nem retenção; podem crescer no disco do broker. A limpeza é manual (painel de gestão, que exige subir a stack com `sh stack/up.sh --management-panel`: o painel não é publicado por padrão). | Definir `x-max-length`/TTL por fila na topologia (muda os argumentos declarados e exige nova decisão de compatibilidade). |
| P-06 | security CHK016 | O `pika==1.5.0` foi avaliado só quanto à licença (BSD-3-Clause, informada pelo operador no `block-001`), não quanto a vulnerabilidades conhecidas. Risco aceito até revisão pelo operador. | Consultar o histórico de advisories oficial do projeto e registrar o resultado antes de `execute-task` 2.1. |
| P-07 | performance CHK002 | Não há meta de vazão ou latência por PDF; o único alvo mensurável é "no máximo N conversões simultâneas" (SC-001). | Definir meta (por exemplo, lote de 10 PDFs) e acrescentar à medição da tarefa 7.2. |
| P-08 | performance CHK011 | Os PDFs da medição de RAM são sintéticos ou versionáveis, de origem conhecida; nenhum PDF de usuário é usado nem versionado. O relatório registra nome, páginas, perfil e SHA-256. | Operador indica a origem dos PDFs de medição (e se podem ser versionados). |

> Fonte e acompanhamento: `tasks.md` (seção de mesmo nome); os itens seguem `[ ]` nos checklists até a revisão do operador.

## Technical Context

**Language/Version**: Python `>=3.10` (fonte: `pyproject.toml`, `requires-python`)
**Primary Dependencies**: existentes — `pypdfium2`, `docling`, `markitdown` (motores via `router`); novas,
no extra opcional `queue`: `pika==1.5.0` (BSD-3-Clause, research L15/L16) e `psutil` (BSD-3-Clause, research L12)
**Storage**: arquivos — Markdown em `<data_dir>/<job_id>/<stem>.md`; sem banco (FR-008)
**Testing**: `pytest` (`uv run pytest`), canal AMQP falso para a suíte sem Docker; integração real atrás de
`ARTIGO2MD_STACK=1` (fonte: `tests/test_stack_integration.py:36-38`)
**Target Platform**: macOS nativo (MPS/ocrmac; fonte: briefing §Plataforma e §Workers, spec FR-001); broker em Docker local (`stack/compose.yaml`)
**Project Type**: CLI + processo de longa duração (worker)
**Performance Goals**: retomar o consumo ≤ 1 min após o broker voltar (SC-007); conversões simultâneas ≤ N
**Constraints**: só loopback; nenhum Docling/pymupdf4llm no processo principal; `convert`/`doctor`/`serve`
sem carregar a fila; padrões de RAM/workers só a partir da medição (FR-015; tarefa 7.3 de `tasks.md`, `research.md` Decision 7 e `ram-measurement.md`: `--workers` 4, `--min-free-memory` 4822 MB)
**Scale/Scope**: um usuário, uma máquina, lotes de dezenas a centenas de PDFs

## Constitution Check

*GATE: Deve passar antes do Phase 0. Re-checado após Phase 1.*

| Princípio | Status | Notas |
|-----------|--------|-------|
| I. Processamento 100% Local | PASS | conexão só com `infra.HOST` (`127.0.0.1`), host não parametrizável; sem telemetria; PDF lido do disco, não trafega na fila |
| II. Licença Permissiva | PASS | `psutil` BSD-3-Clause (L12); `pika` 1.5.0 BSD-3-Clause (L15/L16, fonte oficial informada pelo operador no `block-001`); versão fixada no extra para casar com a versão verificada; nada GPL/AGPL introduzido; extra opcional não muda o enquadramento (dependência só para o worker) |
| III. Mensagens em Português | PASS | todas as mensagens do worker e `reason`/`message` dos eventos em português; campos/opções em inglês (`contracts/`) |
| IV. Testes Antes do Commit | PASS | suíte sem Docker cobre contrato, topologia, retry, DLQ, idempotência, RAM, encerramento; integração atrás de `ARTIGO2MD_STACK=1` |
| V. Veracidade de Dados | PASS | contrato derivado dos tipos reais (`router.py`, `pdfscan.py`) e marcado PROPOSTA; nenhuma licença presumida; nenhum número de RAM/workers inventado; afirmações sobre software de terceiros não conferidas marcadas [A VERIFICAR] |

Re-check pós-design: sem novo serviço, sem nova camada além do módulo `jobqueue`; o pool reaproveita
`ConversionWorker` (nenhuma duplicação do pipeline). Nenhum item aberto (P1 resolvida, `block-001`).

## Arquitetura

```
artigo2md worker (processo principal: sem Docling/pymupdf4llm)
 ├─ main thread: argparse → WorkerSettings → sinais (SIGINT/SIGTERM) → supervisiona slots
 ├─ slot 1 (thread): conexão AMQP + canal(prefetch=1, confirms) + ConversionWorker ─► filho spawn (router.convert)
 ├─ slot 2 ...
 └─ slot N ...
RabbitMQ 127.0.0.1:<porta>:
  artigo2md.jobs ─convert─► artigo2md.jobs.convert ─► slots
  artigo2md.retry ─convert─► artigo2md.jobs.retry ─(expiração, DLX)─► artigo2md.jobs
  artigo2md.dead ─► artigo2md.jobs.dead
  artigo2md.events (topic) ─job.#─► artigo2md.events.all ─► (futura project-api)
```

Fluxo de um job no slot: validar (processo principal) → gate de RAM se o conversor está frio → converter
(`ConversionWorker.convert(..., tick=heartbeat)`, progresso vira evento) → gravar atômico → publicar
`result` (confirm) → `ack`. Falhas: research Decision 4 e `contracts/queue-messages.md`.

Mudanças em código existente (mínimas, compatíveis com a interface web):

- `worker.py`: filho envia `("invalid_pdf", msg)` para `InvalidPdfError`; pai levanta
  `ConversionError(kind=...)`/`WorkerCrashed` (subclasses de `RuntimeError`, mesma mensagem atual);
  parâmetro opcional `tick` em `convert` (usa `conn.poll`); método `kill()` que encerra o filho sem
  esperar o lock (encerramento por prazo, FR-013).
- `cli.py`: subparser `worker` com import local de `artigo2md.jobqueue` (como `doctor` faz com `infra`).
- `pyproject.toml`: extra `queue` com `pika==1.5.0` (a versão cuja licença foi verificada; upgrade exige
  nova verificação de licença) e `psutil`.
- `stack/THIRD_PARTY_LICENSES.md`: nova seção "Dependências Python do worker nativo (extra `queue`)"
  com linhas no formato validado por `tests/test_stack_files.py` (6 colunas, fonte oficial, data).

## Project Structure

### Documentation (this feature)

```
docs/specs/conversion-queue/
├── spec.md
├── plan.md                 # este arquivo
├── research.md             # Phase 0
├── data-model.md           # Phase 1
├── quickstart.md           # Phase 1 (cenários de teste)
├── contracts/
│   ├── queue-messages.md   # contrato de mensagens v1 + topologia + códigos de erro
│   └── worker-cli.md       # opções, códigos de saída e mensagens do comando
└── ram-measurement.md      # gerado na implementação (FR-015), com números reais
```

### Source Code (repository root)

Existente (verificado): `src/artigo2md/{cli,engines,infra,pdfscan,router,web,worker}.py`, `tests/`,
`stack/`. Novo:

```
src/artigo2md/
├── worker.py               # ALTERADO (tick, kill, classificação de erro)
├── cli.py                  # ALTERADO (subcomando worker, import local)
└── jobqueue/               # NOVO — só importado por `artigo2md worker`
    ├── __init__.py
    ├── contract.py         # validação e construção das mensagens v1 (stdlib + router.ENGINES)
    ├── output.py           # validação de job_id/nome, destino confinado, gravação atômica
    ├── settings.py         # WorkerSettings, porta/credenciais via infra, máscara de senha
    ├── memory.py           # gate de RAM (psutil.virtual_memory().available)
    ├── topology.py         # nomes e declaração idempotente; erro de incompatibilidade em português
    ├── slot.py             # máquina de estados do slot (cold/ready/busy/reconnecting/draining)
    └── service.py          # pool, sinais, backoff de reconexão, encerramento sem órfãos
tools/
└── measure_worker_ram.py   # NOVO — medição manual de FR-015 (fora do pacote)
tests/
├── fake_amqp.py            # NOVO — broker/canal falso em memória (confirms, prefetch, nack, cancel)
├── test_jobqueue_contract.py
├── test_jobqueue_output.py
├── test_jobqueue_topology.py
├── test_jobqueue_slot.py   # retry, DLQ, idempotência, gate de RAM, concorrência
├── test_jobqueue_service.py# sinais, prazo, órfãos, backoff, credenciais nunca impressas
├── test_worker.py          # NOVO — tick/kill/classificação no ConversionWorker
├── test_cli.py             # ALTERADO — worker: opções obrigatórias; convert/doctor sem jobqueue/pika
└── test_jobqueue_integration.py  # ARTIGO2MD_STACK=1 (broker real)
```

**Structure Decision**: subpacote `jobqueue` (não `queue`, para não confundir com o módulo `queue` da
biblioteca padrão), importado só sob demanda pelo CLI — mantém FR-017 testável por `sys.modules`.

## Convenções de Borda

| Camada | Case style | Validação | Fonte da verdade |
|--------|------------|-----------|------------------|
| Mensagens AMQP (job/eventos) | `snake_case` (chaves JSON) | `jobqueue/contract.py` no consumo e antes de publicar (worker) | `contracts/queue-messages.md` |
| Campos copiados do roteador (`report`, `analysis`, `attempts`, `quality`) | `snake_case` (como nos dataclasses) | `dataclasses.asdict` — sem renomear | `src/artigo2md/router.py`, `src/artigo2md/pdfscan.py` |
| Cabeçalhos AMQP de mensagens mortas | `x-artigo2md-kebab-case` | testes de slot | `contracts/queue-messages.md` |
| Nomes de exchange/fila | `artigo2md.<ponto>` | `jobqueue/topology.py` (constantes únicas) | `contracts/queue-messages.md` |
| Opções de CLI | `--kebab-case` | `argparse` | `contracts/worker-cli.md` |

**Mapper layer**: nenhum — o evento `result` embute `ConversionResult.report()` sem transformação; a
`project-api` futura consome o mesmo shape. **Roundtrip**: quickstart cenário 1 valida eventos reais do
broker contra o contrato.

## Plano de testes (FR-019)

Sem Docker (`uv run pytest`): contrato (exemplos válidos/inválidos, versão desconhecida, motor inválido,
campos extras); output (travessia: `..`, `/`, `\`, absoluto, NUL, nome oculto, symlink plantado, nome
longo; atomicidade com falha simulada no meio); topologia (declara os 8 objetos com os argumentos do
research; idempotente; incompatibilidade → mensagem); slot (prefetch, ordem gravar→publicar→ack, retry com
`attempt+1`, DLQ com corpo original e cabeçalhos, esgotamento, confirm negado → sem ack, gate de RAM,
nack sem incremento quando a memória cai); service (SIGTERM com/sem prazo, 0 filhos vivos, backoff 1→15 s,
credenciais ausentes/recusadas sem valor no log); worker.py (tick chamado durante espera, `invalid_pdf`,
`kill`, web inalterada); CLI (opções obrigatórias, extra ausente → código 3; `convert`/`doctor` sem
`pika`/`artigo2md.jobqueue` em `sys.modules`).

Com `ARTIGO2MD_STACK=1`: quickstart cenários 1, 3, 8, 9, 11 contra o broker real — inclusive a
confirmação dos itens [A VERIFICAR] (expiração + DLX, vhost `/`, erro de pré-condição em topologia
divergente).

## Complexity Tracking

Sem violações a justificar.
