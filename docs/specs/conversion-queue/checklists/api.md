# API Checklist: Fila de conversão e worker nativo

**Purpose**: Qualidade dos requisitos do contrato de mensagens da fila, da topologia e da CLI `artigo2md worker`.
**Created**: 2026-11-25
**Feature**: [spec.md](../spec.md) | Plan: [plan.md](../plan.md) | Contratos: [queue-messages.md](../contracts/queue-messages.md), [worker-cli.md](../contracts/worker-cli.md)

## Contrato de mensagens

- [x] CHK001 - Estão definidos campos, tipos e obrigatoriedade para os quatro tipos de mensagem (job, progress, result, error)? [Completude, Spec §FR-009; queue-messages §job, §Eventos] {auto}
- [x] CHK002 - Cada campo derivado de tipo existente cita a origem no código (sem campo inventado)? [Rastreabilidade, Spec §FR-009; queue-messages §result cita `router.py`/`pdfscan.py`] {auto}
- [x] CHK003 - Há campo de versão do esquema e regra de evolução (versão nova para mudança incompatível; worker rejeita versão desconhecida sem executar)? [Completude, Spec §FR-009, US3-5; queue-messages §Campos comuns, §Evolução] {auto}
- [x] CHK004 - O cancelamento está explicitamente fora do contrato e o tratamento de uma versão futura que o traga está definido? [Clareza, Spec §FR-021; queue-messages §Campos comuns] {auto}
- [x] CHK005 - Está definido se acrescentar campo opcional a um evento é mudança compatível (mesma `schema_version`) ou incompatível? O job rejeita campos extras, mas nada é dito sobre a tolerância dos consumidores de eventos. [Ambiguity, queue-messages §Evolução vs §Campos comuns] **Resolvido:** `queue-messages.md` §Evolução ("O que é compatível": campo opcional em evento é compatível; consumidores ignoram campos desconhecidos; job rejeita extras); `spec.md` Clarifications 2026-11-25. {auto}
- [x] CHK006 - A ordem de entrega dos eventos de um mesmo job (progress antes de result/error) é especificada para o consumidor? O contrato fala de deduplicação por `job_id` + `attempt`, não de ordenação. [Gap, Spec §FR-008; queue-messages §Eventos] **Resolvido:** `queue-messages.md` §Eventos ("Ordem de entrega": mesma tentativa em sequência de um slot, `progress` antes de `result`/`error`, sem ordem entre jobs/tentativas). {auto}
- [ ] CHK007 - O contrato de mensagens e os nomes de exchanges/filas (marcados PROPOSTA) foram aceitos pelos futuros consumidores (`project-api`, app desktop)? [Assumption, queue-messages cabeçalho PROPOSTA; Spec §US3] (premissa em `tasks.md`, P-01; aguardando revisão do operador) {humano}

## Erros, retry e mensagens mortas

- [x] CHK008 - Cada falha listada em FR-010 (PDF inexistente/ilegível/com senha/inválido, motor inválido, mensagem fora do contrato, processo morto, erro de escrita) tem código e classe (passageira/definitiva)? [Cobertura, Spec §FR-010; queue-messages §Códigos de erro] {auto}
- [x] CHK009 - Está definido o que ocorre com falha passageira na última tentativa (`retryable=false` e fila de mortas)? [Completude, Spec §US2-2; queue-messages §Códigos de erro, último parágrafo] {auto}
- [x] CHK010 - A fila de mensagens mortas preserva corpo original, motivo em português, contagem de tentativas e instrução de reenvio? [Completude, Spec §FR-011; queue-messages §Mensagens mortas] {auto}
- [x] CHK011 - Valores padrão de atraso e tentativas estão definidos e documentados (30 s, 3)? [Mensurabilidade, Spec §FR-010; plan research Decision 7, worker-cli §opções] {auto}
- [x] CHK012 - Está definido como `attempt` se comporta sob entrega duplicada? Se a cópia de retry é confirmada mas o `ack` do original se perde, o job volta com `attempt` antigo e há também a cópia com `attempt+1`, o que pode exceder `max_attempts`. [Ambiguity, Spec §FR-012; queue-messages §Garantias de entrega] **Resolvido:** `queue-messages.md` §Garantias de entrega item 4 (aceito como limitação do at-least-once; `attempt` nunca passa de `max_attempts`; dedup por `job_id`+`attempt`); `quickstart.md` cenário 5b. {auto}
- [x] CHK013 - "O resultado já calculado não é perdido" (queda do broker durante a conversão) tem significado verificável? Sem estado persistente próprio (FR-012), o que persiste é o Markdown atômico; o job é reprocessado ao ser reentregue. [Ambiguity, Spec §US5-4, §FR-012] **Resolvido:** `spec.md` US5-4 reescrito e Clarifications 2026-11-25 ("resultado não perdido" = Markdown atômico no destino + reentrega idempotente). {auto}

