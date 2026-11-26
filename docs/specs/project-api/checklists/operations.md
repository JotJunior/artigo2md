# Deployment/Ops Checklist: API da versão desktop (project-api)

**Purpose**: validar a qualidade dos requisitos de implantação e operação da `project-api` (configuração da stack, diretório de dados e permissões do bind mount, dependências e lockfile, migração do banco, recuperação após queda, observabilidade, retenção). Testa o que está ESCRITO na spec/plan/research/contracts/quickstart, não a implementação.
**Created**: 2026-11-25
**Feature**: [spec.md](../spec.md)
**Profundidade / audiência**: Standard; reviewer do PR (defaults da skill — execução autônoma feature-00c, sem operador interativo) [Assumption]

## Configuração e implantação

- [x] CHK001 - Cada variável de ambiente nova ou reutilizada tem valor no compose, regra de validação e comportamento em falha especificados? [Completude, Spec §FR-006, FR-032; contracts/configuration.md "Variáveis lidas pela API"] {auto}
  - Evidência: tabela com colunas Status, Valor no compose, Validação e Falha para `ARTIGO2MD_DATA_DIR_HOST/_MOUNT`, `ARTIGO2MD_API_ALLOW_DEV_ORIGIN` (`ARTIGO2MD_API_ALLOWED_ORIGINS` constava aqui e foi removida por decisão do operador, dec-052), `ARTIGO2MD_RABBITMQ_HOST/PORT`, credenciais e banco; "Nenhuma falha de configuração derruba o processo".
- [x] CHK002 - O pré-requisito `ARTIGO2MD_DATA_DIR` (existente, absoluto, resolvido, sem `/` final) e a recusa de `up.sh` em português estão especificados, inclusive para quem já tem um `stack/.env` sem a variável? [Cobertura, contracts/configuration.md "stack/.env.example" e "up.sh"; plan.md R-07] {auto}
  - Evidência: `up.sh` recusa ausente/vazio/relativo/inexistente/não-diretório/diferente de `pwd -P`; R-07 prevê mensagem explicando o valor e atualização de README e `.env.example`.
- [x] CHK003 - O requisito cobre o descompasso entre o `--data-dir` do worker e o `ARTIGO2MD_DATA_DIR` da API, com sintoma e diagnóstico definidos para o usuário? [Gap, research.md Decision 5, Decision 10; contracts/configuration.md "stack/.env.example"; Spec §FR-025] -> Fechado na onda-006 (tarefa 2.x): research.md Decision 10: mensagem em portugues e sintoma do descompasso --data-dir x ARTIGO2MD_DATA_DIR.
  - Gap: a igualdade é exigida só por comentário do `.env.example` ("use o mesmo caminho"); se divergirem, o `markdown_path` do worker cai fora do diretório de dados e o job termina falho com motivo genérico de confinamento, sem mensagem que aponte a causa. Destino: `/create-tasks` (especificar a mensagem de diagnóstico).
- [x] CHK004 - O requisito de comparação exata (sensível a maiúsculas) do prefixo do diretório de dados, e sua razão, está explícito? [Clareza, research.md Decision 10 e Decision 5] {auto}
  - Evidência: "comparação de prefixo é exata ... porque `ARTIGO2MD_DATA_DIR` e o `--data-dir` do worker são o mesmo caminho resolvido".
- [x] CHK005 - O que muda no compose do serviço `api` e o que fica inalterado (porta, usuário, `read_only`, `cap_drop`, healthcheck, `depends_on`) estão listados sem ambiguidade? [Completude, Spec §FR-030; contracts/configuration.md "stack/compose.yaml"] {auto}

## Permissões do bind mount e lockfile (itens sinalizados pelo plan)

- [x] CHK006 - O usuário `10001` do container consegue ler Markdown e remover pastas gravados pelo worker nativo no bind mount do runtime Docker do macOS em uso? O requisito define o critério de aceite e o que fazer se falhar? [Assumption, Spec §FR-004, FR-024, FR-030; plan.md R-02; research.md "A verificar na implementação"; quickstart.md cenário 8] -> Decisao do operador (block-001/dec-023, 2026-11-25): runtime Docker Desktop 29.9.2 no macOS (contexto `desktop-linux`, observado em `docker info`); se o usuario `10001` nao ler ou remover arquivos do worker no bind mount, a API recusa a operacao com mensagem clara em portugues, sem rodar o container como root e sem `chmod` automatico; o comportamento real e verificado no teste de integracao (tarefa 11.3).
  - Aberto: nenhuma fonte do projeto afirma o comportamento (Docker Desktop/OrbStack/colima). O critério está escrito (cenário 8: leitura e `DELETE` funcionam; se não, registrar runtime e erro) e a saída é "decisão do operador antes de mudar `user` do container" (R-02) — essa decisão e a escolha do runtime são do dono. Não auto-resolver.
