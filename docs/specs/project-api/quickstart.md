# Quickstart: API da versão desktop (project-api)

**Feature**: `project-api` | **Date**: 2026-11-25

Rotas e corpos seguem [contracts/http-api.md](contracts/http-api.md) (implementados e verificados). `API` =
`http://127.0.0.1:${ARTIGO2MD_API_PORT}`.

## Pré-requisitos

- `stack/.env` com `ARTIGO2MD_DATA_DIR` apontando para uma pasta existente (caminho resolvido) e `sh stack/up.sh`.
- Worker no host: `uv run artigo2md worker --data-dir "$ARTIGO2MD_DATA_DIR"` (mesma pasta).
- Testes sem Docker: `uv run pytest`. Com a stack: `ARTIGO2MD_STACK=1 ARTIGO2MD_DB_TESTS=1 uv run pytest -k project_api`.

## Cenário 1: Roundtrip End-to-End (US1 + US2 + US4) — chamada real

1. `POST API/v1/projects` com `{"name": "Estudos"}` → guarda `id`.
2. `POST API/v1/projects/{id}/sources` com `kind=folder`, `mode=in_place`, `path` de uma pasta do repositório
   que contenha um PDF de amostra e `documents` com o caminho absoluto desse PDF. Não há PDF versionado: o PDF de amostra
   é gerado pela fixture `article_pdf` de `tests/conftest.py:40-42` (`fpdf2`, 2 páginas) numa pasta temporária; o teste de
   integração usa essa fixture e, na execução manual, o PDF vem do operador.
3. `POST API/v1/projects/{id}/jobs` com `{"engine": "auto", "not_processed": true}`.
4. Esperar `GET API/v1/documents/{document_id}` mostrar `status = "succeeded"`.
5. `GET API/v1/documents/{document_id}/markdown`.

**Expected**: cada resposta real é validada campo a campo contra as tabelas "Representações" do contrato (nomes,
tipos, `snake_case`) — o teste parseia o payload recebido da API no ar, não um mock; o Markdown é byte a byte o
arquivo em `$ARTIGO2MD_DATA_DIR/<job_id>/<stem>.md`; o histórico do projeto tem `job_created`, `job_started`,
`attempt_recorded` e `job_succeeded` (SC-001).

## Cenário 2: PDF inválido (US2-6)

1. Registrar um arquivo `.pdf` que não é PDF e enfileirar.

**Expected**: job `failed` com `error_summary` em português vindo de `reason` do evento `error`
(`invalid_pdf`); nenhum resultado.

## Cenário 3: Broker fora do ar (US2-10, SC-006)

1. Parar só o RabbitMQ; `POST API/v1/projects/{id}/jobs` para 1.000 documentos.

**Expected**: resposta 202 com todos em `failed`, cada um com motivo em português; `GET /health` continua 200;
nenhum job `pending` com `published_at` nulo ao fim. Subir o RabbitMQ: novo pedido funciona sem reiniciar a API.

## Cenário 4: Reinício da API no meio da fila (US2-9, SC-005)

1. Enfileirar 20 documentos; parar o container da API durante o processamento; esperar os workers terminarem;
   subir a API.

**Expected**: 20 jobs em estado final coerente com os eventos publicados; nenhuma tentativa, resultado nem entrada de
histórico duplicados; uma mensagem de job republicada em dobro é tolerada (entrega at-least-once, plan R-06) e o
resultado repetido que ela gerar é descartado. Medição da vazão do consumidor: só registrada, sem limiar (decisão do
operador, block-001/dec-023).

## Cenário 5: Evento duplicado e fora do contrato (US2-7, US2-8)

1. Publicar manualmente em `artigo2md.events` (painel do RabbitMQ) o mesmo `result` duas vezes, um JSON inválido,
   um `schema_version: 2` e um `job_id` inexistente.

**Expected**: um único resultado gravado; demais descartados com log em português (a duplicata em `INFO`, os outros
   três em `WARNING`); consumo segue.

## Cenário 6: Fluxo ao vivo (US3, SC-004)

1. Abrir `GET API/v1/projects/{id}/events` com `curl -N`; em outro terminal, enfileirar dois documentos.

**Expected**: `job_state pending`, `progress` em português, `job_state running` e o estado final de cada job;
evento de outro projeto não aparece; latência medida entre `emitted_at` (repassado nos eventos SSE `progress`, `live-events.md`) e a chegada ao cliente,
registrada para validar a meta de 2 s em 95% (decisão do operador, block-001/dec-023; resultado em "Medições (11.4)").

## Cenário 7: Segurança (US6, SC-003, SC-007)

1. `curl -H "Origin: https://exemplo.invalid" -X POST API/v1/projects ...` → **Expected**: 403
   `origin_not_allowed`, nada gravado.
2. `curl -H "Host: evil.example" API/health` → **Expected**: 400.
3. Publicar um `result` com `markdown_path` contendo `..` e outro com caminho fora do diretório de dados →
   **Expected**: job `failed` com motivo em português, sem eco do caminho, e nenhum arquivo fora do diretório lido.
   Criar um link simbólico dentro do diretório de dados apontando para fora e publicar um `result` cujo
   `markdown_path` é o link → **Expected**: o `result` é **aceito** (o texto do caminho está dentro da pasta de dados;
   a API não resolve o link no cadastro do resultado) e `GET .../markdown` responde **403 `path_not_allowed`** sem vazar
   o conteúdo (a abertura usa `O_NOFOLLOW`); o job **não** vira `failed` por isso.
