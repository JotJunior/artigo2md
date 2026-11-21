# Feature Specification: Roteamento de motores e avaliação de qualidade

<!-- cstk-init:reconstructed -->
> **Origem**: spec reconstituida a partir do projeto existente por `cstk-init` em
> 2026-11-20. Nao foi escrita antes da implementacao; descreve comportamento observado.

**Feature**: `engine-routing`
**Created**: 2026-11-20
**Status**: Draft

## Clarifications

### Session 2026-11-20

- Q: Qual a prioridade da User Story 1? → A: P1 (informado pelo operador)
- Q: O que acontece quando o motor pedido explicitamente não está instalado? → A: Falha com instrução de instalação (informado pelo operador)
- Q: Os descontos da nota e o corte de aceite (FR-006 a FR-009) refletem uma conversão aceitável? → A: Sim, manter os valores (informado pelo operador)
- Q: Qual a meta mensurável da capacidade? → A: Cobertura por testes automatizados (informado pelo operador)

## User Scenarios & Testing

### User Story 1 - Converter com escolha automática de motor e fallback (Priority: P1) (informado pelo operador)

A partir da inspeção do PDF, o artigo2md monta a ordem de motores a tentar, converte, dá uma nota de qualidade ao resultado e, quando a nota não é aceita ou o motor falha, tenta o próximo, ficando com o resultado de maior nota.

**Acceptance Scenarios**:

1. **Given** um PDF totalmente escaneado, pymupdf4llm instalado e MinerU ausente, **When** o plano automático é montado, **Then** ele contém apenas docling (fonte: `tests/test_core.py:22`) (cobre FR-002, FR-004)
2. **Given** um PDF com texto, pymupdf4llm instalado e MinerU ausente, **When** o plano automático é montado, **Then** a ordem é docling, pymupdf, markitdown; e, pedindo markitdown explicitamente, o plano contém só markitdown (fonte: `tests/test_core.py:22`) (cobre FR-001, FR-002)
3. **Given** um PDF com texto e todos os motores disponíveis, **When** o plano automático é montado, **Then** a ordem é docling, pymupdf, mineru, markitdown (fonte: `tests/test_core.py:32`) (cobre FR-002)
4. **Given** um PDF com texto e nenhum motor opcional, **When** o plano automático é montado, **Then** a ordem é docling, markitdown (fonte: `tests/test_core.py:39`) (cobre FR-002)
5. **Given** um PDF com pontuação de fórmula alta e MinerU disponível, **When** o plano automático é montado, **Then** mineru é o primeiro motor (fonte: `tests/test_core.py:59`) (cobre FR-003)
6. **Given** um Markdown com duas tabelas em pipes, um texto com duas tags de tabela HTML e um texto só com linhas horizontais, **When** as tabelas são contadas, **Then** os resultados são 2, 2 e 0 (fonte: `tests/test_core.py:65`) (cobre FR-005)
7. **Given** uma análise que cita tabela em 1 página, **When** uma saída sem tabelas e outra com 1 tabela são avaliadas, **Then** a primeira não é aceita e a segunda é (fonte: `tests/test_core.py:73`) (cobre FR-006, FR-009)
8. **Given** o Docling falhando com erro e nenhum motor opcional, **When** a conversão automática roda, **Then** o resultado vem do markitdown e a primeira tentativa registra o erro (fonte: `tests/test_core.py:86`) (cobre FR-010, FR-011, FR-016)
9. **Given** uma saída muito curta ou com menos da metade do texto nativo, **When** ela é avaliada, **Then** [PENDENTE: qual teste cobre os descontos de FR-007 e FR-008?]
10. **Given** um PDF com páginas escaneadas, **When** o Docling é chamado, **Then** [PENDENTE: qual teste cobre o OCR ligado (FR-014) e o aviso de progresso (FR-015)?]

### Edge Cases

- [PENDENTE: qual teste cobre a recusa de motor fora da lista no roteador (FR-012)?]
- [PENDENTE: qual teste cobre a falha quando nenhum motor produz saída (FR-013)?]
- [PENDENTE: qual teste cobre a falha com instrução de instalação quando o motor pedido não está instalado (FR-017)?]

