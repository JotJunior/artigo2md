# Requirements Checklist: Inspeção do PDF

**Purpose**: Validar a qualidade dos requisitos da spec reconstituída `pdf-inspection` (completude, clareza, consistência e cobertura), não a implementação.
**Created**: 2026-11-20
**Feature**: [spec.md](../spec.md)

## Completude de Requisitos

- [x] CHK001 - Cada requisito funcional observado cita a origem no código? [Completude, Spec §Functional Requirements] {auto} — FR-001 a FR-006 têm origem citada (fonte: `docs/specs/pdf-inspection/spec.md:31`)
- [ ] CHK002 - Estão definidos os requisitos para PDF inválido ou protegido por senha? [Gap, Spec §Edge Cases] {auto} — o único edge case é um campo pendente (fonte: `docs/specs/pdf-inspection/spec.md:25`)
- [ ] CHK003 - O processamento sem rede está escrito como requisito, e não só como inferência? [Gap, Spec §INF-001] {auto} — aparece apenas como INF-001 (fonte: `docs/specs/pdf-inspection/spec.md:40`)

## Clareza de Requisitos

- [x] CHK004 - O limiar que define página escaneada está quantificado? [Clareza, Spec §FR-002] {auto} — 80 caracteres de texto nativo (fonte: `docs/specs/pdf-inspection/spec.md:32`)
- [x] CHK005 - Os termos que caracterizam citação de tabela estão enumerados? [Clareza, Spec §FR-003] {auto} — table, tabela ou quadro seguidos de número (fonte: `docs/specs/pdf-inspection/spec.md:33`)
- [ ] CHK006 - Os símbolos matemáticos contados como indício de fórmula estão enumerados? [Ambiguity, Spec §FR-004] {auto} — o requisito diz "símbolos matemáticos" sem listá-los (fonte: `docs/specs/pdf-inspection/spec.md:34`)
- [ ] CHK007 - Os termos típicos de artigo científico estão enumerados? [Ambiguity, Spec §FR-005] {auto} — o requisito diz "termos típicos" sem listá-los (fonte: `docs/specs/pdf-inspection/spec.md:35`)

## Consistencia de Requisitos

- [x] CHK008 - A definição de página escaneada é consistente entre FR-002 e FR-006? [Consistência, Spec §FR-002, Spec §FR-006] {auto} — FR-006 deriva os dois estados da contagem definida em FR-002 (fonte: `docs/specs/pdf-inspection/spec.md:36`)

## Qualidade de Criterios de Aceite

- [ ] CHK009 - Há critério de sucesso mensurável para a capacidade? [Gap, Spec §SC-001] {auto} — SC-001 é um campo pendente (fonte: `docs/specs/pdf-inspection/spec.md:51`)
- [ ] CHK010 - A prioridade da user story reflete o valor da capacidade para o produto? [Prioridade, Spec §User Story 1] {humano}

## Cobertura de Cenarios

- [x] CHK011 - Cada requisito funcional é citado em ao menos um cenário ou edge case? [Cobertura, Spec §Acceptance Scenarios] {auto} — gate requirement-coverage: requirements=6, covered=6, errors=0
- [ ] CHK012 - Cada requisito funcional tem cenário com teste existente como origem? [Gap, Spec §FR-004] {auto} — o cenário de FR-004 é um campo pendente (fonte: `docs/specs/pdf-inspection/spec.md:21`)

## Requisitos Nao-Funcionais

- [ ] CHK013 - Há requisito de tempo de inspeção por documento? [Gap] {auto} — a spec não traz requisito de desempenho
- [ ] CHK014 - A heurística por termos (tabela, fórmula, artigo) é suficiente para o risco aceito de escolher o motor errado? [Risco, Spec §FR-003, Spec §FR-004, Spec §FR-005] {humano}

## Dependencias e Premissas

- [x] CHK015 - A dependência usada na leitura do PDF está documentada? [Dependência, Spec §FR-001] {auto} — pypdfium2 (fonte: `docs/specs/pdf-inspection/spec.md:31`)

## Notes

- Items `{auto}` ja vem resolvidos pelo agente (`[x]` com citacao, ou marcador `[Gap]`/`[Ambiguity]`)
- Items `{humano}` ficam `[ ]` aguardando decisao do dono do produto
- Marcar items concluidos com `[x]`
- Items numerados sequencialmente para referencia
