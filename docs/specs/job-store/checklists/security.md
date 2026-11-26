# Security Checklist: Persistência da versão desktop (job-store)

**Purpose**: validar a qualidade dos requisitos de segurança da camada de persistência: conexão restrita ao ambiente local, proteção da senha, prevenção de injeção, isolamento dos testes e integridade das migrações. Não valida código.
**Created**: 2026-11-25
**Feature**: [spec.md](../spec.md)
**Contexto**: banco local monousuário, sem login nem contas (briefing, seção 2); sem autenticação/autorização de usuário nesta feature.

## Conexão e confinamento local

- [x] CHK001 - Os destinos de conexão permitidos são uma lista fechada e verificável? [Clareza, Spec §FR-016] {auto} — evidência: contracts/repository.md ("host ... aceitos: `127.0.0.1`, `::1`, `localhost`, `postgres`") e research D4.
- [x] CHK002 - A recusa de destino não permitido está especificada sem ecoar o valor recusado? [Completude, Spec §FR-016] {auto} — evidência: contracts/repository.md ("o valor recusado não é ecoado"); quickstart cenário 7.2.
- [x] CHK003 - O requisito "nenhum serviço remoto, nenhuma telemetria" é traduzido em critério verificável? [Mensurabilidade, Spec §FR-016, Constitution I] {auto} — evidência: FR-016 (MUST NOT conectar a serviço remoto nem enviar telemetria), edge case do host remoto, quickstart cenário 7.2 (host externo recusado antes de conectar).
- [x] CHK004 - O critério de aceitação do nome `postgres` ("todos os endereços loopback ou privados") é compatível com FR-016 ("loopback ou o nome do serviço na rede interna da stack")? [Ambiguity, Spec §FR-016, Research D4] {auto} — `ipaddress...is_private` também aceita faixas de rede local do usuário (por exemplo, 192.168.x.x), que não são a rede interna da stack; a spec não define "rede interna" por faixa ou rede. — **resolvido (job-store, execute-task 2026-11-25)**: `stack/compose.yaml` não declara `networks:` nem sub-rede, então nenhuma faixa mais estreita pode ser citada sem presumir; o critério do nome `postgres` fica "todos os endereços loopback ou privados" e a spec FR-016 foi realinhada; o residual virou o risco L-04 no plan (spec FR-016, Assumptions; plan, Riscos; contracts/repository.md).
- [x] CHK005 - Está especificado que a conexão usa o endereço já validado (e não resolve o nome de novo no momento de conectar)? [Gap, Spec §FR-016, Research D4] {auto} — D4 resolve o nome antes de conectar, mas o contrato conecta com `host=` nome; um segundo resolver entre validação e conexão (troca de resposta DNS) não está coberto por requisito. — **resolvido (job-store, execute-task 2026-11-25)**: a conexão usa o endereço já validado (`DbSettings.resolve_connect_host()`), sem nova resolução do nome (spec FR-016; contracts/repository.md; research D4).

## Segredos e vazamento de informação

- [x] CHK006 - O requisito de não exibir a senha é mensurável e cobre mensagem, log, `repr` e saída de teste? [Mensurabilidade, Spec §FR-016, SC-007] {auto} — evidência: SC-007 (zero ocorrências em mensagens, logs e saída dos testes), `password` com `field(repr=False)` (contracts/repository.md), quickstart cenário 7.3 (`str(exc)`, `repr(settings)`, saída capturada).
- [x] CHK007 - Está proibida a DSN em texto único que embutiria a senha? [Completude, Spec §FR-016, Research D4] {auto} — evidência: research D4 (argumentos nomeados de `asyncpg.connect`, "nunca uma DSN em texto"; alternativa DSN rejeitada).
- [x] CHK008 - Está definido que mensagens traduzidas e exceções encadeadas não carregam texto bruto do driver (campo `detail` com valores de linhas, parâmetros de conexão) pela cadeia `__cause__`/`__context__`? [Ambiguity, Spec §FR-016, contracts/repository.md] {auto} — `DatabaseUnavailableError` é lançada `from None`, mas o contrato não exige o mesmo de `StoreDatabaseError` e `MigrationFailedError` ("<motivo>"), cuja mensagem inclui motivo vindo do driver. — **resolvido (job-store, execute-task 2026-11-25)**: toda exceção da camada é lançada `from None`, com texto próprio em português e sem `message`/`detail`/`hint` do servidor; vale também para `StoreDatabaseError` e `MigrationFailedError` (contracts/repository.md, "Encadeamento de exceções"; contracts/migrations.md).
- [x] CHK009 - Os parâmetros ausentes ou inválidos têm falha definida que cita só o nome do parâmetro? [Completude, Spec §FR-016, US5-7] {auto} — evidência: contracts/repository.md, tabela de parâmetros e mensagens de exemplo (`StoreConfigError`); quickstart cenário 7.1.
- [x] CHK010 - A origem dos parâmetros (ambiente, arquivo apontado por variável, `./stack/.env`) está ordenada e o leitor do arquivo é sem execução? [Clareza, Spec §FR-016, Research D4] {auto} — evidência: research D4 (ordem igual à do `doctor`; `infra.parse_env_file` "nada é executado").

## Prevenção de injeção e integridade

