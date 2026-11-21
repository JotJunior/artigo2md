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

### Session 2026-11-21

- Q: O que acontece quando um motor obrigatório não está instalado? → A: Manter o comportamento atual (informado pelo operador)
- Q: O download de modelos do Docling deve ser proibido depois do cache? → A: Não, manter como está (informado pelo operador)

## User Scenarios & Testing

### User Story 1 - Converter com cada motor e saber quais estão disponíveis (Priority: P1) (informado pelo operador)

O artigo2md tem um adaptador para cada motor de conversão (Docling, MarkItDown, pymupdf4llm e MinerU), detecta quais estão instalados e mantém uma ficha de cada um, usada na interface e no diagnóstico.

**Acceptance Scenarios**:

1. **Given** a ficha dos motores, **When** ela é consultada, **Then** lista quatro motores na ordem docling, markitdown, pymupdf, mineru; os não obrigatórios têm instrução de instalação e os obrigatórios têm licença MIT (fonte: `tests/test_core.py:75`) (cobre FR-010, FR-011)
2. **Given** o pymupdf4llm instalado e um artigo de teste, **When** a conversão roda com o motor pymupdf, **Then** o resultado vem desse motor e contém o texto do artigo (fonte: `tests/test_core.py:83`) (cobre FR-004)
3. **Given** um artigo de teste, **When** a conversão roda com o motor markitdown, **Then** o resultado vem desse motor e contém o texto do artigo (fonte: `tests/test_core.py:110`) (cobre FR-003)
4. **Given** o teste lento habilitado e um artigo de teste com tabela, **When** a conversão roda com o motor docling, **Then** o resultado vem do Docling, contém o texto do artigo e ao menos uma tabela (fonte: `tests/test_core.py:130`) (cobre FR-001)
5. **Given** o OCR pedido no macOS e em outro sistema, **When** as opções de OCR são montadas, **Then** no macOS vem o OCR nativo da Apple com pt-BR e en-US e nos demais a opção automática (fonte: `tests/test_engines.py:170`) (fonte: `tests/test_engines.py:181`) (cobre FR-002)
6. **Given** o conversor do Docling pedido mais de uma vez, **When** o modo é o mesmo, **Then** o mesmo conversor é devolvido, e um modo diferente recebe outro (fonte: `tests/test_engines.py:189`) (cobre FR-012)
7. **Given** o MinerU simulado no PATH ou indicado pela variável de ambiente, **When** ele é localizado, **Then** o caminho encontrado é usado, desde que seja executável (fonte: `tests/test_engines.py:83`) (fonte: `tests/test_engines.py:87`) (cobre FR-006)
8. **Given** o MinerU simulado respondendo uma versão da linha 3.x, outra linha ou um texto sem versão, **When** o estado é consultado, **Then** só a linha 3.x é utilizável e as demais trazem o detalhe do problema (fonte: `tests/test_engines.py:105`) (fonte: `tests/test_engines.py:110`) (fonte: `tests/test_engines.py:120`) (cobre FR-007)
9. **Given** o MinerU simulado gerando dois arquivos Markdown, **When** a conversão roda, **Then** o processo recebe a telemetria desativada e o Markdown devolvido é o do maior arquivo (fonte: `tests/test_engines.py:129`) (cobre FR-008)
10. **Given** as opções do Docling montadas pelo adaptador e o módulo de motores importado, **When** elas são inspecionadas, **Then** os serviços remotos estão desligados de forma explícita e a telemetria do hub de modelos está desativada (fonte: `tests/test_engines.py:17`) (fonte: `tests/test_engines.py:25`) (cobre FR-013)
11. **Given** a criação de sockets bloqueada, **When** o MarkItDown e o pymupdf4llm convertem um artigo de teste, **Then** as conversões terminam normalmente; o teste cobre conexões abertas pelo Python, não acesso à rede por código nativo (fonte: `tests/test_engines.py:38`) (fonte: `tests/test_engines.py:44`) (cobre FR-013)

### Edge Cases

- Sem o pymupdf4llm instalado, a conversão com esse motor sinaliza motor indisponível com a instrução de instalação (fonte: `tests/test_engines.py:162`) (cobre FR-005)
- Quando o MinerU termina com erro ou não gera Markdown, a conversão falha com o final da saída do processo; com versão incompatível, nenhuma conversão é disparada (fonte: `tests/test_engines.py:139`) (fonte: `tests/test_engines.py:146`) (fonte: `tests/test_engines.py:153`) (cobre FR-009, FR-007)
- Quando um motor obrigatório não está instalado, o diagnóstico acusa e falha, e a conversão segue para o próximo motor (fonte: `tests/test_cli.py:311`) (fonte: `tests/test_core.py:116`) (cobre FR-014)
- [PENDENTE: qual teste comprova que a conversão real com o Docling não envia conteúdo a serviços externos (FR-013)?]

