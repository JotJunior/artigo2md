# Contract: API HTTP da versão desktop (project-api)

> **Contrato implementado e verificado** (rotas em `src/artigo2md/api/routes/`, erros em `src/artigo2md/api/errors.py`,
> origem/CORS/corpo em `src/artigo2md/api/security.py`). Os **campos** dos registros repetem os dataclasses de
> `src/artigo2md/store/models.py` com os mesmos nomes e tipos (coluna "Origem"); nada além deles é afirmado como
> existente. `tests/test_api_contract_coverage.py` confere que as rotas registradas na aplicação são exatamente as
> documentadas aqui e em [live-events.md](live-events.md) e que os campos serializados batem com a tabela
> "Representações". Os limites numéricos (16 MiB, 10 000, 512 MiB, 32 assinantes, 2000 caracteres) foram aceitos pelo
> operador (block-001/dec-023). `GET /health` é o contrato já publicado em
> [`container-stack/contracts/api-health.md`](../../container-stack/contracts/api-health.md) e não muda.

## Regras gerais

| Tema | Regra |
|------|-------|
| Base | `http://127.0.0.1:${ARTIGO2MD_API_PORT}` no host (padrão `8778`, `src/artigo2md/infra.py:32`); `Host` só `127.0.0.1` ou `localhost` (400 caso contrário, como na API base; corpo de erro JSON em português) |
| Versionamento | rotas de negócio sob o prefixo `/v1`; mudança incompatível = prefixo `/v2` convivendo com `/v1`; campo novo **opcional** em resposta é compatível (o cliente MUST ignorar campos desconhecidos). `/health` fica sem prefixo |
| Formato | JSON UTF-8, `snake_case`, nomes em inglês; textos para o usuário (`message`) em português |
| Datas | ISO 8601 com fuso (`datetime.isoformat()` dos `timestamptz` do banco) |
| Ids | inteiros (`bigint` do banco, `models.py`); o `job_id` da fila é `str(id)` |
| Corpo | pedido `POST`/`PUT`/`PATCH`/`DELETE` que traz corpo exige `Content-Type: application/json` (415); rotas sem corpo (`archive`, `cancel`, `DELETE`, o fluxo ao vivo) não exigem tipo; `Content-Length` repetido ou não numérico → 400 `invalid_request`; corpo acima de 16 MiB → 413 `payload_too_large`, decidido pelo `Content-Length` sem ler o corpo ou, sem ele, contando os bytes à medida que chegam |
| Origem | pedido com `Origin` fora da lista efetiva (fixa: `tauri://localhost`; mais `http://localhost:1420` só com `ARTIGO2MD_API_ALLOW_DEV_ORIGIN` explícita; não há variável que troque a lista) (inclui `null`) ou com mais de um cabeçalho `Origin` → 403 antes de qualquer efeito; sem `Origin` → aceito (cliente nativo). A checagem vale para todo `/v1`; `/health` fica fora dela |
| CORS | métodos permitidos: `GET`, `POST`, `PATCH`, `DELETE`, `OPTIONS`; cabeçalhos de pedido permitidos: `Content-Type` (único necessário para `application/json`; `Accept` é seguro por padrão); cabeçalho exposto: `Content-Disposition` (nome do zip de exportação); `allow_credentials=False`; preflight (`OPTIONS` com `Access-Control-Request-Method`) de origem autorizada → 200 sem tocar banco nem fila, de origem não autorizada → 403 `origin_not_allowed`; preflight com método ou cabeçalho fora da lista → 400 `invalid_request` em português; o CORS vale só para `/v1` (`OPTIONS /health` nunca é 2xx) |
| Documentação automática | desligada (`/docs`, `/redoc`, `/openapi.json` → 404), como na API base |

### Corpo de erro (todas as rotas `/v1`)

```json
{"error": {"code": "project_name_taken", "message": "Já existe um projeto ativo com esse nome."}}
```

