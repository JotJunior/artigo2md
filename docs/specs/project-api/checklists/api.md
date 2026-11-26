# API Checklist: API da versão desktop (project-api)

**Purpose**: validar a qualidade dos requisitos do contrato HTTP/SSE da `project-api` (completude das rotas, clareza dos limites, consistência entre spec, plan e contratos, versionamento, tratamento de erro). Testa o que está ESCRITO em `spec.md`, `plan.md`, `research.md`, `data-model.md` e `contracts/`, não a implementação.
**Created**: 2026-11-25
**Feature**: [spec.md](../spec.md)
**Profundidade / audiência**: Standard; reviewer do PR (defaults da skill — execução autônoma feature-00c, sem operador interativo) [Assumption]

## Completude do contrato

- [x] CHK001 - Todo requisito funcional com superfície HTTP (FR-001 a FR-026, FR-033) está mapeado para ao menos uma rota publicada? [Completude, Plan §Cobertura de requisitos; contracts/http-api.md, tabelas de rotas] {auto}
  - Evidência: a tabela "Cobertura de requisitos" do plan liga FR-001–FR-026 a módulos de rota e o http-api.md traz rota para projetos, fontes, documentos, jobs, resultado, Markdown, histórico, exportação e cancelamento (FR-018 → `POST /v1/jobs/{id}/cancel`; FR-021 → `.../export`; FR-023 → `.../history`).
- [x] CHK002 - A estratégia de versionamento (prefixo, convivência `/v1`/`/v2`, regra para campo novo opcional, `/health` sem prefixo) está definida? [Completude, Spec §FR-033; contracts/http-api.md "Versionamento"] {auto}
  - Evidência: linha "Versionamento" de "Regras gerais" cobre os quatro pontos.
- [x] CHK003 - Cada representação de resposta tem origem rastreável nos registros existentes (nomes e tipos de `models.py`), de modo que `desktop-projects`/`desktop-processing` usem só o publicado? [Completude, Spec §FR-033, SC-008; contracts/http-api.md "Representações"] {auto}
  - Evidência: tabela "Representações" cita `models.py`/`QualityRecord` por tipo e SSE cita `models.JOB_STATUSES` e `queue-messages.md` (live-events.md). Nota: o aviso inicial do contrato fala em "coluna 'Origem'", que a tabela não tem como coluna separada (a origem está no cabeçalho "Campos (origem `models.py`)"); redação, sem efeito no conteúdo.
- [x] CHK004 - O mapeamento dos rótulos em português da spec (ainda não processado, pendente, em andamento, concluído, falho, cancelado) para os valores de `status` do contrato está explícito? [Consistência, Spec §FR-009; contracts/http-api.md "DocumentStatus"; data-model.md "Job"] {auto}
  - Evidência: `not_processed|pending|running|succeeded|failed|cancelled` em `DocumentStatus`; a lista de motores da spec (FR-026) coincide com `models.REQUESTED_ENGINES` citado na rota de jobs.
- [x] CHK005 - Todo código de erro HTTP citado nas tabelas de rotas existe na tabela de códigos de erro, com a exceção do store ou a condição que o dispara? [Consistência, contracts/http-api.md "Corpo de erro"] {auto}
  - Evidência: 400/403/404/409/413/415/422/500/503 por rota têm linha correspondente (`invalid_request`, `origin_not_allowed`, `not_found`/`no_result`, `project_name_taken`, `active_job_exists`, `invalid_transition`, `validation_failed`, `database_unavailable`, `broker_unavailable`, `data_dir_unavailable`, `internal_error`).
- [x] CHK006 - A fronteira entre `400 invalid_request` (formato) e `422 validation_failed` (domínio do job-store) está definida para valores de enumeração inválidos (`kind`, `mode`, `engine`)? [Ambiguity, contracts/http-api.md "Corpo de erro"; Spec §FR-005, FR-026] -> Fechado na onda-006 (tarefa 2.x): contracts/http-api.md, tabela de erros: 400 = formato, 422 = dominio (kind/mode/engine listam valores validos).
  - Gap: a tabela diz "fora do formato" para 400 e "valor fora do domínio do job-store" para 422; `kind`/`mode` inválidos cabem nas duas. Só o motor tem destino explícito (422, na rota de jobs). Destino: `/clarify`.