- [x] CHK007 - A regeneração do `uv.lock` (necessária porque a imagem usa `uv sync --frozen --only-group api` e o grupo `api` ganha `pika==1.5.0`) está listada como entregável com critério de aceite? [Gap, research.md Decision 3; contracts/configuration.md "pyproject.toml"; plan.md "Source Code" e "Technical Context"] -> Fechado na onda-006 (tarefa 2.x): plan.md "Source Code" e contracts/configuration.md "uv.lock (entregavel)" com criterio de aceite.
  - Gap: `stack/api/Dockerfile:19` usa `--frozen --only-group api` e o `uv.lock` atual tem `pika 1.5.0` (extra `queue`), mas o grupo `api` em `pyproject.toml` não o lista; sem regenerar, o build da imagem falha ou omite o pika. Só a Decision 3 menciona `uv lock`; o contrato de configuração e a árvore de arquivos do plan (que não cita `uv.lock`) omitem o arquivo. Destino: `/create-tasks` (tarefa "regenerar `uv.lock` e conferir o diff restrito ao grupo `api`").
- [x] CHK008 - Está decidido que a regeneração do `uv.lock` não pode alterar outras versões já verificadas (somente o grupo `api` ganha `pika`)? [Assumption, Spec §Constitution II (licenças); contracts/configuration.md "Nenhuma outra dependência nova"] -> Decisao do operador (block-001/dec-023, 2026-11-25): aceito: o `uv lock` so adiciona `pika` ao grupo `api`; nenhuma outra versao muda.
  - Aberto: `uv lock` pode mover outras versões; o plan afirma "nenhuma outra dependência nova" mas não define a política para atualizações transitivas. Aceitar ou restringir é decisão do dono.

## Banco de dados e migração

- [x] CHK009 - A aplicação da migração `0002` pela própria API (na partida e na reconexão), com falha mantendo `/health` e rotas de banco em 503, está especificada? [Completude, research.md Decision 12; contracts/store-extensions.md "Migração"] {auto}
  - Evidência: `apply_migrations(settings)` antes de servir rotas de banco; arquivo novo com checksum próprio, `0001_initial.sql` inalterado.
- [x] CHK010 - O requisito trata jobs `pending` já existentes no banco quando a migração `0002` é aplicada (coluna `published_at` nula)? [Gap, contracts/store-extensions.md "Migração `0002`"; plan.md "Fluxos principais" (republica `list_unpublished_jobs` na partida)] -> Fechado na onda-006 (tarefa 2.x): contracts/store-extensions.md: jobs pending pre-existentes tratados como nao publicados e republicados na partida.
  - Gap: `list_unpublished_jobs` seleciona `pending` com `published_at IS NULL`; jobs `pending` criados antes da migração (por exemplo por testes do job-store em banco de usuário) seriam republicados na primeira partida. A premissa "não há jobs pendentes anteriores" não está escrita nem há backfill. Destino: `/clarify` (premissa) ou `/create-tasks`.
- [x] CHK011 - Os efeitos da migração sobre as regras existentes (transição `pending → failed`, novo tipo `project_renamed`, teste que compara o CHECK com `EVENT_TYPES`) estão descritos, inclusive o ajuste do teste? [Consistência, contracts/store-extensions.md "Mudança de comportamento", "Migração"] {auto}
  - Evidência: troca de `jobs_guard_transition()` e do CHECK, `EVENT_TYPES` atualizado e `tests/test_store_migrations.py:69` passando a ler o CHECK vigente.

## Recuperação, disponibilidade e observabilidade

- [x] CHK012 - Os requisitos de sobrevivência a queda/reinício do broker e do banco (reconexão independente, republicação na partida, `/health` sempre respondendo, 503 em português) estão completos? [Cobertura de cenários, Spec §FR-017, FR-032, US2-9, US6-8; plan.md "Fluxos principais"] {auto}
  - Evidência: "cada dependência tem seu laço de reconexão independente e o `/health` nunca depende delas"; consumo com repetição e recuo (Decision 4); quickstart cenários 3 e 4.
- [x] CHK013 - O resultado esperado do quickstart cenário 4 ("nenhum job republicado em dobro") é compatível com a republicação at-least-once aceita em R-06? [Conflict, quickstart.md cenário 4; plan.md R-06; Spec §SC-005] -> Fechado na onda-006 (tarefa 2.x): quickstart cenario 4 alinhado a plan R-06 (duplicata tolerada, resultado repetido descartado).
  - Gap: o quickstart exige mais do que o plan aceita; mesmo achado de api.md CHK017, repetido aqui por ser critério operacional. Destino: `/clarify`.
- [x] CHK014 - Há requisito para o usuário ou o app saberem que o consumidor de eventos está parado/desconectado, dado que `/health` mantém o significado do contrato da API base? [Gap, Spec §FR-017, FR-033; contracts/live-events.md "fonte de verdade é o banco"] -> Fechado na onda-006 (tarefa 2.x): contracts/http-api.md "Estado do consumidor": GET /v1/status (tarefa 7.3.5); /health inalterado.
  - Gap: se só o consumidor cair (a publicadora e o banco seguem), jobs ficam em `pending`/`running` sem sinal observável; a spec cobre mensagens de 503 só para operações que falham. Destino: `/create-tasks` (especificar sinal mínimo, sem alterar `/health`).
