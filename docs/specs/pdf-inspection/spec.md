# Feature Specification: Inspeção do PDF

<!-- cstk-init:reconstructed -->
> **Origem**: spec reconstituida a partir do projeto existente por `cstk-init` em
> 2026-11-20. Nao foi escrita antes da implementacao; descreve comportamento observado.

**Feature**: `pdf-inspection`
**Created**: 2026-11-20
**Status**: Draft

## User Scenarios & Testing

### User Story 1 - Inspecionar o PDF antes de converter (Priority: [PENDENTE: qual a prioridade desta capacidade?])

Antes da conversão, o artigo2md lê o texto nativo de cada página do PDF e resume o documento: quantas páginas têm texto, quantas parecem escaneadas, quantas citam tabelas e quantos indícios de fórmula e de artigo científico aparecem. Esse resumo orienta a escolha do motor.

**Acceptance Scenarios**:

1. **Given** um PDF de 2 páginas com texto nativo e uma citação de tabela numerada, **When** o PDF é inspecionado, **Then** a análise informa 2 páginas com texto, nenhuma escaneada, 1 página com citação de tabela e pontuação de artigo maior que zero (fonte: `tests/test_core.py:9`) (cobre FR-001, FR-002, FR-003, FR-005)
2. **Given** um PDF de uma página em branco, **When** o PDF é inspecionado, **Then** a análise o marca como totalmente escaneado e com necessidade de OCR (fonte: `tests/test_core.py:17`) (cobre FR-002, FR-006)
3. **Given** um PDF com símbolos matemáticos, **When** o PDF é inspecionado, **Then** [PENDENTE: qual teste cobre a soma de indícios de fórmula (FR-004)?]

### Edge Cases

- [PENDENTE: o que acontece quando o arquivo não é um PDF válido ou está protegido por senha (FR-001)?]

## Requirements

### Functional Requirements

- **FR-001**: O sistema lê o texto nativo de cada página do PDF com pypdfium2 e acumula o total de páginas e de caracteres. (fonte: `src/artigo2md/pdfscan.py:59`)
- **FR-002**: O sistema trata como escaneada a página com menos de 80 caracteres de texto nativo e conta separadamente páginas com texto e páginas escaneadas. (fonte: `src/artigo2md/pdfscan.py:21`)
- **FR-003**: O sistema conta as páginas que citam tabelas pelos termos "table", "tabela" ou "quadro" seguidos de número. (fonte: `src/artigo2md/pdfscan.py:18`)
- **FR-004**: O sistema soma indícios de fórmula: símbolos matemáticos e os termos "equation" e "equação". (fonte: `src/artigo2md/pdfscan.py:19`)
- **FR-005**: O sistema soma ocorrências de termos típicos de artigo científico, em inglês e português, numa pontuação de artigo. (fonte: `src/artigo2md/pdfscan.py:13`)
- **FR-006**: O sistema marca o PDF como totalmente escaneado quando todas as páginas são escaneadas e como necessitando de OCR quando ao menos uma é. (fonte: `src/artigo2md/pdfscan.py:45`)

### Inferido (nao confirmado)

- **INF-001**: A inspeção parece não depender de rede; o módulo importa apenas pypdfium2 para ler o arquivo local.

### Key Entities

- **Analysis**: resumo do PDF (páginas, páginas com texto, páginas escaneadas, total de caracteres, citações de tabela, pontuação de fórmula, pontuação de artigo). (fonte: `src/artigo2md/pdfscan.py:34`)
- **PageInfo**: dados por página (número, caracteres, texto nativo, citações de tabela, indícios de fórmula). (fonte: `src/artigo2md/pdfscan.py:25`)

## Success Criteria

### Measurable Outcomes

- **SC-001**: [PENDENTE: qual a meta mensuravel, com fonte, para esta capacidade?]

## Delta Requirements

**Skip**: spec reconstituida de comportamento ja existente; nao altera comportamento documentado — cstk-init, 2026-11-20