- [x] CHK007 - Está definido o comportamento de `POST /v1/projects/{id}/jobs` quando o corpo traz `document_ids` e `not_processed` juntos, nenhum dos dois, ou ids de documento de outro projeto? [Gap, contracts/http-api.md "Jobs"; Spec §FR-010] -> Fechado na onda-006 (tarefa 2.x): contracts/http-api.md "Jobs": exatamente um de document_ids/not_processed (400); ids inexistentes ou de outro projeto -> 404 sem criacao parcial.
  - Gap: o contrato escreve "**ou**" mas não diz o status para a combinação inválida nem para ids de outro projeto. Destino: `/create-tasks` (tarefa "especificar").
- [x] CHK008 - "Documento ainda não processado" (alvo do lote `not_processed`) é definido de forma não ambígua? [Clareza, Spec §FR-010, US2 cenário 2; data-model.md "Entity: Document"] {auto}
  - Evidência: status derivado do job mais recente, `not_processed` = sem job; documentos falhos/cancelados são reenfileirados por `document_ids` (US2 cenário 10 e US5).
- [x] CHK009 - A ordenação das listas sem ordem declarada (projetos, documentos de um projeto, tentativas) está especificada? [Gap, contracts/http-api.md "Projetos", "Fontes e documentos", "Jobs"] -> Fechado na onda-006 (tarefa 2.x): contracts/http-api.md: projetos por (created_at,id), documentos por document_id, tentativas por position (repository.py:295,416,596).
  - Gap: só `history` (`occurred_at, id`), fontes (por `id`, store-extensions.md) e `Attempt` (`position`, spec US4 cenário 2) têm ordem; `GET /v1/projects` e `GET /v1/projects/{id}/documents` não dizem. Telas dependem da ordem. Destino: `/create-tasks`.
- [x] CHK010 - A ausência de paginação nas listas (projetos, documentos, fontes, tentativas) está justificada com o volume esperado? [Clareza, Spec §Edge Cases "Pasta com muitos arquivos"; Spec §FR-009] {auto}
  - Evidência: "a listagem de documentos do projeto responde numa única consulta" para até 1.000 documentos; paginação por limite/deslocamento só onde a spec exige (FR-023) com limites 1..500 herdados de `list_events`.

## Clareza e consistência

- [x] CHK011 - O requisito "o limite de tamanho do caminho acompanha o do contrato da fila" é inequívoco quanto ao número (16384 bytes da mensagem ou 1024 bytes do caminho)? [Ambiguity, Spec §FR-008; contracts/http-api.md "Regras de caminho"; conversion-queue/contracts/queue-messages.md L36-37] -> Fechado na onda-006 (tarefa 2.x): spec.md FR-008 reescrito: limite do caminho = 1024 bytes (PC_PATH_MAX); 16384 bytes e a mensagem inteira.
  - Evidência da divergência: a spec cita os 16384 bytes da mensagem; o contrato adota 1024 bytes (`PC_PATH_MAX`, `queue-messages.md`). O plan resolveu para 1024 com fonte, mas o texto de FR-008 permanece impreciso. Destino: `/clarify` (alinhar a redação da spec).
- [x] CHK012 - FR-031 ("erros de validação ... não gravar nada parcialmente") é consistente com FR-007/FR-008 e US1 cenário 8 (registrar os caminhos válidos e rejeitar só os inválidos da mesma lista)? [Conflict, Spec §FR-031 vs §FR-008, US1-8; contracts/http-api.md `DocumentOutcomes.rejected`] -> Fechado na onda-006 (tarefa 2.x): spec.md FR-031 delimita a excecao do registro por lista (created/existing/rejected).
  - Gap: a redação literal de FR-031 contradiz o registro parcial; o contrato resolve com `created/existing/rejected`. Falta a spec delimitar a exceção. Destino: `/clarify`.