| Status | `code` | Quando |
|--------|--------|--------|
| 400 | `invalid_request` | **formato**: JSON malformado, campo obrigatório ausente, campo desconhecido, tipo errado, `limit`/`offset` fora da faixa, id de caminho não positivo, `Content-Length` inválido, ou combinação inválida de campos (inclui `kind = "file"` com `documents` diferente de `[path]`, dec-032; substitui o 422 padrão do FastAPI; mensagem em português que cita o campo, sem eco do valor). Qualquer outro erro 4xx do Starlette sem código próprio também vira `invalid_request` |
| 403 | `origin_not_allowed` | `Origin` não autorizada |
| 403 | `path_not_allowed` | caminho gravado recusado pela regra confinada ao diretório de dados (FR-025) |
| 404 | `not_found` | projeto, fonte (inclusive de outro projeto), documento ou job inexistente (inclui `document_ids` que não existem ou pertencem a outro projeto) e rota inexistente |
| 404 | `no_result` | documento sem resultado (`GET .../markdown`, `GET .../result`) |
| 409 | `project_name_taken` | `ProjectNameTakenError` |
| 409 | `active_job_exists` | `ActiveJobExistsError` |
| 409 | `invalid_transition` | `InvalidJobTransitionError` (cancelar job final) ou `StoreValidationError` de estado (arquivar já arquivado) |
| 409 | `export_in_progress` | outra exportação em andamento (uma por vez) |
| 413 | `payload_too_large` | corpo > 16 MiB, mais de 10 000 caminhos num pedido (fontes e documentos), mais de 10 000 `document_ids` num pedido de jobs, ou exportação acima de 512 MiB de Markdown |
| 503 | `too_many_streams` | mais de 32 conexões SSE simultâneas |
| 415 | `unsupported_media_type` | corpo sem `application/json` |
| 422 | `validation_failed` | **domínio**: campo com o tipo certo e valor fora do domínio (`kind`, `mode` e `engine` fora das listas, nome vazio ou longo demais, caminho de fonte `copied` fora da pasta permitida) ou `StoreValidationError`; a mensagem lista os valores válidos quando o domínio é uma enumeração (`kind`: `folder`, `file`; `mode`: `in_place`, `copied`; `engine`: `auto`, `docling`, `pymupdf`, `mineru`, `markitdown`) |
| 503 | `database_unavailable` | `DatabaseUnavailableError` ou banco ainda não migrado; mensagem indica `sh stack/up.sh` |
| 503 | `broker_unavailable` | publicação impossível (só em rota que publica e não pôde nem criar o job) |
| 503 | `data_dir_permission_denied` | o usuário do container não consegue ler ou remover arquivo/diretório gravado pelo worker no diretório de dados (`PermissionError`); mensagem em português, sem root e sem `chmod` automático (decisão do operador, block-001/dec-023) |
| 503 | `data_dir_unavailable` | `ARTIGO2MD_DATA_DIR_HOST`/`_MOUNT` ausentes ou inválidos (rotas que leem/apagam arquivos) |
| 500 | `internal_error` | qualquer outra exceção; mensagem fixa em português, sem detalhe interno |

**Fora da tabela**: `405 method_not_allowed` — método HTTP não permitido numa rota existente. Não faz parte da tabela
padrão de códigos (`ERRORS` em `errors.py`, igualada à tabela acima por `tests/test_api_errors.py`); o corpo é
`{"error": {"code": "method_not_allowed", "message": "Método HTTP não permitido para esta rota."}}` e o cabeçalho
`Allow` do Starlette é preservado.

Mensagens nunca contêm senha, credencial, texto bruto do driver nem pilha (FR-031); as exceções do `store` já
trazem texto próprio em português (`repository.md`, "Hierarquia de exceções").

## Representações

| Nome | Campos (origem `models.py`) |
|------|-----------------------------|
| `Project` | `id` int, `name` str, `status` str (`active`\|`archived`), `created_at`, `updated_at` |
| `Source` | `id`, `project_id`, `kind` (`folder`\|`file`), `mode` (`in_place`\|`copied`), `path` str, `created_at` |
| `DocumentStatus` | `document_id`, `project_id`, `source_id`, `path`, `status` (`not_processed`\|`pending`\|`running`\|`succeeded`\|`failed`\|`cancelled`), `latest_job_id` int\|null, `latest_job_requested_at`\|null, `last_result` `ResultSummary`\|null |
| `ResultSummary` | `job_id`, `markdown_path`, `engine`, `score` number, `accepted` bool, `created_at` |
| `Job` | `id`, `project_id`, `document_id`, `requested_engine`, `status`, `requested_at`, `started_at`\|null, `finished_at`\|null, `error_summary` str\|null |
| `Quality` | `score` number, `accepted` bool, `tables_found` int, `tables_expected` int, `chars_per_page` number, `issues` list[str] (`QualityRecord`) |
| `Attempt` | `id`, `job_id`, `position` int, `engine`, `seconds` number, `quality` `Quality`\|null, `error` str\|null, `created_at` |
| `Result` | `job_id`, `markdown_path`, `engine`, `quality` `Quality`, `warnings` list[str] (= `quality.issues`, propriedade `Result.warnings`), `created_at` |
| `HistoryEvent` | `id`, `project_id`, `document_id`\|null, `job_id`\|null, `event_type` str, `occurred_at`, `message` str |