- [x] CHK015 - O idioma e o conteúdo permitido dos logs operacionais (descartes do consumidor, avisos de configuração) estão definidos? [Clareza, Spec §FR-016, FR-031; contracts/configuration.md "Falha"; plan.md "Constitution Check" III] {auto}
  - Evidência: "descartado com registro em português"; avisos de origem inválida em português; sem senha nem texto bruto de driver.
- [x] CHK016 - O requisito de capacidade do consumidor (R-05: consumo sequencial) tem um limiar mensurável, em vez de "medir no cenário 4"? [Mensurabilidade, plan.md R-05; quickstart.md cenário 4; Spec §SC-005] -> Decisao do operador (block-001/dec-023, 2026-11-25): medir no cenario 4 do quickstart basta; sem limiar de vazao (nao ha meta; premissa P-07 da conversion-queue).
  - Aberto: o único critério numérico é SC-005 (20 documentos, perda zero); não há meta de vazão de eventos com 4 workers. Definir se isso basta é decisão do dono.

## Armazenamento e retenção

- [x] CHK017 - O tamanho do `tmpfs /tmp` é compatível com o teto de exportação de 512 MiB (zip gravado em `/tmp` do container)? [Gap, research.md Decision 11; plan.md S-04; contracts/configuration.md "Mantidos sem mudança" (`tmpfs: /tmp`)] -> Fechado na onda-006 (tarefa 2.x): contracts/configuration.md: tmpfs /tmp com size=768m para o teto de 512 MiB; plan S-04.
  - Gap: `stack/compose.yaml` declara `tmpfs: - /tmp` sem `size`; o requisito não relaciona o limite de 512 MiB (Markdown somado, mais o zip em si) com a capacidade efetiva do tmpfs. Destino: `/create-tasks`.
- [x] CHK018 - Há política de retenção para os diretórios `<data_dir>/<job_id>/` de jobs substituídos (reprocessamento, tentativas falhas) além da exclusão do projeto? [Gap, Spec §FR-004, FR-026; plan.md S-01] -> Decisao do operador (block-001/dec-023, 2026-11-25): manter os diretorios `<data_dir>/<job_id>/` de jobs substituidos e apaga-los somente junto com a exclusao do projeto.
  - Aberto: FR-004 só remove na exclusão do projeto; reprocessar cria job novo e preserva os anteriores (FR-026), logo o disco cresce sem limite. Manter tudo é decisão de produto (histórico) a confirmar.
- [x] CHK019 - As decisões de infraestrutura N/A (scheduling, rotação de chaves, token externo, mutex multi-pod, backup) estão declaradas com motivo? [Premissa, Spec §nota "Decisões de infraestrutura"] {auto}
  - Evidência: instância única local sem login; persistência e backup pertencem a `job-store`/`container-stack`.

## Testabilidade e entrega

- [x] CHK020 - Os requisitos de teste separam o que roda sem Docker (fakes de banco e fila) do que exige a stack real, com as variáveis que habilitam cada nível e um ciclo completo com worker real? [Completude, Spec §FR-034; plan.md "Technical Context" (Testing), "Project Structure" (tests/)] {auto}
  - Evidência: `tests/fake_store.py` + `tests/fake_amqp.py`; `ARTIGO2MD_STACK=1`/`ARTIGO2MD_DB_TESTS=1`; `test_api_integration.py` com worker real.
- [x] CHK021 - O "PDF de amostra do repositório" exigido por SC-001 e pelo quickstart cenário 1 existe como artefato rastreável? [Ambiguity, Spec §SC-001; quickstart.md cenário 1 passo 2; tests/conftest.py fixtures `article_pdf`] -> Fechado na onda-006 (tarefa 2.x): quickstart cenario 1: PDF de amostra = fixture article_pdf (tests/conftest.py:40-42), sem PDF versionado.
  - Gap: o repositório não tem PDF versionado; as amostras são geradas por fixtures de `tests/conftest.py`. O cenário manual manda usar "uma pasta do repositório que contenha um PDF de amostra", o que não existe. Destino: `/clarify` (dizer de onde vem o PDF do cenário 1).
- [x] CHK022 - A plataforma-alvo (macOS com compose local e worker nativo no host) está explícita, evitando requisito implícito para outros sistemas? [Premissa, plan.md "Target Platform"; Spec §US6] {auto}
  - Evidência: "container `artigo2md-api:local` (Linux) no compose local do macOS; worker nativo no host".

## Notes

- Items `{auto}` ja vem resolvidos pelo agente (`[x]` com citacao, ou marcador `[Gap]`/`[Ambiguity]`/`[Conflict]` mantendo `[ ]`)
- Items `{humano}` decididos pelo dono do produto em 2026-11-25 (block-001/dec-023) e marcados `[x]` com a decisao citada em cada item
- Destinos: `[Gap]` (CHK003, CHK007, CHK010, CHK014, CHK017) → `/create-tasks`; `[Ambiguity]`/`[Conflict]` (CHK013, CHK021) → `/clarify`
- Origem CORS padrao `tauri://localhost` (sinalizada pelo plan) esta em `security.md` CHK003; propostas numericas sem lastro em `api.md` CHK031
