# Requirements Checklist: Roteamento de motores e avaliação de qualidade

**Purpose**: Validar a qualidade dos requisitos da spec reconstituída `engine-routing` (completude, clareza, consistência e cobertura), não a implementação.
**Created**: 2026-11-20
**Feature**: [spec.md](../spec.md)

## Completude de Requisitos

- [x] CHK001 - Cada requisito funcional observado cita a origem no código? [Completude, Spec §Functional Requirements] {auto} — FR-001 a FR-016 têm origem citada (fonte: `docs/specs/engine-routing/spec.md:39`)
- [ ] CHK002 - Está definido o comportamento quando o motor solicitado explicitamente não está instalado? [Gap, Spec §FR-001] {auto} — FR-001 só diz que o plano contém apenas esse motor (fonte: `docs/specs/engine-routing/spec.md:39`)
- [ ] CHK003 - Está definido um limite de tempo por motor ou por conversão? [Gap] {auto} — a spec não traz requisito de tempo limite

## Clareza de Requisitos

- [x] CHK004 - A nota de aceite está quantificada? [Clareza, Spec §FR-009] {auto} — pelo menos 65 (fonte: `docs/specs/engine-routing/spec.md:47`)
- [x] CHK005 - Os descontos da nota estão quantificados com a condição de cada um? [Clareza, Spec §FR-006, Spec §FR-007, Spec §FR-008] {auto} — 45, 35 e 20, cada um com sua condição (fonte: `docs/specs/engine-routing/spec.md:44`)
- [x] CHK006 - O limiar que põe o MinerU em primeiro está quantificado? [Clareza, Spec §FR-003] {auto} — pontuação de fórmula de pelo menos 12 (fonte: `docs/specs/engine-routing/spec.md:41`)
- [ ] CHK007 - Está definido o que significa "o PDF tiver texto" em FR-002? [Ambiguity, Spec §FR-002] {auto} — a relação com "totalmente escaneado" de FR-004 fica implícita (fonte: `docs/specs/engine-routing/spec.md:40`)
- [ ] CHK008 - Está definido o desempate entre resultados com a mesma nota? [Ambiguity, Spec §FR-011] {auto} — FR-011 só diz "o de maior nota" (fonte: `docs/specs/engine-routing/spec.md:49`)

## Consistencia de Requisitos

- [x] CHK009 - A ordem de motores de FR-002 é consistente com os cenários de aceite? [Consistência, Spec §FR-002] {auto} — o cenário com todos os motores lista docling, pymupdf, mineru, markitdown (fonte: `docs/specs/engine-routing/spec.md:21`)

## Qualidade de Criterios de Aceite

- [ ] CHK010 - Há critério de sucesso mensurável para a capacidade? [Gap, Spec §SC-001] {auto} — SC-001 é um campo pendente (fonte: `docs/specs/engine-routing/spec.md:70`)
- [ ] CHK011 - A prioridade da user story reflete o valor da capacidade para o produto? [Prioridade, Spec §User Story 1] {humano}
- [ ] CHK012 - Os pesos dos descontos e a nota de corte refletem o que o produto considera uma conversão aceitável? [Risco, Spec §FR-006, Spec §FR-009] {humano}

## Cobertura de Cenarios

- [x] CHK013 - Cada requisito funcional é citado em ao menos um cenário ou edge case? [Cobertura, Spec §Acceptance Scenarios] {auto} — gate requirement-coverage: requirements=16, covered=16, errors=0
- [ ] CHK014 - Cada requisito funcional tem cenário com teste existente como origem? [Gap, Spec §FR-007, Spec §FR-008, Spec §FR-012, Spec §FR-013, Spec §FR-014, Spec §FR-015] {auto} — esses requisitos só aparecem em cenários ou edge cases pendentes (fonte: `docs/specs/engine-routing/spec.md:27`)

## Requisitos Nao-Funcionais

- [ ] CHK015 - Há requisito de tempo total de conversão quando vários motores são tentados? [Gap] {auto} — a spec não traz requisito de desempenho

## Dependencias e Premissas

- [x] CHK016 - A dependência do resultado da inspeção do PDF está documentada? [Dependência, Spec §User Story 1] {auto} — a descrição da user story parte da inspeção do PDF (fonte: `docs/specs/engine-routing/spec.md:15`)

## Notes

- Items `{auto}` ja vem resolvidos pelo agente (`[x]` com citacao, ou marcador `[Gap]`/`[Ambiguity]`)
- Items `{humano}` ficam `[ ]` aguardando decisao do dono do produto
- Marcar items concluidos com `[x]`
- Items numerados sequencialmente para referencia