4. `docker inspect` do container `api` → **Expected**: único `Mounts` = diretório de dados em `/data`.
5. Processo novo: `import artigo2md.api.app` → **Expected**: `docling`, `pymupdf4llm`, `pypdfium2`,
   `artigo2md.router` fora de `sys.modules`.

## Cenário 8: Permissões do bind mount (verificado na onda-014; runtime: Docker Desktop 29.9.2, contexto `desktop-linux`)

1. Com o worker nativo tendo gravado um Markdown, `GET .../markdown` e depois `DELETE API/v1/projects/{id}`.

**Expected**: leitura e remoção funcionam com o usuário `10001` do container no runtime Docker do macOS em uso
(observado: funcionam, ver "Resultados observados"); se não funcionarem, a API recusa a operação com mensagem clara em português (código `data_dir_permission_denied`), sem executar o container como root e sem `chmod` automático (decisão do operador, block-001/dec-023); registrar o runtime e o erro observado (nenhuma fonte do projeto afirma o comportamento).

## Resultados observados (onda-014, 2026-11-25)

Comando: `ARTIGO2MD_STACK=1 uv run pytest tests/test_api_integration.py -s` (14 testes, cerca de 70 s). Ambiente: Docker
Desktop 29.9.2 (contexto `desktop-linux`), macOS, Docling real com os modelos já no cache local (`HF_HUB_OFFLINE=1`),
projeto Compose descartável próprio (`artigo2md-it-<hex>`, portas livres, senhas geradas na hora, pasta de dados própria)
com a imagem da API reconstruída do código atual e o worker nativo gravando na mesma pasta. O teste remove containers,
rede e volumes no fim e confere que nada sobrou. Os números abaixo são medições, não promessas.

| Cenário | Observado |
|---------|-----------|
| 1 (ciclo completo) | projeto -> fonte -> job -> `succeeded`; `GET .../markdown` idêntico, byte a byte, ao arquivo gravado pelo worker; histórico com `job_created`, `job_started`, `attempt_recorded` e `job_succeeded` (uma vez cada); cada payload recebido da API no ar confere com as tabelas "Representações" |
| 2 (PDF inválido) | job `failed` com `error_summary` em português; nenhum resultado; `job_failed` no histórico |
| 3 (broker fora) | 1.000 documentos com o RabbitMQ parado: `202` em 0,7 s, 1.000 jobs `failed` com motivo, `/health` 200, nenhum `pending` sem `published_at`; ao voltar, a API (mesmo `StartedAt`) e o worker reconectaram sozinhos e o pedido seguinte concluiu |
| 4 (reinício da API) | API parada com 8 de 20 jobs concluídos; ao voltar, 20 `succeeded`, 20 resultados, nenhuma entrada de histórico (`job_created`, `job_started`, `job_succeeded`) nem tentativa duplicada |
| 5 (eventos inválidos) | `result` duplicado, JSON inválido, `schema_version: 2` e `job_id` inexistente: 1 resultado, 1 `job_succeeded`, 1 tentativa; 3 descartes em `WARNING`, em português, sem o corpo da mensagem; a duplicata é registrada em `INFO` (não aparece no nível padrão do container) |
| 6 (fluxo ao vivo) | 8 PDFs reais, 4 workers: `job_state` `pending`/`running`/`succeeded` por job e `progress`; cliente de outro projeto não recebeu nada |
| 7 (segurança) | `Origin` estranha e `null` -> 403 sem gravar; `Host` estranho -> 400; `markdown_path` com `..` ou fora da pasta de dados -> job `failed` sem eco do caminho; link simbólico dentro de `/data` apontando para fora -> o resultado é aceito (o texto do caminho está dentro) e `GET .../markdown` responde `403 path_not_allowed` sem vazar o conteúdo; um único `Mounts` (bind em `/data`); `sys.modules` sem `docling`, `pymupdf4llm`, `pypdfium2` nem `artigo2md.router` |
| 8 (permissões) | Docker Desktop 29.9.2, `desktop-linux`: o usuário `10001` do container leu o Markdown gravado pelo worker nativo (`200`) e `DELETE` do projeto respondeu `204` removendo `<mount>/<job_id>/`; nenhuma mudança de `user` foi necessária |
| Porta | `127.0.0.1:<porta>` apenas; conexão ao IP de LAN do host na mesma porta foi recusada |

### Medições (11.4)

| Medida | Valor |
|--------|-------|
| Latência `emitted_at` -> cliente SSE (20 eventos `progress`) | mediana 4 ms, p95 18 ms, máximo 18 ms; 100% dentro de 2 s (meta da decisão 1.1.1: 2 s em 95%) — **meta atendida** |
| Vazão de ponta a ponta, 8 PDFs reais com 4 workers | 44 eventos SSE em 5,1 s (cerca de 8,7 eventos/s) |
| Vazão do consumidor sequencial (1.000 `progress` acumulados com a API parada, depois drenados) | cerca de 0,4 s (cerca de 2.900 eventos/s); só medida, sem limiar (decisão 1.1.3) |
| `GET /v1/projects/{id}/documents` com 1.000 documentos (HTTP completo, 15 medições) | mediana 4 ms, p95 5 ms, máximo 6 ms |
