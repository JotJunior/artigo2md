# Contract: fluxo ao vivo por projeto (SSE)

> **Contrato implementado e verificado** (`src/artigo2md/api/live.py`, `routes/events.py`, emissões em
> `routes/jobs.py`, `routes/projects.py` e `consumer.py`). Os valores de `status` vêm de `models.JOB_STATUSES`;
> `attempt` e `message` repetem os campos do evento da fila
> (`docs/specs/conversion-queue/contracts/queue-messages.md`, `progress` e `error`). O teto de 32 conexões, a fila de
> 1000 eventos e o keep-alive de 15 s foram aceitos pelo operador (block-001/dec-023).

## GET /v1/projects/{project_id}/events

**Resposta**: 200, `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Projeto inexistente → 404 JSON
(corpo de erro de [http-api.md](http-api.md)) antes de abrir o fluxo. Mesmas regras de `Host` e `Origin` das
demais rotas (`EventSource` de origem não autorizada → 403). Projeto **arquivado** existe: o fluxo abre. Acima de 32 conexões simultâneas → 503 `too_many_streams` (corpo JSON, antes de abrir o fluxo).

Cada evento SSE tem `event:` e uma linha `data:` com JSON numa linha só. Comentário `: keep-alive` a cada 15 s.

| `event` | `data` | Quando |
|---------|--------|--------|
| `progress` | `{"job_id": int, "document_id": int, "attempt": int, "message": str, "emitted_at": str}` | evento `progress` da fila aceito para um job não final do projeto |
| `job_state` | `{"job_id": int, "document_id": int, "attempt": int\|null, "status": str, "message": str}` | mudança gravada: `running`, `succeeded`, `failed`, `cancelled`, e também `pending` (job criado pela API) |
| `warning` | `{"job_id": int, "document_id": int, "attempt": int, "message": str, "emitted_at": str}` | evento `error` com `retryable=true` (falha passageira; o job segue `running`) |

- `emitted_at` é copiado do evento da fila (ISO 8601 UTC com `Z`, `queue-messages.md`); permite medir a latência (SC-004).
- `attempt` é a tentativa da fila; em `job_state` vindo de ação da API (criação, cancelamento) é `null`.
- `message` em português: o texto do worker (`progress.message`, `error.reason`) ou o texto da mensagem gravada no
  histórico pela camada `store`.
- Só eventos de jobs do `project_id` do caminho (FR-019).
- Emissões da API, sempre **depois** da gravação: `job_state pending` na criação do job (`Job criado.`), `job_state failed` quando a publicação na fila não é confirmada e `job_state cancelled` no cancelamento (`Job cancelado pelo usuário.`). Emissões do consumidor: `job_state running` quando um evento da fila move o job de `pending` para `running`, `progress`, `warning` e o estado final `succeeded`/`failed`. Evento descartado (duplicata, job final, fora do contrato, projeto arquivado) não gera evento ao vivo.
- **Arquivar** projeto com jobs não finais: um `job_state` com `status=cancelled` por job cancelado (mesma operação do `archive`); o fluxo **continua aberto** (o projeto existe e pode ser reativado).
- **Excluir** projeto com jobs não finais: um `job_state` com `status=cancelled` por job cancelado e, em seguida, o servidor **encerra** os fluxos desse projeto; ao reconectar, o cliente recebe 404 `not_found` e sabe que o projeto não existe mais. Eventos tardios dos workers para esses jobs são descartados (FR-016) e não geram evento.
- Ordem: para um mesmo job, na ordem em que a API os tratou; nenhuma garantia entre jobs diferentes.
- Sem `id:`/`Last-Event-ID`: ao reconectar o cliente relê `GET /v1/projects/{project_id}/documents` (FR-020).
- Cliente que não consome (fila interna de 1000 eventos cheia) tem a conexão encerrada; os demais clientes não são
  afetados (FR-020).
- Os eventos são **sinais de interface**: a fonte de verdade é o banco (rotas de consulta).
