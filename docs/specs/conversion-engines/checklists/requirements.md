# Requirements Checklist: Motores de conversão

**Purpose**: Validar a qualidade dos requisitos da spec reconstituída `conversion-engines` (completude, clareza, consistência e cobertura), não a implementação.
**Created**: 2026-11-20
**Feature**: [spec.md](../spec.md)

## Completude de Requisitos

- [x] CHK001 - Cada requisito funcional observado cita a origem no código? [Completude, Spec §Functional Requirements] {auto} — FR-001 a FR-012 têm origem citada (fonte: `docs/specs/conversion-engines/spec.md:34`)
- [ ] CHK002 - Está definido um limite de tempo para a execução do MinerU como processo externo? [Gap, Spec §FR-008] {auto} — FR-008 não menciona tempo limite (fonte: `docs/specs/conversion-engines/spec.md:41`)
- [ ] CHK003 - Está definido o comportamento quando um motor obrigatório não está instalado? [Gap, Spec §FR-010] {auto} — FR-010 só informa a disponibilidade (fonte: `docs/specs/conversion-engines/spec.md:43`)

## Clareza de Requisitos

- [x] CHK004 - A versão aceita do MinerU está especificada? [Clareza, Spec §FR-007] {auto} — só a linha 3.x (fonte: `docs/specs/conversion-engines/spec.md:40`)
- [x] CHK005 - Os idiomas do OCR no macOS estão especificados? [Clareza, Spec §FR-002] {auto} — pt-BR e en-US (fonte: `docs/specs/conversion-engines/spec.md:35`)
- [ ] CHK006 - Está definido o que é o "modo preciso" da estrutura de tabelas? [Ambiguity, Spec §FR-001] {auto} — o termo aparece sem definição (fonte: `docs/specs/conversion-engines/spec.md:34`)
- [ ] CHK007 - Está quantificado quanto do "final da saída do processo" entra na mensagem de falha? [Ambiguity, Spec §FR-009] {auto} — sem tamanho definido (fonte: `docs/specs/conversion-engines/spec.md:42`)

## Consistencia de Requisitos

- [x] CHK008 - Os campos da ficha de motor em FR-011 são consistentes com o cenário que a consulta? [Consistência, Spec §FR-011] {auto} — o cenário usa licença, obrigatoriedade e instalação, todos listados em FR-011 (fonte: `docs/specs/conversion-engines/spec.md:19`)

## Qualidade de Criterios de Aceite

- [ ] CHK009 - Há critério de sucesso mensurável para a capacidade? [Gap, Spec §SC-001] {auto} — SC-001 é um campo pendente (fonte: `docs/specs/conversion-engines/spec.md:59`)
- [ ] CHK010 - A prioridade da user story reflete o valor da capacidade para o produto? [Prioridade, Spec §User Story 1] {humano}

## Cobertura de Cenarios

- [x] CHK011 - Cada requisito funcional é citado em ao menos um cenário ou edge case? [Cobertura, Spec §Acceptance Scenarios] {auto} — gate requirement-coverage: requirements=12, covered=12, errors=0
- [ ] CHK012 - Cada requisito funcional tem cenário com teste existente como origem? [Gap, Spec §FR-002, Spec §FR-005, Spec §FR-006, Spec §FR-007, Spec §FR-008, Spec §FR-009, Spec §FR-012] {auto} — esses requisitos só aparecem em cenários ou edge cases pendentes (fonte: `docs/specs/conversion-engines/spec.md:23`)

## Requisitos Nao-Funcionais

- [ ] CHK013 - Há requisito sobre telemetria ou acesso à rede dos demais motores, além do MinerU? [Gap, Spec §FR-008] {auto} — só FR-008 trata de telemetria, e apenas do MinerU (fonte: `docs/specs/conversion-engines/spec.md:41`)
- [ ] CHK014 - Aceitar só a linha 3.x do MinerU continua adequado ao risco de licença e telemetria aceito pelo produto? [Risco, Spec §FR-007] {humano}

## Dependencias e Premissas

- [x] CHK015 - A forma de localizar o MinerU está documentada? [Dependência, Spec §FR-006] {auto} — comando no PATH ou variável de ambiente ARTIGO2MD_MINERU (fonte: `docs/specs/conversion-engines/spec.md:39`)

## Notes

- Items `{auto}` ja vem resolvidos pelo agente (`[x]` com citacao, ou marcador `[Gap]`/`[Ambiguity]`)
- Items `{humano}` ficam `[ ]` aguardando decisao do dono do produto
- Marcar items concluidos com `[x]`
- Items numerados sequencialmente para referencia
