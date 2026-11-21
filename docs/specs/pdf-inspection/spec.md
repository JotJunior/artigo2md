# Feature Specification: Inspeção do PDF

<!-- cstk-init:reconstructed -->
> **Origem**: spec reconstituida a partir do projeto existente por `cstk-init` em
> 2026-11-20. Nao foi escrita antes da implementacao; descreve comportamento observado.

**Feature**: `pdf-inspection`
**Created**: 2026-11-20
**Status**: Draft

## Clarifications

### Session 2026-11-20

- Q: Qual a prioridade da User Story 1? → A: P1 (informado pelo operador)
- Q: O que a inspeção faz com PDF inválido ou protegido por senha? → A: Falha com mensagem clara (informado pelo operador)
- Q: O processamento sem rede da inspeção vira requisito? → A: Sim, promovido a requisito (informado pelo operador)
- Q: Qual a meta mensurável da capacidade? → A: Cobertura por testes automatizados (informado pelo operador)

## User Scenarios & Testing

### User Story 1 - Inspecionar o PDF antes de converter (Priority: P1) (informado pelo operador)

Antes da conversão, o artigo2md lê o texto nativo de cada página do PDF e resume o documento: quantas páginas têm texto, quantas parecem escaneadas, quantas citam tabelas e quantos indícios de fórmula e de artigo científico aparecem. Esse resumo orienta a escolha do motor.

**Acceptance Scenarios**:

1. **Given** um PDF de 2 páginas com texto nativo e uma citação de tabela numerada, **When** o PDF é inspecionado, **Then** a análise informa 2 páginas com texto, nenhuma escaneada, 1 página com citação de tabela e pontuação de artigo maior que zero (fonte: `tests/test_core.py:9`) (cobre FR-001, FR-002, FR-003, FR-005)
2. **Given** um PDF de uma página em branco, **When** o PDF é inspecionado, **Then** a análise o marca como totalmente escaneado e com necessidade de OCR (fonte: `tests/test_core.py:17`) (cobre FR-002, FR-006)
3. **Given** um PDF com símbolos matemáticos, **When** o PDF é inspecionado, **Then** [PENDENTE: qual teste cobre a soma de indícios de fórmula (FR-004)?]

### Edge Cases

- Quando o arquivo não é um PDF válido ou está protegido por senha, a inspeção falha com mensagem clara em português, sem acionar os motores (informado pelo operador) (fonte: `tests/test_core.py:22`) (fonte: `tests/test_core.py:28`) (fonte: `tests/test_core.py:44`) (cobre FR-007)
- Quando a biblioteca de leitura devolve um erro sem código conhecido, a inspeção falha com uma mensagem genérica em português que inclui o texto original (fonte: `tests/test_core.py:34`) (cobre FR-007)
- Quando um PDF protegido por senha é enviado pela interface web, o job termina em erro com a mesma mensagem em português (fonte: `tests/test_web.py:49`) (cobre FR-007)
- [PENDENTE: qual teste comprova que a inspeção não acessa a rede (FR-008)?]

## Requirements

### Functional Requirements

- **FR-001**: O sistema lê o texto nativo de cada página do PDF com pypdfium2 e acumula o total de páginas e de caracteres. (fonte: `src/artigo2md/pdfscan.py:84`)
- **FR-002**: O sistema trata como escaneada a página com menos de 80 caracteres de texto nativo e conta separadamente páginas com texto e páginas escaneadas. (fonte: `src/artigo2md/pdfscan.py:21`)
- **FR-003**: O sistema conta as páginas que citam tabelas pelos termos "table", "tabela" ou "quadro" seguidos de número. (fonte: `src/artigo2md/pdfscan.py:18`)
- **FR-004**: O sistema soma indícios de fórmula: símbolos matemáticos e os termos "equation" e "equação". (fonte: `src/artigo2md/pdfscan.py:19`)
- **FR-005**: O sistema soma ocorrências de termos típicos de artigo científico, em inglês e português, numa pontuação de artigo. (fonte: `src/artigo2md/pdfscan.py:13`)
- **FR-006**: O sistema marca o PDF como totalmente escaneado quando todas as páginas são escaneadas e como necessitando de OCR quando ao menos uma é. (fonte: `src/artigo2md/pdfscan.py:70`)
- **FR-007**: O sistema falha com mensagem clara em português quando o arquivo não é um PDF válido ou está protegido por senha; os demais erros de abertura recebem uma mensagem genérica em português. (informado pelo operador) (fonte: `src/artigo2md/pdfscan.py:32`)
- **FR-008**: O sistema inspeciona apenas o arquivo local e não acessa a rede durante a inspeção. (informado pelo operador) (fonte: `src/artigo2md/pdfscan.py:3`)

### Inferido (nao confirmado)

- Nenhuma inferência em aberto: a antiga INF-001 foi promovida a FR-008 na clarificação.

### Key Entities

- **Analysis**: resumo do PDF (páginas, páginas com texto, páginas escaneadas, total de caracteres, citações de tabela, pontuação de fórmula, pontuação de artigo). (fonte: `src/artigo2md/pdfscan.py:59`)
- **PageInfo**: dados por página (número, caracteres, texto nativo, citações de tabela, indícios de fórmula). (fonte: `src/artigo2md/pdfscan.py:50`)

## Success Criteria

### Measurable Outcomes

- **SC-001**: Todo requisito funcional desta spec tem ao menos um teste automatizado que passa. (informado pelo operador)

## Delta Requirements

**Skip**: spec reconstituida de comportamento ja existente; nao altera comportamento documentado — cstk-init, 2026-11-20