- [x] CHK011 - A proibição de montar SQL por concatenação de valores do usuário é um requisito e tem verificação? [Completude, Spec §FR-015, US5-6] {auto} — evidência: FR-015; contracts/repository.md ("toda consulta usa parâmetros `$n`"); US5-6 (acentos, aspas, barras e caracteres de comando gravados e lidos idênticos).
- [x] CHK012 - Os pontos em que um identificador SQL é montado dinamicamente (criação do banco de teste) estão restritos por requisito? [Cobertura, Spec §FR-015, Research D9] {auto} — evidência: research D9 (nome gerado pelo teste, só `[a-z0-9_]`, identificador citado; guarda `^artigo2md_test_[0-9a-f]{16}$`).
- [x] CHK013 - A integridade das migrações aplicadas é protegida contra alteração posterior e contra banco de versão mais nova? [Completude, Spec §FR-012, US3-5, US3-7] {auto} — evidência: checksum SHA-256 em `schema_migrations`, `MigrationChecksumError` e `SchemaTooNewError` (contracts/migrations.md).
- [x] CHK014 - As regras que protegem a integridade dos dados (job não final único, transições, resultado só de job concluído, pertencimento projeto/documento/job) têm garantia no banco e não só na camada? [Completude, Spec §FR-013, §FR-004] {auto} — evidência: data-model (índice único parcial, gatilho `jobs_guard_transition`, FKs compostas, gatilho diferido de resultado).
- [x] CHK015 - O histórico de eventos tem requisito de imutabilidade e está definido onde a garantia vale (camada x banco)? [Clareza, Spec §FR-008, US4-3] {auto} — evidência: FR-008 e US4-3 ("a camada não oferece essa operação"); data-model, gatilho `history_events_append_only` recusa alteração; remoção só em cascata pela exclusão do projeto.

## Isolamento dos testes e dados do usuário

- [x] CHK016 - O requisito de não tocar o banco real do usuário nos testes é mensurável e tem salvaguarda contra exclusão do banco errado? [Mensurabilidade, Spec §FR-019, SC-006] {auto} — evidência: SC-006; research D9 (banco temporário `artigo2md_test_<16 hex>`, `DROP DATABASE` só se o nome casar o padrão e diferir de `POSTGRES_DB`).
- [x] CHK017 - A falta de permissão para criar o banco temporário tem comportamento definido? [Cobertura, Research D9] {auto} — evidência: research D9 passo 5 (falha com mensagem em português explicando a causa).
- [x] CHK018 - A fronteira que impede worker, CLI e módulos de conversão de acessarem o banco está especificada com verificação automática? [Completude, Spec §FR-017, US5-4] {auto} — evidência: FR-017; quickstart cenário 8.

## Retenção e riscos residuais

- [x] CHK019 - A retenção e o descarte dos dados persistidos estão definidos? [Completude, Spec §FR-011] {auto} — evidência: FR-011 (dados mantidos por tempo indeterminado até a exclusão do projeto; exclusão remove todos os dependentes e não toca arquivos do disco).
- [x] CHK020 - Está classificado quais campos de texto livre podem conter conteúdo de documento (`error`, `error_summary`, `issues`, `message`) e que isso permanece só no banco local? [Gap, Spec §FR-005, §FR-007, Constitution I] {auto} — o plan afirma que "só caminhos e métricas vão ao banco", mas as tentativas guardam o texto do erro do motor e `issues`, sem requisito que delimite o conteúdo possível. — **resolvido (job-store, execute-task 2026-11-25)**: `error`, `error_summary`, `issues` e `message` classificados como texto livre possivelmente derivado de documento, só no banco local e nunca enviado a fora (spec FR-005; contracts/repository.md; plan, Riscos).
- [x] CHK021 - A aceitação dos riscos residuais L-01 (conexão com o usuário da imagem, sem papel de menor privilégio) e L-02 (sem TLS em loopback/rede interna) reflete o apetite de risco do produto? [Risco, Assumption, Plan §Riscos] {humano} — **resolvido (job-store, execute-task 2026-11-25)**: **resposta do operador (block-006, opção A)**: aceitos L-01 (usuário da imagem, sem papel de menor privilégio) e L-02 (sem TLS em loopback/rede interna) (plan, Riscos; spec, Assumptions).
- [x] CHK022 - A aceitação de L-03 (texto sem limite de tamanho em nomes, caminhos e mensagens) é adequada, ou o produto exige um teto? [Risco, Assumption, Plan §Riscos] {humano} — **resolvido (job-store, execute-task 2026-11-25)**: **resposta do operador (block-006, opção A)**: aceito L-03 (texto sem limite de tamanho em nomes, caminhos e mensagens) (plan, Riscos; spec, Assumptions).
- [x] CHK023 - Há exigência de acompanhar avisos de segurança conhecidos do driver `asyncpg` (faixa `>=0.32.0,<0.33`) além da verificação de licença? [Gap, Spec §FR-018] {humano} — **resolvido (job-store, execute-task 2026-11-25)**: **resposta do operador (block-006, opção A)**: não acompanhar avisos de segurança do `asyncpg`; só a verificação de licença (FR-018) (spec, Assumptions; plan, Riscos).

## Notes

- Items `{auto}` marcados `[x]` citam a evidência; os `[ ]` com marcador são achados abertos.
- Destinos: `[Ambiguity]` (CHK004, CHK008) → `/clarify`; `[Gap]` (CHK005, CHK020) → tarefas de requisito em `/create-tasks`; `{humano}` (CHK021, CHK022, CHK023) → decisão do dono do produto antes de `execute-task`.
- Autenticação, autorização, MFA e rotação de segredos não se aplicam (monousuário, sem login: briefing seção 2; spec, nota de "Decisões de infraestrutura").
- Atualização 2026-11-25 (execute-task, FASE 1): CHK004/005/008/020 resolvidos; CHK021-023 respondidos pelo operador no block-006.
