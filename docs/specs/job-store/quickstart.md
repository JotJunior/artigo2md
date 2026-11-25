# Quickstart / Cenários de teste: job-store

**Feature**: `job-store` | **Spec**: [spec.md](spec.md) | **Contratos**: [repository](contracts/repository.md), [migrations](contracts/migrations.md)

## Pré-requisitos

- `uv sync --extra dev --extra pymupdf` (traz `asyncpg`; research, Decision 2). Os comandos abaixo usam `uv run --extra dev --extra pymupdf`, que dispensa o `sync` prévio.
- Integração: stack no ar (`sh stack/up.sh`, feature `container-stack`) e `stack/.env` preenchido.

## Rodar

```sh
uv run --extra dev --extra pymupdf pytest                                # sem Docker: integração aparece como pulada (motivo em pt)
ARTIGO2MD_DB_TESTS=1 uv run --extra dev --extra pymupdf pytest -k store  # integração contra o Postgres da stack, banco temporário
```

> Esta feature não tem borda backend↔frontend nem rota HTTP: o roundtrip end-to-end é o de gravar e ler de
> volta no Postgres real (cenários 2 e 3).

## Cenário 1: Suíte sem Docker (US5-1, FR-019, SC-005)

1. Sem `ARTIGO2MD_DB_TESTS`, rodar `uv run pytest`
2. → **Expected**: suíte verde; testes de integração `SKIPPED` com motivo "defina ARTIGO2MD_DB_TESTS=1 com a stack no ar".

## Cenário 2: Projeto, fonte e documentos (US1)

1. Aplicar migrações no banco temporário → `create_project("Revisão Sistemática")`
2. `add_source(kind="folder", mode="in_place", path="/Users/x/Artigos 2024")` e `add_source(kind="file", mode="copied", path=...)`
3. `add_document` duas vezes com o mesmo caminho
4. → **Expected**: um só documento (`created=False` na segunda); `list_documents` mostra `not_processed`; dados idênticos após fechar e reabrir o `Store`.

## Cenário 3: Job com duas tentativas e resultado (US2-2, US2-3, SC-002)

1. `create_job(doc, "auto")` → `start_job` → `record_attempt` reprovada (`accepted=False`) e aceita
2. `complete_job(..., quality=QualityRecord(score=82.5, accepted=True, tables_found=2, tables_expected=2, chars_per_page=1500.0, issues=("ação", "tabela \"x\"")))`
3. → **Expected**: `get_result` devolve os seis campos idênticos, `issues` na mesma ordem; `warnings == issues`; evento `job_succeeded` registrado.

(Os valores numéricos acima são dados de teste escolhidos para o cenário, não dados de sistema.)

## Cenário 4: Regras recusadas (US2-5, US2-6, US2-7)

1. Com job `pending`, `create_job` de novo no mesmo documento → **Expected**: `ActiveJobExistsError`, nada criado.
2. Job `running` → tentar voltar a `pending` (não há método; `UPDATE` direto é recusado pelo gatilho) → **Expected**: recusa.
3. `complete_job` duas vezes no mesmo job → **Expected**: segunda chamada `InvalidJobTransitionError`, sem segundo resultado nem evento.
4. Duas tarefas `asyncio.gather` criando job no mesmo documento → **Expected**: exatamente um sucesso e um `ActiveJobExistsError`.

## Cenário 5: Arquivar, desarquivar e excluir (US1-5..9)

1. Projeto com job `running` → `archive_project` → **Expected**: job `cancelled`, evento `job_cancelled`; `complete_job` tardio → `InvalidJobTransitionError`.
2. Criar projeto ativo com o mesmo nome em maiúsculas → `unarchive_project` do arquivado → **Expected**: `ProjectNameTakenError`, nada renomeado.
3. `delete_project` → **Expected**: fontes, documentos, jobs, tentativas, resultados e eventos removidos; outro projeto intacto.

## Cenário 6: Migrações (US3, SC-001)

1. Banco vazio → `apply_migrations` → **Expected**: `applied=(1,)`, em até 10 s.
2. De novo → **Expected**: `applied=()`.
3. Duas chamadas concorrentes → **Expected**: ambas sem erro, cada versão registrada uma vez.
4. Alterar o checksum gravado de `0001` → **Expected**: `MigrationChecksumError`.
5. Inserir versão `9999` em `schema_migrations` → **Expected**: `SchemaTooNewError`.
6. Migração de teste com SQL inválido no meio → **Expected**: `MigrationFailedError` citando o arquivo; nenhuma tabela parcial.

## Cenário 7: Configuração e segredo (US5-7, FR-016, SC-007)