## Requirements

### Functional Requirements

- **FR-001**: O sistema converte com o Docling com estrutura de tabelas no modo preciso (o modo de maior precisão do reconhecimento de tabelas do Docling, com correspondência de células) e devolve o Markdown e o número de tabelas. (fonte: `src/artigo2md/engines.py:100`) (fonte: `src/artigo2md/engines.py:60`)
- **FR-002**: Quando o OCR é pedido, o sistema usa no macOS o OCR nativo da Apple com os idiomas pt-BR e en-US; nos demais sistemas usa a opção automática do Docling ou, na falta dela, o EasyOCR. (fonte: `src/artigo2md/engines.py:89`)
- **FR-003**: O sistema converte com o MarkItDown com plugins desligados. (fonte: `src/artigo2md/engines.py:118`)
- **FR-004**: O sistema converte com o pymupdf4llm quando ele está instalado. (fonte: `src/artigo2md/engines.py:139`)
- **FR-005**: Sem o pymupdf4llm instalado, o sistema sinaliza motor indisponível com a instrução de instalação. (fonte: `src/artigo2md/engines.py:122`)
- **FR-006**: O sistema localiza o MinerU pelo comando no PATH ou pelo caminho indicado na variável de ambiente ARTIGO2MD_MINERU. (fonte: `src/artigo2md/engines.py:150`)
- **FR-007**: O sistema só considera o MinerU utilizável na linha 3.x; outra versão é reportada como incompatível. (fonte: `src/artigo2md/engines.py:176`)
- **FR-008**: O sistema executa o MinerU como processo externo com a telemetria desativada pela variável MINERU_TELEMETRY_DISABLED e usa o maior arquivo Markdown gerado; cada execução tem limite de 1800 segundos. (fonte: `src/artigo2md/engines.py:195`) (fonte: `src/artigo2md/engines.py:189`)
- **FR-009**: Quando o MinerU termina com erro ou não gera Markdown, o sistema sinaliza falha com os últimos 600 caracteres da saída do processo. (fonte: `src/artigo2md/engines.py:202`)
- **FR-010**: O sistema informa quais dos quatro motores estão disponíveis na máquina. (fonte: `src/artigo2md/engines.py:207`)
- **FR-011**: O sistema mantém a ficha de cada motor (identificador, nome, licença, obrigatoriedade, papel, peso, instalação) com o estado de instalação e o detalhe de versão incompatível do MinerU. (fonte: `src/artigo2md/engines.py:240`)
- **FR-012**: O conversor do Docling é criado uma vez por modo (com ou sem OCR) e reutilizado. (fonte: `src/artigo2md/engines.py:72`)
- **FR-013**: Nenhum motor envia o documento ou conteúdo dele a serviços externos, e a telemetria é desativada sempre que o motor oferece essa opção: os serviços remotos do Docling ficam desligados de forma explícita e a telemetria do hub de modelos é desativada no processo; o download de modelos do Docling na primeira conversão continua permitido. (informado pelo operador) (fonte: `src/artigo2md/engines.py:68`) (fonte: `src/artigo2md/engines.py:26`)
- **FR-014**: Quando um motor obrigatório não está instalado, o diagnóstico o mostra como ausente e termina com código 1, e na conversão ele conta como tentativa com erro e o próximo motor é tentado. (informado pelo operador) (fonte: `src/artigo2md/cli.py:133`) (fonte: `src/artigo2md/router.py:137`)

### Inferido (nao confirmado)

- **INF-001**: A verificação de versão do MinerU parece ficar em memória por executável durante a vida do processo.

### Key Entities

- **EngineOutput**: motor, Markdown, número de tabelas (quando o motor informa) e dados extras. (fonte: `src/artigo2md/engines.py:30`)

## Success Criteria

### Measurable Outcomes

- **SC-001**: Todo requisito funcional desta spec tem ao menos um teste automatizado que passa. (informado pelo operador)

## Delta Requirements

**Skip**: spec reconstituida de comportamento ja existente; nao altera comportamento documentado — cstk-init, 2026-11-20
