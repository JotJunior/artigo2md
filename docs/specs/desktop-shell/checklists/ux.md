# UX Checklist: Esqueleto do app desktop macOS

**Purpose**: Validar a qualidade dos requisitos de experiência (navegação, estados, mensagens, configurações, acessibilidade) antes do create-tasks.
**Created**: 2026-11-24
**Feature**: [spec.md](../spec.md)

## Estrutura e navegação

- [x] CHK001 - A barra lateral tem entradas e destaque de seleção definidos? [Completude, Spec §FR-002] {auto}
- [x] CHK002 - A ordem da árvore está definida sem ambiguidade (pastas antes, alfabética sem caixa)? [Clareza, Spec §FR-005] {auto}
- [x] CHK003 - A largura da barra lateral tem faixa e padrão quantificados? [Clareza, data-model.md §AppSettings `sidebarWidth`: 200 a 480 px, padrão 260] {auto}
- [x] CHK004 - O comportamento ao redimensionar a janela ou recolher a barra lateral está definido? [Cobertura, Spec §FR-001, §FR-039, Edge Cases] {auto}

## Estados de interface

- [x] CHK005 - O estado sem pasta aberta tem conteúdo definido? [Completude, contracts/commands.md §ErrorCode `noFolderOpen`: estado vazio com convite "Abrir pasta…"] {auto}
- [ ] CHK006 - Estão definidos os estados "pasta aberta vazia" e "nenhum arquivo selecionado" no visualizador? [Gap, nenhum texto na spec nem no catálogo de data-model.md] {auto}
- [ ] CHK007 - Há indicação de carregamento definida para a árvore e para o documento (não só para imagens)? [Gap, data-model.md: `loading` existe só no status de imagem; Spec §FR-029 exige só responsividade] {auto}
- [x] CHK008 - O conteúdo exibido ao reabrir ou recarregar um arquivo alterado ou apagado está definido? [Cobertura, Spec §FR-031, Edge Cases] {auto}

## Mensagens (FR-003)

- [x] CHK009 - Todos os textos literais da spec têm chave no catálogo de mensagens? [Completude, data-model.md §Catálogo de mensagens] {auto}
- [ ] CHK010 - Todo status de imagem e todo código de erro têm texto no catálogo? [Gap, data-model.md status `tooLarge` e `readError`; contracts/commands.md `tooLarge`, `invalidSettings`, `io`: "definida no execute-task"] {auto}
- [ ] CHK011 - Os rótulos sem texto literal na spec (opções de aparência, níveis de tamanho, "Recolher barra lateral") estão definidos? [Gap, data-model.md §Catálogo: "definidos no execute-task"] {auto}
- [x] CHK012 - O aviso de configurações ilegíveis tem texto definido? [Completude, Spec §FR-023; data-model.md `settingsReset`] {auto}

## Configurações

- [x] CHK013 - As opções de aparência têm valores e padrão definidos? [Clareza, Spec §FR-020] {auto}
- [ ] CHK014 - Os três níveis de tamanho do texto estão quantificados (tamanho de fonte de cada nível)? [Ambiguity, Spec §FR-020 "ao menos três níveis"; data-model.md `viewerFontSize`: só `small`/`medium`/`large`] {auto}
- [x] CHK015 - O efeito imediato e a persistência das configurações estão definidos? [Completude, Spec §FR-021, §FR-022, §FR-039] {auto}
- [x] CHK016 - "Restaurar padrões" tem efeito definido, incluindo o que é preservado? [Clareza, Spec §FR-024; contracts/commands.md §reset_settings: preserva `lastFolder`] {auto}

## Visualizador

- [ ] CHK017 - Está definida a indicação visual de link externo inativo (o que a pessoa vê ao clicar)? [Ambiguity, Spec §FR-040: "MUST ficar inativos, sem abrir nada", sem retorno visual definido] {auto}
- [x] CHK018 - Cada falha de imagem tem aviso próprio, sem interromper o restante do documento? [Cobertura, Spec §FR-014, §FR-016, §FR-032, §FR-041] {auto}
- [x] CHK019 - A fórmula inválida tem tratamento isolado definido? [Edge Case, Spec §FR-033] {auto}
- [x] CHK020 - O limite do aviso de arquivo grande está quantificado? [Clareza, Spec §FR-034; research.md Decision 3: 5.000.000 bytes] {auto}

## Acessibilidade

- [ ] CHK021 - Há requisitos de navegação por teclado (árvore, barra lateral, configurações) e de rótulos para leitor de tela? [Gap, nenhum requisito de acessibilidade em spec.md, plan.md ou data-model.md] {auto}
- [ ] CHK022 - Acessibilidade (teclado e VoiceOver) entra no escopo desta feature ou fica para uma feature posterior do roadmap? [Risco, decorre de CHK021] {humano}

## Critérios de sucesso

- [x] CHK023 - O critério de "100% em português" tem verificação automatizada? [Mensurabilidade, Spec §SC-006; plan.md §Testes, linha "Textos"] {auto}
- [ ] CHK024 - O critério de primeiro uso (SC-001) define quantas pessoas e como a medição é feita? [Ambiguity, Spec §SC-001: "até 30 segundos, sem instrução externa", sem protocolo] {auto}

## Notes

- Items `{auto}` vêm resolvidos pelo agente (`[x]` com citação, ou marcador `[Gap]`/`[Ambiguity]`).
- Items `{humano}` ficam `[ ]` aguardando decisão do dono do produto.
- Destinos: `[Gap]` (CHK006, CHK007, CHK010, CHK011, CHK021) viram tarefas no create-tasks; `[Ambiguity]` (CHK014, CHK017, CHK024) são decisões de desenho a fixar no execute-task e registrar no catálogo/contrato.