1. `DbSettings.from_env` sem `POSTGRES_PASSWORD` → **Expected**: `StoreConfigError` citando `POSTGRES_PASSWORD`.
2. `ARTIGO2MD_POSTGRES_HOST=db.example.com` → **Expected**: recusa antes de conectar, sem ecoar o valor.
3. Senha errada / porta fechada → **Expected**: `DatabaseUnavailableError`; a senha não aparece em `str(exc)`, `repr(settings)` nem na saída capturada do pytest.

## Cenário 8: Fronteira (US5-4, FR-017, FR-021, SC-004)

1. Subprocesso importa `artigo2md.cli`, `web`, `worker`, `router`, `engines`, `pdfscan`, `infra`, `api.app`
2. → **Expected**: `asyncpg` e `artigo2md.store` ausentes de `sys.modules`; varredura AST de `src/artigo2md/` fora de `store/` sem import deles.

## Cenário 9: Escala (SC-003)

1. Projeto com 1.000 documentos, metade com jobs concluídos
2. `list_documents(project_id)` com o contador do ponto único de execução de SQL do repositório zerado antes da chamada
3. → **Expected**: até 2 s e exatamente 1 comando SQL contado; o teste imprime o ambiente medido (sistema, versão do Python,
   `server_version` do Postgres), com cache quente (uma execução de aquecimento antes da medida).

## Cenário 10: Linha do tempo (US4, FR-008, FR-022)

1. Projeto com dois documentos; gravar com `add_event` eventos intercalados dos dois, alguns com o mesmo instante
2. `list_events(document_id=...)` de cada um → **Expected**: só os eventos do documento consultado, em ordem estável `(occurred_at, id)`.
3. Gravar centenas de eventos e paginar com `limit`/`offset` → **Expected**: páginas disjuntas e completas; `limit` 0 ou 501, nenhum filtro e os dois filtros → `StoreValidationError`.
4. `UPDATE` direto de um evento (conexão de teste) → **Expected**: recusado pelo gatilho; a camada não tem método de alteração/remoção.
5. Todos os instantes lidos → **Expected**: `datetime` com `tzinfo`; a ordem não muda ao trocar o fuso da sessão do banco (`SET TIME ZONE`).
6. Sequência de operações (criar projeto, fonte, documento, job, iniciar, tentativa, concluir) → **Expected**: os eventos automáticos na ordem das operações; uma operação que falha não deixa evento.

## Cenário 11: Mesmo caminho em dois projetos e fontes repetidas (US1-7, FR-002, FR-003)

1. Dois projetos registram o mesmo caminho de PDF → **Expected**: dois documentos independentes.
2. Duas fontes com o mesmo caminho no mesmo projeto → **Expected**: duas fontes, sem erro; `add_document` repetido devolve o documento existente com a fonte original.

## Cenário 12: Finalizações e atomicidade (US2-8, US2-9, US2-10)

1. Job cujo melhor resultado tem `accepted=False` → `complete_job` → **Expected**: job `succeeded` com resultado e `accepted=False`.
2. Job com todas as tentativas falhando → `fail_job` → **Expected**: tentativas com seus erros guardadas, nenhum resultado.
3. Forçar falha ao gravar o resultado ou o evento dentro de `complete_job` → **Expected**: estado final, resultado e evento não ficam gravados; um job `succeeded` sem resultado é recusado pelo gatilho diferido no commit.

## Cenário 13: Faixas de qualidade (FR-007)

1. `QualityRecord` com `score` 101 ou -1, `tables_found` -1, `chars_per_page` -0.5 → **Expected**: `StoreValidationError` antes do SQL; o job não muda.
2. `score` 0 e 100, `issues` vazio → **Expected**: aceitos; `issues` volta como lista vazia.

## Cenário 14: Nomes de projeto e idioma/forma dos identificadores (FR-001, FR-020)

1. `create_project("  Revisão  ")` → **Expected**: nome lido de volta `"Revisão"` (sem espaços nas pontas); espaços internos preservados.
2. `create_project("revisão")`, `create_project("REVISÃO")` e `create_project("Straße")` seguido de `create_project("STRASSE")` → **Expected**: `ProjectNameTakenError` em cada repetição; `create_project("revisao")` é aceito (`a`≠`á`).
3. `archive_project` duas vezes e `unarchive_project` sobre projeto ativo → **Expected**: `StoreValidationError` simétrico, nada muda.
4. Teste sem banco sobre os arquivos `.sql` e os módulos → **Expected**: todo identificador do SQL é ASCII `snake_case`; a revisão humana confere o idioma.