## Entrega e topologia

- [x] CHK014 - A ordem gravar Markdown → publicar evento (confirm) → `ack` e o comportamento sem confirmação do broker estão especificados? [Completude, Spec §FR-003; queue-messages §Garantias de entrega] {auto}
- [x] CHK015 - A topologia (exchange de jobs, fila durável, retry com atraso, mortas, exchange de eventos separada) tem nomes, tipos e argumentos definidos, com declaração idempotente e erro claro em topologia incompatível? [Completude, Spec §FR-002; research Decision 5; worker-cli código 5] {auto}

## CLI

- [x] CHK016 - Cada opção do `artigo2md worker` tem tipo, padrão (ou obrigatoriedade) e validação, e os códigos de saída cobrem uso inválido, extra ausente, credenciais e topologia? [Completude, Spec §FR-001, §FR-022; worker-cli §opções, §Códigos de saída] {auto}
- [x] CHK017 - Há texto em português definido para cada situação de erro/aviso que a spec exige (broker fora, credenciais, diretório de dados, memória, topologia, encerramento)? [Cobertura, Spec §FR-018; worker-cli §Mensagens] {auto}
- [x] CHK018 - FR-001 exige "valores padrão documentados" para todos os parâmetros, mas FR-004/FR-005/FR-015 e `worker-cli.md` tornam `--workers` e `--min-free-memory` obrigatórios, sem padrão, até a medição de RAM; US4-2 fala em "comportamento definido pela regra da medição" sem dizer qual é. [Conflict, Spec §FR-001 vs §FR-004/§FR-005/§FR-015/§US4-2; worker-cli §opções] **Resolvido:** `spec.md` FR-001 (exceção a `--workers`/`--min-free-memory`) e US4-2 (recusa com "medição pendente", código 2); `worker-cli.md` §Transição. **Fechado na tarefa 7.3:** os dois padrões existem (`--workers` 4, `--min-free-memory` 4822 MB), derivados de `ram-measurement.md`; a recusa "medição pendente" foi removida. {auto}
- [x] CHK019 - Quem fecha a transição "obrigatório → padrão do relatório" (alteração de `--workers`/`--min-free-memory` após a medição) está atribuído a uma tarefa rastreável? [Gap, Spec §SC-004; plan Decision 7] **Resolvido:** `worker-cli.md` §Transição e `plan.md` Technical Context atribuem a transição à tarefa 7.3 de `tasks.md`; teste em 5.3.3/5.3.4 e 7.3.4. **Fechado na tarefa 7.3:** `tests/test_ram_measurement_report.py` exige que os padrões do código coincidam com os derivados da saída bruta (`ram-measurement.raw.json`). {auto}

## Notes

- Items `{auto}` ja vem resolvidos pelo agente (`[x]` com citacao, ou marcador `[Gap]`/`[Ambiguity]`/`[Conflict]`).
- Items `{humano}` ficam `[ ]` aguardando decisao do dono do produto.
- Destino dos abertos: CHK005, CHK012, CHK013, CHK018 -> `/clarify` (ou ajuste textual da spec); CHK006, CHK019 -> `/create-tasks` (tarefa de especificar/fechar); CHK007 -> decisao humana antes de `/execute-task` (nao bloqueia a implementacao do worker).
