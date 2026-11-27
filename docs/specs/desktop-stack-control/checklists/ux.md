# UX Checklist: Controle da infraestrutura pelo app desktop

**Purpose**: Validar a qualidade dos requisitos de interação, estados, mensagens e guia da feature (revisão de requisitos, não de implementação).
**Created**: 2026-11-26
**Feature**: [spec.md](../spec.md)

## Estados e feedback

- [x] CHK001 - Os estados por serviço são enumerados e distintos, com "parado" explicitamente diferenciado de erro? [Clareza, Spec §FR-026, §US5-3] {auto}
- [x] CHK002 - A regra de composição do indicador resumido (saudável/parcial/parada) é definida de forma verificável? [Clareza, Spec §US5-2; Plan research Decision 10 (linhas 328-332), data-model InfraSnapshot.summary] {auto}
- [x] CHK003 - Toda operação longa tem requisito de mostrar a etapa atual e de nunca ficar em "iniciando" indefinidamente? [Completude, Spec §FR-015, §US2-9, Edge "Runtime parado no meio de uma operação"] {auto}
- [x] CHK004 - A recusa de operação concorrente (inclusive de segunda instância) tem comportamento e aviso definidos? [Cobertura, Spec §FR-015, §US2-10, Edge "Duas janelas ou duas instâncias"] {auto}
- [x] CHK005 - Cada etapa da primeira execução tem estados, ações de recuperação ("Tentar de novo", retomar, "Preparar modelos") e recálculo de estado real definidos? [Completude, Spec §FR-030, §FR-034, §FR-036] {auto}
- [x] CHK006 - O requisito de "indicação de atividade" quando o tamanho total do download é desconhecido é descrito de forma objetiva (o que a pessoa vê)? [Ambiguity, Spec §FR-032] {auto} — Resolvido (feature-00c, tarefa FASE 1, 2026-11-27): spec.md FR-032 e US6-3; data-model.md `FirstRunStep` (`updatedAt`, teste de atividade).

## Fluxos e coerência entre cenários

- [x] CHK007 - O fluxo da primeira "Iniciar infraestrutura" é descrito sem contradição entre US2-1 ("sobe a stack, sem pedir nada") e US6-1/FR-031 (confirmação explícita antes de baixar imagens)? O plano resolve com `imagesMissing` (sem chamar o `up.sh`), mas a spec não diz o que a pessoa vê nesse caso. [Ambiguity, Spec §US2-1 vs §US6-1, §FR-031; Plan contracts/tauri-commands.md `start_stack`] {auto} — Resolvido (feature-00c, tarefa FASE 1, 2026-11-27): spec.md US2-1 (caso `imagesMissing`); quickstart.md cenário 2, passo 1.
- [x] CHK008 - A pré-condição de "Iniciar workers" é a mesma na spec e nos contratos? A spec (FR-018, US3-1/2) só exige a stack no ar; o contrato de `start_workers` também exige a etapa `models` em `done` (erro `modelsMissing`), e a spec US6-9 trata a ausência de modelos no momento da conversão, não do início dos workers. [Conflict, Spec §FR-018, §US6-9 vs contracts/tauri-commands.md `start_workers`] {auto} — Resolvido (feature-00c, tarefa FASE 1, 2026-11-27): spec.md FR-018, US3-2, US6-9 (`modelsMissing`); data-model.md `WorkerPool`.
- [x] CHK009 - O fechamento do app é descrito sem contradição? A spec diz que o app só termina depois que nenhum processo de worker resta (Edge "Fechar o app com um job em andamento"); o plano (M21) define teto de 60 s após o qual o app sai e registra os PIDs remanescentes. [Conflict, Spec §FR-024, Edge; Plan M21] {auto} — Resolvido (feature-00c, tarefa FASE 1, 2026-11-27): spec.md edge 'Fechar o app com um job em andamento' e FR-024 (teto de 60 s, plan.md M21); quickstart.md cenário 5.
- [x] CHK010 - O comportamento sem Docker mantém "Arquivos" e "Configurações" integralmente utilizáveis e define o que a tela de infraestrutura mostra em cada diagnóstico? [Cobertura, Spec §FR-002, §FR-003, §US1-1..6] {auto}
- [x] CHK011 - Há requisito para o app ao reabrir com stack no ar e pool parado (ou pool órfão), definindo o que é mostrado e oferecido? [Cobertura, Spec §FR-021, §FR-024, Edge "Fechar o app com a stack no ar"] {auto}

## Guia de instalação

- [x] CHK012 - O guia tem requisitos de comparação, escolha, contagem de passos restantes, retomada do passo e encerramento automático ao detectar "Docker pronto"? [Completude, Spec §FR-004, §FR-006, §US4-1..7] {auto}
- [x] CHK013 - O requisito de offline e de não abrir navegador nem executar instalador é verificável, com fonte oficial registrada por passo? [Mensurabilidade, Spec §FR-005, §US4-3/4; Plan research Decision 15] {auto}
- [x] CHK014 - Restrições de versão do macOS por runtime são tratadas no guia (OrbStack 14+, colima 13+ x app 11+)? [Cobertura de edge case, Plan research Decision 15 (linhas 451-455 e 469-470)] {auto}

## Acessibilidade e idioma

- [x] CHK015 - Existem requisitos de acessibilidade para as telas novas (navegação por teclado, leitor de tela/VoiceOver, contraste, estado comunicado além da cor no indicador da barra lateral)? Nem esta spec nem `desktop-shell/spec.md` os definem (busca sem ocorrências). [Gap, Spec §FR-025/FR-026] {auto} — Resolvido (feature-00c, tarefa FASE 1, 2026-11-27): spec.md FR-042 e SC-010 (padrão herdado do desktop-shell; contraste sem limiar inventado).
- [x] CHK016 - O requisito de idioma cobre telas, guia, mensagens traduzidas de códigos de saída e é verificável por revisão? [Mensurabilidade, Spec §FR-038, §SC-006, §US7-3] {auto}

## Decisões de produto

- [ ] CHK017 - O indicador "parcial" (regra atual: tudo que não é "saudável" nem "parada", inclusive stack no ar com pool de workers parado) comunica adequadamente à pessoa não técnica, em especial no estado normal após fechar o app com a stack mantida no ar? [Risco, Plan research Decision 10] {humano}
- [ ] CHK018 - A comparação e o tom do guia (quais runtimes destacar, se há recomendação, quanto detalhe técnico) refletem o que o dono do produto quer apresentar? [Premissa, Spec §FR-004, §US4] {humano}
- [ ] CHK019 - A meta de até 20 minutos para a pessoa não técnica chegar a "Docker pronto" pelo guia (SC-002) é adequada ao público e à avaliação manual de aceitação prevista? [Mensurabilidade, Spec §SC-002] {humano}

## Notes

- Items `{auto}` resolvidos pelo agente (`[x]` com citação, ou `[Gap]`/`[Ambiguity]`/`[Conflict]` em aberto)
- Items `{humano}` ficam `[ ]` aguardando decisão do dono do produto (nunca auto-marcados)
- Destino dos abertos: CHK007 e CHK008 e CHK009 e CHK006 -> reconciliar spec/plan (create-tasks: tarefa de definição + clarify se necessário); CHK015 -> tarefa "definir requisitos de acessibilidade" no create-tasks
