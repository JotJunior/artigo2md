# Feature Specification: Motores de conversão

<!-- cstk-init:reconstructed -->
> **Origem**: spec reconstituida a partir do projeto existente por `cstk-init` em
> 2026-11-20. Nao foi escrita antes da implementacao; descreve comportamento observado.

**Feature**: `conversion-engines`
**Created**: 2026-11-20
**Status**: Draft

## Clarifications

### Session 2026-11-20

- Q: Qual a prioridade da User Story 1? → A: P1 (informado pelo operador)
- Q: Qual requisito de rede e telemetria vale para os motores além do MinerU? → A: Exigir de todos os motores (informado pelo operador)
- Q: A política de aceitar o MinerU só na linha 3.x (FR-007) continua valendo? → A: Sim, só 3.x (informado pelo operador)
- Q: Qual a meta mensurável da capacidade? → A: Cobertura por testes automatizados (informado pelo operador)

## User Scenarios & Testing

### User Story 1 - Converter com cada motor e saber quais estão disponíveis (Priority: P1) (informado pelo operador)

O artigo2md tem um adaptador para cada motor de conversão (Docling, MarkItDown, pymupdf4llm e MinerU), detecta quais estão instalados e mantém uma ficha de cada um, usada na interface e no diagnóstico.

**Acceptance Scenarios**:

1. **Given** a ficha dos motores, **When** ela é consultada, **Then** lista quatro motores na ordem docling, markitdown, pymupdf, mineru; os não obrigatórios têm instrução de instalação e os obrigatórios têm licença MIT (fonte: `tests/test_core.py:45`) (cobre FR-010, FR-011)
2. **Given** o pymupdf4llm instalado e um artigo de teste, **When** a conversão roda com o motor pymupdf, **Then** o resultado vem desse motor e contém o texto do artigo (fonte: `tests/test_core.py:53`) (cobre FR-004)
3. **Given** um artigo de teste, **When** a conversão roda com o motor markitdown, **Then** o resultado vem desse motor e contém o texto do artigo (fonte: `tests/test_core.py:80`) (cobre FR-003)
4. **Given** o teste lento habilitado e um artigo de teste com tabela, **When** a conversão roda com o motor docling, **Then** o resultado vem do Docling, contém o texto do artigo e ao menos uma tabela (fonte: `tests/test_core.py:100`) (cobre FR-001)
5. **Given** um PDF com páginas escaneadas, **When** o Docling roda com OCR, **Then** [PENDENTE: qual teste cobre a escolha do OCR (FR-002) e o reuso do conversor (FR-012)?]
6. **Given** o MinerU instalado, **When** ele é localizado, verificado e executado, **Then** [PENDENTE: qual teste cobre FR-006, FR-007, FR-008 e FR-009?]

### Edge Cases

- [PENDENTE: qual teste cobre o erro de motor indisponível quando o pymupdf4llm não está instalado (FR-005)?]
- [PENDENTE: qual teste ou verificação comprova que Docling, MarkItDown e pymupdf4llm não enviam conteúdo nem telemetria (FR-013)?]

## Requirements

### Functional Requirements

- **FR-001**: O sistema converte com o Docling com estrutura de tabelas no modo preciso e devolve o Markdown e o número de tabelas. (fonte: `src/artigo2md/engines.py:78`)
- **FR-002**: Quando o OCR é pedido, o sistema usa no macOS o OCR nativo da Apple com os idiomas pt-BR e en-US; nos demais sistemas usa a opção automática do Docling ou, na falta dela, o EasyOCR. (fonte: `src/artigo2md/engines.py:67`)
- **FR-003**: O sistema converte com o MarkItDown com plugins desligados. (fonte: `src/artigo2md/engines.py:90`)
- **FR-004**: O sistema converte com o pymupdf4llm quando ele está instalado. (fonte: `src/artigo2md/engines.py:106`)
- **FR-005**: Sem o pymupdf4llm instalado, o sistema sinaliza motor indisponível com a instrução de instalação. (fonte: `src/artigo2md/engines.py:110`)
- **FR-006**: O sistema localiza o MinerU pelo comando no PATH ou pelo caminho indicado na variável de ambiente ARTIGO2MD_MINERU. (fonte: `src/artigo2md/engines.py:117`)
- **FR-007**: O sistema só considera o MinerU utilizável na linha 3.x; outra versão é reportada como incompatível. (fonte: `src/artigo2md/engines.py:143`)
- **FR-008**: O sistema executa o MinerU como processo externo com a telemetria desativada pela variável MINERU_TELEMETRY_DISABLED e usa o maior arquivo Markdown gerado. (fonte: `src/artigo2md/engines.py:160`)
- **FR-009**: Quando o MinerU termina com erro ou não gera Markdown, o sistema sinaliza falha com o final da saída do processo. (fonte: `src/artigo2md/engines.py:167`)
- **FR-010**: O sistema informa quais dos quatro motores estão disponíveis na máquina. (fonte: `src/artigo2md/engines.py:172`)
- **FR-011**: O sistema mantém a ficha de cada motor (identificador, nome, licença, obrigatoriedade, papel, peso, instalação) com o estado de instalação e o detalhe de versão incompatível do MinerU. (fonte: `src/artigo2md/engines.py:205`)
- **FR-012**: O conversor do Docling é criado uma vez por modo (com ou sem OCR) e reutilizado. (fonte: `src/artigo2md/engines.py:41`)
- **FR-013**: Nenhum motor envia o documento ou conteúdo dele a serviços externos, e a telemetria é desativada sempre que o motor oferece essa opção; requisito definido na clarificação, hoje observado no código apenas para o MinerU. (informado pelo operador)

### Inferido (nao confirmado)

- **INF-001**: A verificação de versão do MinerU parece ficar em memória por executável durante a vida do processo.

### Key Entities

- **EngineOutput**: motor, Markdown, número de tabelas (quando o motor informa) e dados extras. (fonte: `src/artigo2md/engines.py:25`)

## Success Criteria

### Measurable Outcomes

- **SC-001**: Todo requisito funcional desta spec tem ao menos um teste automatizado que passa. (informado pelo operador)

## Delta Requirements

**Skip**: spec reconstituida de comportamento ja existente; nao altera comportamento documentado — cstk-init, 2026-11-20