- [x] CHK013 - Os limites que a spec chama "corpo grande demais" (Edge Cases) têm valores numéricos no requisito, e o teto de 10 000 caminhos por pedido é compatível com a referência de 1.000 documentos? [Gap, Spec §Edge Cases "Corpo de pedido grande demais", "Pasta com muitos arquivos"; contracts/http-api.md 413] -> Fechado na onda-006 (tarefa 2.x): spec.md FR-010/FR-020/FR-021/FR-011 trazem os valores numericos aceitos (dec-023); 10 000 cobre a referencia de 1.000 documentos.
  - Gap: 16 MiB, 10 000 caminhos e 512 MiB existem só no contrato [PROPOSTA]; nenhum FR os nomeia. A aceitação dos valores é decisão do dono (ver CHK031).
- [x] CHK014 - A regra "um job ativo por documento" é a mesma em enfileirar, lote, reprocessar e concorrência, com um único código de erro? [Consistência, Spec §FR-012, FR-026, Edge Cases "Dois clientes"; contracts/http-api.md `409 active_job_exists`] {auto}
  - Evidência: `ActiveJobExistsError` → 409 em `POST /v1/documents/{id}/jobs`; no lote vira `skipped`; a restrição é do job-store ("um não final por documento", data-model.md "Entity: Job").
- [x] CHK015 - A semântica de cancelar job em andamento (marca cancelado, resultado tardio ignorado, worker não interrompido) e a saída para job órfão estão definidas sem depender de mensagem de cancelamento na fila? [Clareza, Spec §FR-018, Edge Cases "API reiniciada"; contracts/http-api.md bullet `cancel`] {auto}
  - Evidência: `cancel` vale para `pending` e `running` (inclusive órfão) e eventos tardios são descartados por FR-016.
- [x] CHK016 - Está definido o estado do job quando o evento `result` traz `markdown_path` fora do diretório de dados? [Ambiguity, Spec §US4-6, FR-025; data-model.md "State transitions"; quickstart.md cenário 7 passo 3] -> Fechado na onda-006 (tarefa 2.x): spec.md US4-6: job passa a failed com motivo em portugues (data-model.md ja previa fail_job).
  - Gap: a spec diz que o caminho "não é aceito como resultado" e que o fato é registrado; plan/data-model/quickstart fecham o job como `failed` com motivo. A spec não afirma essa transição. Destino: `/clarify`.
- [x] CHK017 - O critério de SC-005 ("sem resultado nem histórico duplicados") é consistente com o resultado esperado do quickstart cenário 4 ("nenhum job republicado em dobro") e com o risco aceito R-06 (republicação at-least-once)? [Conflict, Spec §SC-005; quickstart.md cenário 4; plan.md R-06] -> Fechado na onda-006 (tarefa 2.x): spec.md SC-005, quickstart cenario 4 e plan R-06 alinhados: duplicata at-least-once tolerada, resultado repetido descartado.
  - Gap: o quickstart promete mais que SC-005 e que R-06 garantem (republicação em dobro é aceita quando a confirmação se perde). Destino: `/clarify` (alinhar o resultado esperado).
- [x] CHK018 - A resposta de falha de publicação é consistente entre pedido unitário e lote (503 com o `Job` já falho vs. 202 com `failed`) e com US2-10 e SC-006? [Consistência, Spec §US2-10, SC-006; contracts/http-api.md "Jobs"; quickstart.md cenário 3] {auto}
  - Evidência: unitário → 503 `broker_unavailable` com `job` falho; lote → 202 com `failed[]`; nos dois o job termina `failed` com motivo e pode ser reenfileirado (research Decision 8).

## Critérios de aceite e mensurabilidade