`published_at` (extensão, [store-extensions.md](store-extensions.md)) **não** é exposto.

## Projetos (US1)

| Método e rota | Corpo | Sucesso | Erros |
|---------------|-------|---------|-------|
| `GET /v1/projects?include_archived=false` | — | 200 `{"projects": [Project]}` por `(created_at, id)` crescente (`list_projects`) | 503 |
| `POST /v1/projects` | `{"name": str}` | 201 `Project` | 400, 409 `project_name_taken`, 422 |
| `GET /v1/projects/{project_id}` | — | 200 `Project` | 404 |
| `PATCH /v1/projects/{project_id}` | `{"name": str}` | 200 `Project` | 404, 409, 422 |
| `POST /v1/projects/{project_id}/archive` | — | 200 `{"project": Project, "cancelled_job_ids": [int]}` | 404, 409 |
| `POST /v1/projects/{project_id}/unarchive` | — | 200 `Project` | 404, 409 |
| `DELETE /v1/projects/{project_id}` | — | 204 | 404, 503 `data_dir_unavailable`, 503 `data_dir_permission_denied`, 503 `database_unavailable` |

**Unicidade do nome**: entre projetos **ativos**, pela chave `name_key` = NFC + `casefold` + NFC
(`repository.name_key`, `store/migrations/0001_initial.sql`, índice `projects_active_name_key`). Nomes que diferem só
por caixa ou por equivalência Unicode do `casefold` (por exemplo `Á`/`á`, `ß`/`ss`) são o mesmo nome; uma letra com
acento **distinta** da sem acento **não** colide (`a` e `á` são nomes diferentes). Espaços (de qualquer tipo Unicode)
nas pontas são removidos antes; nome vazio depois disso → 422 `validation_failed`. `PATCH` com a mesma chave do
próprio projeto só atualiza a grafia; chave em uso por **outro** projeto ativo → 409 `project_name_taken`. Arquivar
libera o nome; desarquivar com o nome em uso → 409.

`DELETE`: numa transação, lê `list_project_job_ids` e depois `delete_project`. Após o `COMMIT`, remove só caminhos
**derivados pela API**, nunca caminhos informados pelo cliente: `<mount>/<job_id>/` para cada job (o `job_id` é
inteiro) e `<mount>/sources/<project_id>/` inteiro. Antes de remover, `os.lstat` confere que o alvo é diretório real
(não link simbólico) sob o `realpath` do ponto de montagem; a remoção usa `shutil.rmtree`, que recusa link simbólico
na raiz e não segue links internos. Antes do `COMMIT`, na mesma transação, o plano de remoção exige a pasta de dados configurada e existente: sem ela → 503 `data_dir_unavailable` e o banco **não** é tocado (nem os jobs são cancelados); e uma verificação de permissão (`os.access`, só leitura do estado, sem `chmod`) dos diretórios derivados recusa a exclusão com 503 `data_dir_permission_denied` se o usuário do container não puder removê-los (nada é alterado no banco). O `DELETE` de um projeto com jobs não finais cancela esses jobs na mesma transação e emite os eventos de [live-events.md](live-events.md) só depois do `COMMIT`. Falha ao remover depois do `COMMIT` é registrada em log e não desfaz a exclusão (o banco já não
aponta para os arquivos). Os diretórios `<mount>/<job_id>/` de jobs substituídos são mantidos e só removidos aqui, junto com o projeto (block-001/dec-023). Nada fora do diretório de dados é tocado.

## Fontes e documentos (US1)

