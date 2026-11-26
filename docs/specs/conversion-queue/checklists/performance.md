# Performance Checklist: Fila de conversão e worker nativo

**Purpose**: Qualidade dos requisitos de concorrência, proteção de RAM, reconexão e desligamento do worker.
**Created**: 2026-11-25
**Feature**: [spec.md](../spec.md) | Plan: [plan.md](../plan.md)

## Concorrência e throughput

- [x] CHK001 - O limite de conversões simultâneas (N) e o prefetch 1 por consumidor são quantificados e testáveis? [Mensurabilidade, Spec §FR-003, §FR-004, §SC-001] {auto}
- [ ] CHK002 - Há meta de vazão ou latência por PDF (por exemplo, lote de 10 PDFs), ou fica explícito que não há alvo além de "N simultâneas"? [Gap, Spec §SC-001] (premissa em `tasks.md`, P-07; aguardando revisão do operador) {humano}
- [x] CHK003 - Existe limite de tempo por conversão? Um job travado mantém o slot ocupado e a mensagem sem `ack`; os requisitos só tratam heartbeat da conexão. Prazo de entrega do broker para consumidores [A VERIFICAR na documentação oficial do RabbitMQ 4.4]. [Gap, Spec §FR-003; research §heartbeat] **Resolvido:** `spec.md` FR-025; `research.md` Decision 11 (`consumer_timeout` 1800000 ms no RabbitMQ 4.4.1, lido da imagem local); `queue-messages.md` código `conversion_timeout`; `worker-cli.md` §Limite por conversão. {auto}

## Proteção de RAM

- [x] CHK004 - O limite de memória livre é configurável, o padrão depende da medição e o conversor ocioso é descarregado após limite configurável? [Completude, Spec §FR-005; worker-cli §opções] {auto}
- [x] CHK005 - A reavaliação de memória após adiamento tem intervalo definido (10 s) e a medida de memória usada (`available`) está identificada? [Clareza, Spec §FR-005; plan §Decision 7; research L13] {auto}
- [x] CHK006 - A checagem de RAM por slot é serializada ou contabiliza conversores em carga? Vários slots frios podem ver a mesma memória livre ao mesmo tempo e carregar modelos de 1 a 2 GB em paralelo; FR-005 trata a checagem "antes de iniciar", por conversor. [Gap, Spec §FR-005; data-model §Slot] **Resolvido:** `spec.md` FR-005 e Clarifications; `data-model.md` §Slot ("Gate de RAM": único, lock, reserva compartilhada); `quickstart.md` cenário 7b. {auto}
- [x] CHK007 - "Sem consumir CPU em espera ativa" (fila vazia) tem critério verificável? [Ambiguity, Spec §Edge Cases (fila vazia)] **Resolvido:** `spec.md` Edge Cases (fila vazia); `quickstart.md` cenário 9b; testes em 5.1.6 e 6.2.5. {auto}
- [x] CHK008 - A condição de pool acima da RAM (aviso sem recusar o valor) e o caso N = 1 estão definidos? [Cobertura, Spec §US4-5, §Edge Cases (N = 1); worker-cli §Mensagens] {auto}

## Medição de RAM (FR-015)

- [x] CHK009 - O conteúdo mínimo do relatório está especificado (máquina, memória total, PDFs com perfil, pelo menos três tamanhos de pool, ocioso/carregado/pico por processo, comportamento do gate)? [Completude, Spec §FR-015, §SC-004; research Decision 8] {auto}
- [x] CHK010 - Está explícito que nenhum número padrão de workers ou de memória é fixado antes do relatório e que os padrões devem citar seus números? [Clareza, Spec §FR-004, §FR-015, §SC-004; plan §Constraints] {auto}
- [ ] CHK011 - Os PDFs de medição (texto nativo, escaneado, com tabelas) têm origem definida, dado que PDFs reais podem ser sensíveis? [Gap, Spec §FR-015; research Decision 8] (premissa em `tasks.md`, P-08; aguardando revisão do operador) {humano}

## Reconexão e desligamento

- [x] CHK012 - A reconexão tem política quantificada (1 s dobrando até 15 s) compatível com o alvo de retomar em até 1 minuto? [Mensurabilidade, Spec §FR-014, §SC-007; plan §Decision 7] {auto}
- [x] CHK013 - O prazo de encerramento é configurável (padrão 30 s) e define o destino do job que não termina (devolvido sem `ack`, filhos encerrados)? [Completude, Spec §FR-013, §SC-008; worker-cli §opções] {auto}
- [x] CHK014 - Está definido o que acontece se a reconexão e os adiamentos por RAM coincidirem (slot em `reconnecting` com memória baixa)? [Gap, Spec §FR-005, §FR-014; data-model §Slot] **Resolvido:** `spec.md` FR-005; `data-model.md` §Slot (slot em `reconnecting` não avalia o gate); `quickstart.md` cenário 7c; teste em 5.2.7. {auto}

## Capacidade

- [ ] CHK015 - O crescimento da fila de eventos sem consumidor (até a `project-api` existir) e da fila de mortas tem limite ou política definida? [Gap, Spec §FR-008, §FR-011; research Decision 5] (premissa em `tasks.md`, P-05; aguardando revisão do operador) {humano}

## Notes

- Items `{auto}` ja vem resolvidos pelo agente (`[x]` com citacao, ou marcador `[Gap]`/`[Ambiguity]`).
- Items `{humano}` ficam `[ ]` aguardando decisao do dono do produto.
- Destino dos abertos: CHK003, CHK006, CHK014 -> `/create-tasks` (tarefa de especificar/decidir); CHK007 -> `/clarify`; CHK002, CHK011, CHK015 -> decisao humana, nao bloqueantes para o core.
