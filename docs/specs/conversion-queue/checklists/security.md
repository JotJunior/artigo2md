# Security Checklist: Fila de conversão e worker nativo

**Purpose**: Qualidade dos requisitos de isolamento local, credenciais, validação de entrada e proteção de dados do worker.
**Created**: 2026-11-25
**Feature**: [spec.md](../spec.md) | Plan: [plan.md](../plan.md) | Pesquisa: [research.md](../research.md)

## Isolamento local

- [x] CHK001 - Está especificado que a única conexão de rede é a `127.0.0.1` na porta do RabbitMQ, sem parametrização do host, sem telemetria e sem conexão para fora? [Clareza, Spec §FR-016, §US6-2; research Decision 9] {auto}
- [x] CHK002 - O critério de isolamento é mensurável (zero conexões fora de loopback e zero credenciais em mensagens/logs/eventos num lote completo)? [Mensurabilidade, Spec §SC-010] {auto}
- [x] CHK003 - O requisito de não acessar banco de dados e de não trafegar o PDF pela fila está explícito? [Completude, Spec §FR-007, §FR-008] {auto}
- [ ] CHK004 - O limite de confiança dos produtores de mensagens está documentado? Hoje qualquer cliente com as credenciais do broker pode pedir ao worker que leia qualquer PDF absoluto legível pelo usuário e grave o Markdown no diretório de dados; a spec não afirma que isso é aceito. [Assumption, Spec §FR-007; queue-messages §job.pdf_path] (premissa em `tasks.md`, P-02; aguardando revisão do operador) {humano}

## Credenciais

- [x] CHK005 - A origem das credenciais (ambiente, depois arquivo da stack) e a proibição de imprimi-las em mensagens, logs, eventos e `repr` estão definidas? [Completude, Spec §FR-016, §FR-023; research Decision 9; data-model §WorkerSettings] {auto}
- [x] CHK006 - O comportamento para credenciais ausentes ou recusadas informa só o nome das variáveis e a origem, nunca o valor, sem laço de reconexão agressivo? [Clareza, Spec §Edge Cases (credenciais), §FR-023; worker-cli §Mensagens, código 4] {auto}
- [x] CHK007 - Está definido que as credenciais herdadas por variável de ambiente não devem chegar aos processos de conversão filhos (iniciados por `spawn`, que herdam o ambiente) e a Docling/pymupdf4llm? [Gap, Spec §FR-016, §FR-006; plan §Arquitetura] **Resolvido:** `spec.md` FR-024; `research.md` Decision 9 (remoção de `os.environ` antes do `spawn`); `data-model.md` §WorkerSettings; teste de aceite em 5.2.6. {auto}

## Validação de entrada

- [x] CHK008 - A confinação da escrita ao diretório de dados cobre `job_id` e nome derivado (separadores, `..`, NUL, nome oculto, symlink plantado) e trata violação como falha definitiva? [Cobertura, Spec §FR-022, §Edge Cases; research Decision 6; plan §Plano de testes] {auto}
- [x] CHK009 - Mensagem inválida, versão desconhecida e motor inválido são rejeitados sem execução e sem derrubar o worker? [Completude, Spec §FR-009, §US2-4, §US3-3; queue-messages §Códigos de erro] {auto}
- [x] CHK010 - Há limite especificado para o tamanho do corpo da mensagem de job e a profundidade do JSON aceitos pelo validador escrito à mão? [Gap, Spec §FR-009; research Decision 10] **Resolvido:** `queue-messages.md` §`job` (corpo ≤ 16384 bytes, profundidade ≤ 8, política PROPOSTA com justificativa; `invalid_message`). {auto}
- [x] CHK011 - O tratamento de `pdf_path` que é symlink ou aponta para arquivo especial está definido além de "arquivo regular"? [Ambiguity, queue-messages §pdf_not_found] **Resolvido:** `queue-messages.md` §`job` ("`pdf_path` especial": `os.stat`, só arquivo regular; FIFO/dispositivo/diretório/symlink pendente → `pdf_not_found`). {auto}

## Proteção de dados e logs

- [ ] CHK012 - Os requisitos de logs e eventos tratam dados potencialmente sensíveis além das credenciais (caminhos de PDF, trechos de exceções de bibliotecas de terceiros em `reason` do código `conversion_failed`)? A spec só proíbe credenciais. [Gap, Spec §FR-016, §US6; queue-messages §error.reason] (premissa em `tasks.md`, P-03; aguardando revisão do operador) {humano}
- [ ] CHK013 - As permissões dos Markdown gravados e das mensagens mortas (que guardam o corpo original) são especificadas para documentos sensíveis? [Gap, Spec §FR-007, §FR-011] (premissa em `tasks.md`, P-04; aguardando revisão do operador) {humano}
- [ ] CHK014 - A fila de eventos acumula mensagens (com caminhos) até a `project-api` consumi-las; há política de retenção ou limite? [Gap, Spec §FR-008; research Decision 5 `artigo2md.events.all`] (premissa em `tasks.md`, P-05; aguardando revisão do operador) {humano}

## Dependências

- [x] CHK015 - As dependências novas (`pika`, `psutil`) têm licença permissiva confirmada e registrada, com versão fixada para casar com a verificada? [Completude, Spec §FR-020, §US6-5; plan §Constitution Check II; research L12, L15, L16] {auto}
- [ ] CHK016 - Está definido que a versão fixada de `pika` (1.5.0) foi avaliada quanto a vulnerabilidades conhecidas, além da licença? [Gap, Spec §FR-020; plan §Technical Context] (premissa em `tasks.md`, P-06; aguardando revisão do operador) {humano}

## Notes

- Items `{auto}` ja vem resolvidos pelo agente (`[x]` com citacao, ou marcador `[Gap]`/`[Ambiguity]`).
- Items `{humano}` ficam `[ ]` aguardando decisao do dono do produto.
- Destino dos abertos: CHK007, CHK010 -> `/create-tasks` (requisito a especificar); CHK011 -> `/clarify`; CHK004, CHK012, CHK013, CHK014, CHK016 -> decisao humana (nenhum bloqueia o core do worker; CHK004/CHK014 devem ser registrados como premissa ou risco aceito).
- Achados OWASP informativos M1/L1/L2/L3 do plan (dec-024) nao foram reabertos aqui; este checklist valida a qualidade dos requisitos.