- [x] CHK019 - SC-008 é objetivamente mensurável nas duas metades ("constroem suas telas usando só o contrato" e "cobre 100% das rotas")? [Mensurabilidade, Spec §SC-008] -> Fechado na onda-006 (tarefa 2.x): spec.md SC-008 com criterio observavel (rotas do app = rotas do contrato, campos com tipo e origem; teste na tarefa 11.5).
  - Gap: a metade "100% das rotas" é verificável (comparar rotas registradas com `contracts/http-api.md`); "sem precisar ler o código" não tem critério observável. Destino: `/clarify`.
- [x] CHK020 - Cada critério SC-001 a SC-003, SC-005 a SC-007 tem verificação automatizada ou cenário de quickstart correspondente? [Mensurabilidade, Spec §Success Criteria; quickstart.md cenários 1, 3, 4, 7; plan.md "Project Structure" (tests)] {auto}
  - Evidência: SC-001 → cenário 1 + `test_api_integration.py`; SC-002 → mensagens em PT em `test_api_*`; SC-003/SC-007 → cenário 7 + `test_api_paths.py`/`test_api_security.py`; SC-005 → cenário 4; SC-006 → cenário 3.
- [x] CHK021 - A meta de latência do SSE (2 s em 95% das medições) está validada, e não apenas proposta? [Mensurabilidade, Spec §US3-1, SC-004; plan.md "Performance Goals"; quickstart.md cenário 6] -> Decisao do operador (block-001/dec-023, 2026-11-25): aceita a meta SSE de 2 s em 95% das medicoes (SC-004).
  - Aberto: a spec remete "a validar no plan"; o plan mantém [PROPOSTA] e remete à medição do cenário 6. Aceitar ou ajustar o número é decisão do dono.

## Cobertura de cenários e fluxo ao vivo

- [x] CHK022 - Os requisitos do fluxo ao vivo cobrem formato dos eventos, isolamento por projeto, keep-alive, reconexão sem `Last-Event-ID`, múltiplos clientes e cliente lento? [Cobertura, Spec §FR-019, FR-020; contracts/live-events.md] {auto}
  - Evidência: tabela de eventos, `: keep-alive` a cada 15 s, "Sem `id:`/`Last-Event-ID`", fila interna de 1000 com encerramento do cliente lento, "fonte de verdade é o banco".
- [x] CHK023 - Está definido o que o fluxo ao vivo emite (e o que acontece com assinantes) quando o projeto é arquivado ou excluído com jobs não finais e clientes conectados? [Gap, Spec §US1-4, Edge Cases "Exclusão de projeto com jobs em andamento"; contracts/live-events.md] -> Fechado na onda-006 (tarefa 2.x): contracts/live-events.md: archive emite job_state cancelled por job e mantem o fluxo; delete emite e encerra o fluxo.
  - Gap: `job_state cancelled` é listado como "mudança gravada", mas arquivar/excluir cancela jobs em lote e o contrato não diz se há um evento por job nem se o fluxo é encerrado. Destino: `/create-tasks` (especificar).
- [x] CHK024 - O comportamento de `GET .../export` para projeto sem nenhum documento concluído está definido (zip vazio, 404 ou outro)? [Gap, Spec §FR-021, US4-7; contracts/http-api.md "export"; research.md Decision 11] -> Fechado na onda-006 (tarefa 2.x): contracts/http-api.md e research Decision 11: export sem documento concluido -> 404 no_result, sem zip vazio.
  - Gap: a Decision 11 define colisão de nomes e `LEIA-ME.txt` para arquivos omitidos, mas não o caso sem concluídos. Destino: `/create-tasks`.
- [x] CHK025 - O limite de exportação simultânea e de tamanho tem comportamento definido ao ser excedido? [Cobertura, contracts/http-api.md 409 `export_in_progress`, 413; plan.md S-04] {auto}
  - Evidência: uma exportação por vez (409) e teto de 512 MiB de Markdown somado (413) [valores PROPOSTA — ver CHK031].
- [x] CHK026 - A mensagem de erro pedida em cada recusa de regra de negócio (nome em uso, job ativo, motor inválido, cancelar job final, documento sem resultado) tem texto/idioma e destino claros? [Clareza, Spec §FR-002, FR-012, FR-018, FR-024, FR-026, FR-031; contracts/http-api.md "Corpo de erro"] {auto}
  - Evidência: corpo `{"error": {"code", "message"}}` com `message` em português; `422` do motor lista `auto, docling, pymupdf, mineru, markitdown`; `invalid_request` cita o campo sem eco do valor.