| Método e rota | Corpo | Sucesso | Erros |
|---------------|-------|---------|-------|
| `GET /v1/projects/{project_id}/sources` | — | 200 `{"sources": [Source]}` | 404 |
| `POST /v1/projects/{project_id}/sources` | `{"kind": "folder"\|"file", "mode": "in_place"\|"copied", "path": str, "documents": [str]}` (`documents` opcional, padrão `[]`) | 201 `{"source": Source, "documents": DocumentOutcomes}` | 400, 404, 413, 422, 503 `data_dir_unavailable` (`mode = "copied"` sem pasta de dados) |
| `POST /v1/projects/{project_id}/sources/{source_id}/documents` | `{"documents": [str]}` | 200 `DocumentOutcomes` | 400, 404 (inclui fonte de outro projeto), 413, 422, 503 `data_dir_unavailable` (fonte `copied` sem pasta de dados) |
| `GET /v1/projects/{project_id}/documents` | — | 200 `{"documents": [DocumentStatus]}` por `document_id` crescente (`ORDER BY d.id` em `list_documents`) | 404 |
| `GET /v1/documents/{document_id}` | — | 200 `DocumentStatus` | 404 |

`DocumentOutcomes` = `{"created": [Document], "existing": [Document], "rejected": [{"path": str, "message": str}]}`,
com `Document` = `id`, `project_id`, `source_id`, `path`, `created_at` (`models.Document`).

Regras de caminho (FR-008, FR-006, FR-025):

- cada caminho: não vazio, absoluto, sem `\0`, terminando em `.pdf` (sem diferenciar maiúsculas), até 1024 bytes
  em UTF-8 (`PC_PATH_MAX` citado em `queue-messages.md`, "Limites de entrada"); inválido → item em `rejected`
  com mensagem que cita o caminho; os válidos são gravados (US1-8);
- `kind = "file"`: `documents` deve conter exatamente `[path]` — lista vazia, com outro caminho ou com mais de um item → **400 `invalid_request`** e nada é gravado (dec-032; vale também em `POST .../sources/{source_id}/documents` quando a fonte é `file`, comparando com o `path` da fonte). Como a fonte `file` **é** o documento, o seu `path` também segue as regras de documento (`.pdf`, etc.); inválido → 422 e nada é gravado; `kind = "folder"`: `path` é a pasta e
  `documents` os PDFs que o app listou (a API não lê o disco);
- `mode = "copied"`: `path` e cada documento MUST estar sob `${ARTIGO2MD_DATA_DIR_HOST}/sources/<project_id>/`
  (fronteira de componente, sem `..`); fora disso → 422 para a fonte e `rejected` para o documento. O app copia
  os arquivos para essa pasta **antes** de chamar a rota (decisão Q1 do clarify);
- a lista é idempotente por (projeto, caminho) (`add_document`): repetidos vão para
  `existing` e mantêm documento, fonte original e histórico (US1-7).

## Jobs (US2, US5)

| Método e rota | Corpo | Sucesso | Erros |
|---------------|-------|---------|-------|
| `POST /v1/documents/{document_id}/jobs` | `{"engine": str}` (padrão `"auto"`; o corpo inteiro é opcional) | 202 `Job` | 404, 409 `active_job_exists`, 422 (motor fora da lista: mensagem lista `auto, docling, pymupdf, mineru, markitdown`, `models.REQUESTED_ENGINES`), 503 `broker_unavailable` com o `Job` já falho em `job` |
| `POST /v1/projects/{project_id}/jobs` | `{"engine": str, "document_ids": [int]}` **ou** `{"engine": str, "not_processed": true}` | 202 `{"enqueued": [Job], "skipped": [{"document_id": int, "message": str}], "failed": [Job]}` | 400, 404, 413, 422 |
| `GET /v1/jobs/{job_id}` | — | 200 `Job` | 404 |
| `POST /v1/jobs/{job_id}/cancel` | — | 200 `Job` | 404, 409 `invalid_transition` |
| `GET /v1/jobs/{job_id}/attempts` | — | 200 `{"attempts": [Attempt]}` por `position` crescente (`list_attempts`) | 404 |
| `GET /v1/jobs/{job_id}/result` | — | 200 `Result` | 404 `not_found`/`no_result` |

