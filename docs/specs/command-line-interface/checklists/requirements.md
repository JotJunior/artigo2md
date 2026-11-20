# Requirements Checklist: Linha de comando

**Purpose**: Validar a qualidade dos requisitos da spec reconstituída `command-line-interface` (completude, clareza, consistência e cobertura), não a implementação.
**Created**: 2026-11-20
**Feature**: [spec.md](../spec.md)

## Completude de Requisitos

- [x] CHK001 - Cada requisito funcional observado cita a origem no código? [Completude, Spec §Functional Requirements] {auto} — FR-001 a FR-013 têm origem citada (fonte: `docs/specs/command-line-interface/spec.md:33`)
- [ ] CHK002 - Está definido o tratamento de PDFs com o mesmo nome em pastas diferentes? [Gap, Spec §INF-001] {auto} — o caso aparece só como inferência (fonte: `docs/specs/command-line-interface/spec.md:49`)
- [ ] CHK003 - Está definido o código de saída quando todos os PDFs são pulados por já terem Markdown? [Gap, Spec §FR-005] {auto} — FR-005 só descreve o pulo (fonte: `docs/specs/command-line-interface/spec.md:37`)
- [ ] CHK004 - Estão definidos requisitos para o serve quando o host não é o local? [Gap, Spec §FR-009] {auto} — FR-009 só define os valores padrão (fonte: `docs/specs/command-line-interface/spec.md:41`)

## Clareza de Requisitos

- [x] CHK005 - O host e a porta padrão do serve estão especificados? [Clareza, Spec §FR-009] {auto} — 127.0.0.1 e 8777 (fonte: `docs/specs/command-line-interface/spec.md:41`)
- [x] CHK006 - As opções de motor estão enumeradas, com o padrão? [Clareza, Spec §FR-006] {auto} — auto, docling, pymupdf, mineru, markitdown; padrão auto (fonte: `docs/specs/command-line-interface/spec.md:38`)
- [x] CHK007 - Os códigos de saída estão especificados para os casos de erro? [Clareza, Spec §FR-003, Spec §FR-008, Spec §FR-010, Spec §FR-012] {auto} — código 1 em cada caso de erro e 0 no doctor com motores obrigatórios instalados (fonte: `docs/specs/command-line-interface/spec.md:35`)
- [ ] CHK008 - O conteúdo do relatório JSON está definido? [Ambiguity, Spec §FR-007] {auto} — FR-007 só diz que o relatório é gravado (fonte: `docs/specs/command-line-interface/spec.md:39`)
- [ ] CHK009 - Os valores possíveis da "situação" de cada motor no doctor estão enumerados? [Ambiguity, Spec §FR-012] {auto} — o termo aparece sem os valores (fonte: `docs/specs/command-line-interface/spec.md:44`)

## Consistencia de Requisitos

- [x] CHK010 - As opções de motor da linha de comando são as mesmas aceitas pelo roteador? [Consistência, Spec §FR-006] {auto} — a spec engine-routing lista o mesmo conjunto em seu FR-012 (fonte: `docs/specs/engine-routing/spec.md:50`)

## Qualidade de Criterios de Aceite

- [ ] CHK011 - Há critério de sucesso mensurável para a capacidade? [Gap, Spec §SC-001] {auto} — SC-001 é um campo pendente (fonte: `docs/specs/command-line-interface/spec.md:55`)
- [ ] CHK012 - A prioridade da user story reflete o valor da capacidade para o produto? [Prioridade, Spec §User Story 1] {humano}

## Cobertura de Cenarios

- [x] CHK013 - Cada requisito funcional é citado em ao menos um cenário ou edge case? [Cobertura, Spec §Acceptance Scenarios] {auto} — gate requirement-coverage: requirements=13, covered=13, errors=0
- [ ] CHK014 - Algum cenário de aceite tem teste existente como origem? [Gap, Spec §Acceptance Scenarios] {auto} — todos os cenários e edge cases são campos pendentes (fonte: `docs/specs/command-line-interface/spec.md:19`)

## Dependencias e Premissas

- [ ] CHK015 - Manter a linha de comando sem cenários cobertos por teste é compatível com a prioridade de qualidade e robustez registrada no briefing? [Risco, Assumption] {humano} — prioridade registrada no briefing (fonte: `docs/briefing.md:50`)

## Notes

- Items `{auto}` ja vem resolvidos pelo agente (`[x]` com citacao, ou marcador `[Gap]`/`[Ambiguity]`)
- Items `{humano}` ficam `[ ]` aguardando decisao do dono do produto
- Marcar items concluidos com `[x]`
- Items numerados sequencialmente para referencia