## Requirements

### Functional Requirements

- **FR-001**: Quando um motor específico é solicitado, o plano contém apenas esse motor. (fonte: `src/artigo2md/router.py:90`)
- **FR-002**: No modo automático, o sistema tenta o Docling e, em seguida, o pymupdf4llm (se instalado e o PDF tiver texto), o MinerU (se disponível) e o MarkItDown (se o PDF tiver texto). (fonte: `src/artigo2md/router.py:88`)
- **FR-003**: No modo automático, o MinerU vai primeiro quando a pontuação de fórmula do PDF é de pelo menos 12 e o MinerU está disponível. (fonte: `src/artigo2md/router.py:15`)
- **FR-004**: Em PDF totalmente escaneado, o pymupdf4llm e o MarkItDown ficam fora do plano. (fonte: `src/artigo2md/router.py:92`)
- **FR-005**: O sistema conta tabelas em Markdown (linha separadora com pipes) e em HTML (tag de tabela). (fonte: `src/artigo2md/router.py:57`)
- **FR-006**: A nota parte de 100 e perde 45 quando o PDF cita tabelas e nenhuma foi estruturada. (fonte: `src/artigo2md/router.py:68`)
- **FR-007**: A nota perde 35 quando o Markdown tem menos de 100 caracteres por página. (fonte: `src/artigo2md/router.py:71`)
- **FR-008**: A nota perde 20 quando a saída tem menos da metade do texto nativo do PDF. (fonte: `src/artigo2md/router.py:74`)
- **FR-009**: O resultado é aceito quando a nota é de pelo menos 65. (fonte: `src/artigo2md/router.py:14`)
- **FR-010**: Quando um motor falha com erro, o sistema registra o erro na tentativa e segue para o próximo motor. (fonte: `src/artigo2md/router.py:137`)
- **FR-011**: O sistema para no primeiro resultado aceito; sem resultado aceito, fica com o de maior nota. (fonte: `src/artigo2md/router.py:142`)
- **FR-012**: O sistema recusa motor fora da lista auto, docling, pymupdf, mineru, markitdown. (fonte: `src/artigo2md/router.py:123`)
- **FR-013**: Quando nenhum motor produz saída, a conversão falha com uma mensagem que reúne os erros de cada tentativa. (fonte: `src/artigo2md/router.py:148`)
- **FR-014**: O Docling é chamado com OCR ligado quando a análise indica páginas escaneadas. (fonte: `src/artigo2md/router.py:108`)
- **FR-015**: O sistema informa o progresso (inspeção, motor em uso, reprovação na avaliação) por uma função de aviso opcional. (fonte: `src/artigo2md/router.py:126`)
- **FR-016**: O resultado traz o Markdown, o motor usado, a análise do PDF e a lista de tentativas com tempo, qualidade ou erro. (fonte: `src/artigo2md/router.py:41`)
- **FR-017**: Quando o motor solicitado explicitamente não está instalado, a conversão falha com a instrução de instalação desse motor e o sistema não troca para o modo automático. (informado pelo operador) (fonte: `src/artigo2md/router.py:148`)

### Inferido (nao confirmado)

- **INF-001**: O pymupdf4llm parece vir antes do MinerU na ordem de reserva por ser rápido de tentar, segundo comentário no código.

### Key Entities

- **Quality**: nota, aceite, tabelas encontradas e esperadas, caracteres por página e lista de problemas. (fonte: `src/artigo2md/router.py:23`)
- **Attempt**: motor, segundos, qualidade ou erro. (fonte: `src/artigo2md/router.py:33`)
- **ConversionResult**: origem, Markdown, motor, análise e tentativas. (fonte: `src/artigo2md/router.py:41`)

## Success Criteria

### Measurable Outcomes

- **SC-001**: Todo requisito funcional desta spec tem ao menos um teste automatizado que passa. (informado pelo operador)

## Delta Requirements

**Skip**: spec reconstituida de comportamento ja existente; nao altera comportamento documentado — cstk-init, 2026-11-20