## Dependências e premissas

- [x] CHK027 - O contrato HTTP não depende de comportamento do job-store que não exista hoje, e as extensões necessárias estão listadas como novas? [Dependência, Spec §FR-002; contracts/store-extensions.md "Operações novas"] {auto}
  - Evidência: `rename_project`, `get_source`, `list_sources`, `get_document`, `get_job`, `list_project_job_ids`, `mark_job_published`, `list_unpublished_jobs` e a mudança de `fail_job` (`pending → failed`) via migração `0002`.
- [x] CHK028 - A premissa de que o app desktop lista as pastas e copia os arquivos (a API não lê o disco do host) está registrada como decisão de clarificação e refletida nos contratos? [Premissa, Spec §Clarifications Q1/Q2, FR-006, FR-007; contracts/http-api.md "Regras de caminho"] {auto}
  - Evidência: `kind=folder` recebe `documents` pré-listado; `mode=copied` exige caminhos sob `<DATA_DIR_HOST>/sources/<project_id>/` copiados pelo app antes da chamada.
- [x] CHK029 - O contrato `GET /health` está preservado e a documentação automática continua desligada, de forma verificável? [Consistência, Spec §FR-033; contracts/http-api.md "Documentação automática"; plan.md "Constraints"] {auto}
  - Evidência: `/docs`, `/redoc`, `/openapi.json` → 404; `/health` "fica sem prefixo" e inalterado.
- [x] CHK030 - A origem das mensagens que a API publica (campos e limites do contrato `schema_version` 1) está citada e a API não adiciona campos ao job? [Consistência, Spec §FR-011; plan.md "Convenções de Borda"; research.md Decision 1, 8] {auto}
  - Evidência: `schema.build_job(str(job.id), pdf_path, engine, 1)` com os campos `schema_version, type, job_id, pdf_path, engine, attempt`; contrato único compartilhado worker/API (`jobqueue/schema.py`).
- [x] CHK031 - Os valores numéricos propostos sem lastro em requisito (prazo de publicação de 10 s, 32 assinantes SSE, exportação de 512 MiB, corpo de 16 MiB, 10 000 caminhos por pedido, truncamento de 2000 caracteres, fila SSE de 1000 eventos, keep-alive de 15 s) foram aceitos pelo dono do produto? [Assumption, plan.md "Fluxos principais", S-03, S-04, S-06; contracts/http-api.md 413/503; contracts/live-events.md] -> Decisao do operador (block-001/dec-023, 2026-11-25): aceitos todos os limites propostos (publicacao 10 s, 32 assinantes SSE, exportacao 512 MiB, corpo 16 MiB, 10 000 caminhos por pedido, truncamento 2000 caracteres, fila SSE 1000 eventos, keep-alive 15 s).
  - Aberto: todos marcados [PROPOSTA] ou sem fonte externa; não são fatos, são limites de desenho.

## Notes

- Items `{auto}` ja vem resolvidos pelo agente (`[x]` com citacao, ou marcador `[Gap]`/`[Ambiguity]`/`[Conflict]` mantendo `[ ]`)
- Items `{humano}` decididos pelo dono do produto em 2026-11-25 (block-001/dec-023) e marcados `[x]` com a decisao citada em cada item
- Destinos: `[Ambiguity]`/`[Conflict]` (CHK006, CHK011, CHK012, CHK016, CHK017, CHK019) → `/clarify`; `[Gap]` (CHK007, CHK009, CHK013, CHK023, CHK024) → `/create-tasks`; `{humano}` (CHK021, CHK031) → decisao antes de `/execute-task`
- Gate `requirement-coverage.sh` sobre `spec.md`: `RESULT|...|requirements=34|covered=34|errors=0` (nenhum FR sem cenario)