- `POST /v1/projects/{project_id}/jobs`: exatamente um entre `document_ids` (lista não vazia de inteiros) e `not_processed` (`true`); os dois juntos, nenhum dos dois, `document_ids` vazio ou `not_processed` diferente de `true` → 400 `invalid_request`, sem criar job; qualquer id de `document_ids` inexistente ou de **outro projeto** → 404 `not_found` para o pedido inteiro, **antes** de criar qualquer job (nenhuma criação parcial); mais de 10 000 ids → 413 `payload_too_large`. `skipped` vale só para documento com job ativo (FR-012).
- O mesmo `POST /v1/documents/{document_id}/jobs` serve para converter, tentar de novo e reprocessar com outro motor
  (US5): sempre cria um job novo com `attempt = 1` na mensagem; jobs e resultados anteriores ficam.
- Publicação: ver [research.md](../research.md), Decision 8. `failed` no lote = job criado e falho por publicação
  não confirmada (`error_summary` em português); o documento pode ser enfileirado de novo.
- `cancel` vale para `pending` e `running` (inclusive job órfão sem worker vivo, FR-018); grava `job_cancelled`
  e eventos tardios desse job são descartados (FR-016).

## Resultado, Markdown, histórico e exportação (US4)

| Método e rota | Sucesso | Erros |
|---------------|---------|-------|
| `GET /v1/documents/{document_id}/result` | 200 `{"result": Result, "attempts": [Attempt]}` do job de `last_result` | 404 `not_found`/`no_result` |
| `GET /v1/documents/{document_id}/markdown` | 200 `text/markdown; charset=utf-8`, bytes exatos do arquivo do `last_result` | 404 `no_result` (sem acessar o disco); 404 `not_found` se o arquivo sumiu; 403 `path_not_allowed` se a regra confinada recusar; 503 `data_dir_unavailable` |
| `GET /v1/projects/{project_id}/history?limit=100&offset=0` | 200 `{"events": [HistoryEvent]}` por `(occurred_at, id)` | 400 (`limit` 1..500, `offset` >= 0 — limites de `list_events`, `repository.md`), 404 |
| `GET /v1/documents/{document_id}/history?limit=100&offset=0` | idem, filtrado por documento | idem |
| `GET /v1/projects/{project_id}/export` | 200 `application/zip`, `Content-Disposition: attachment; filename="projeto-<project_id>.zip"` | 404 `not_found` (projeto) ou `no_result` (projeto sem nenhum documento concluído: nenhum zip vazio é gerado), 409 `export_in_progress`, 413 `payload_too_large` (acima de 512 MiB), 503 `data_dir_unavailable` |

Conteúdo do zip de exportação (`api/export.py`): um `.md` por documento com resultado vigente, em ordem de
`document_id`, com o nome do arquivo de Markdown do resultado (`<stem>.md`; nas colisões `<stem>-<document_id>.md`),
sem diretórios; os bytes são os do arquivo lido pela abertura confinada. Documento cujo arquivo sumiu, está sem
permissão de leitura ou fora da regra confinada **não** entra no zip e é listado em `LEIA-ME.txt` (em português) dentro
do próprio zip; falta de permissão no meio da cópia recusa a exportação inteira com 503 `data_dir_permission_denied`.
O zip é gravado num arquivo temporário em `/tmp` (tmpfs de 768 MiB do container) já removido do diretório ao ser
aberto; `Content-Length` informa o tamanho. A exportação é de uma por vez (409 `export_in_progress` até a resposta
terminar de ser entregue). `limit` do histórico vai de 1 a 500 (padrão 100); `offset` >= 0 (padrão 0).

"Resultado vigente" = `last_result` de `list_documents`: o resultado do job `succeeded` mais recente do documento. Um job mais novo que falhou não apaga o resultado vigente anterior.

## Estado do consumidor (operação)

| Método e rota | Sucesso | Erros |
|---------------|---------|-------|
| `GET /v1/status` | 200 `{"consumer": {"connected": bool, "last_event_at": str\|null}, "publisher": {"connected": bool}}` | — |

Lê só estado em memória da API (sem tocar banco nem fila): `consumer.connected` é verdadeiro enquanto a thread consumidora mantém a conexão e o canal AMQP; `last_event_at` é o instante do último evento tratado (ISO 8601 com fuso) ou `null`; `publisher.connected` repete o estado da conexão publicadora. O app usa o sinal para avisar "consumidor parado" (FR-017). `GET /health` não muda de caminho, campos nem significado (FR-033).

## Fluxo ao vivo (US3)

`GET /v1/projects/{project_id}/events` — ver [live-events.md](live-events.md).
