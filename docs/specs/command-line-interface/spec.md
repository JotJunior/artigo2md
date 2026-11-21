# Feature Specification: Linha de comando

<!-- cstk-init:reconstructed -->
> **Origem**: spec reconstituida a partir do projeto existente por `cstk-init` em
> 2026-11-20. Nao foi escrita antes da implementacao; descreve comportamento observado.

**Feature**: `command-line-interface`
**Created**: 2026-11-20
**Status**: Draft

## Clarifications

### Session 2026-11-20

- Q: Qual a prioridade da User Story 1? → A: P1 (informado pelo operador)
- Q: O que o convert faz com PDFs de mesmo nome em pastas diferentes? → A: Converte os dois, nomes distintos (informado pelo operador)
- Q: Qual o código de saída quando todos os PDFs são pulados? → A: Código 0 (informado pelo operador)
- Q: Qual a meta mensurável da capacidade? → A: Cobertura por testes automatizados (informado pelo operador)
- Q: Como as saídas de PDFs homônimos ficam distintas (FR-014)? → A: Espelhar subpastas da entrada (informado pelo operador)

## User Scenarios & Testing

### User Story 1 - Converter, servir e diagnosticar pela linha de comando (Priority: P1) (informado pelo operador)

Pelo comando artigo2md, a pessoa converte arquivos ou pastas de PDFs em Markdown, sobe a interface web local e verifica a instalação e os motores disponíveis.

**Acceptance Scenarios**:

1. **Given** um PDF ou uma pasta como entrada, **When** o convert roda com o motor markitdown, **Then** o Markdown é gravado na pasta de saída com o texto do artigo (fonte: `tests/test_cli.py:59`) (cobre FR-001, FR-004, FR-006)
2. **Given** uma pasta com PDFs em subpastas e um arquivo que não é PDF, **When** o convert roda, **Then** só os arquivos com extensão pdf são convertidos, em qualquer nível (fonte: `tests/test_cli.py:100`) (cobre FR-001)
3. **Given** nenhuma pasta de saída nem motor informados, **When** o convert roda, **Then** a saída vai para a pasta markdown do diretório atual e o motor é auto (fonte: `tests/test_cli.py:73`) (fonte: `tests/test_cli.py:85`) (cobre FR-004, FR-006)
4. **Given** um PDF já convertido, **When** o convert roda de novo sem e com a opção de sobrescrita, **Then** ele é pulado com código 0 e, com a opção, reconvertido (fonte: `tests/test_cli.py:135`) (cobre FR-005)
5. **Given** a opção de relatório, **When** o convert roda, **Then** um relatório JSON com origem, motor, análise e tentativas fica ao lado do Markdown (fonte: `tests/test_cli.py:152`) (cobre FR-007)
6. **Given** a porta livre, **When** o serve roda com os valores padrão e depois com as opções de navegador e diretório de dados, **Then** o servidor recebe o host local, a porta padrão e o diretório informado, e o navegador é aberto quando pedido (fonte: `tests/test_cli.py:251`) (fonte: `tests/test_cli.py:262`) (cobre FR-009, FR-011)
7. **Given** a porta ocupada, **When** o serve roda, **Then** ele termina com código 1 e sugere a porta seguinte (fonte: `tests/test_cli.py:273`) (cobre FR-010)
8. **Given** motores obrigatórios instalados ou ausentes, **When** o doctor roda, **Then** ele lista cada motor com licença e situação e termina com código 0 ou 1 (fonte: `tests/test_cli.py:293`) (fonte: `tests/test_cli.py:311`) (cobre FR-012)
9. **Given** dois PDFs de mesmo nome em subpastas da pasta de entrada, **When** o convert roda, **Then** os dois são convertidos em subpastas correspondentes da pasta de saída, com o relatório ao lado e o pulo correto na reexecução (fonte: `tests/test_cli.py:340`) (fonte: `tests/test_cli.py:363`) (fonte: `tests/test_cli.py:374`) (cobre FR-014)

### Edge Cases

- A entrada que não é PDF ou não existe é ignorada com aviso; sem nenhum PDF, o convert termina com código 1 (fonte: `tests/test_cli.py:112`) (fonte: `tests/test_cli.py:126`) (cobre FR-002, FR-003)
- A falha em um PDF não interrompe os demais (fonte: `tests/test_cli.py:176`) (cobre FR-008)
- A opção de versão mostra a versão e encerra (fonte: `tests/test_cli.py:322`) (cobre FR-013)
- Um PDF passado diretamente continua na raiz da pasta de saída (fonte: `tests/test_cli.py:353`) (cobre FR-014)
- [PENDENTE: qual a regra para duas entradas diferentes da mesma execução que apontam para o mesmo caminho de saída (FR-014)?]

## Requirements

### Functional Requirements

- **FR-001**: O subcomando convert aceita arquivos PDF e pastas; as pastas são varridas recursivamente por arquivos com extensão pdf. (fonte: `src/artigo2md/cli.py:13`)
- **FR-002**: A entrada que não é PDF ou não existe é ignorada com aviso na saída de erro. (fonte: `src/artigo2md/cli.py:29`)
- **FR-003**: Sem nenhum PDF encontrado, o convert avisa e termina com código 1. (fonte: `src/artigo2md/cli.py:37`)
- **FR-004**: O convert grava um arquivo Markdown por PDF na pasta de saída, que por padrão é a pasta markdown do diretório atual. (fonte: `src/artigo2md/cli.py:119`)
- **FR-005**: O convert pula o PDF cujo Markdown já existe, salvo com a opção --overwrite; pular não é erro, e o convert termina com código 0 mesmo quando todos são pulados (informado pelo operador). (fonte: `src/artigo2md/cli.py:45`)
- **FR-006**: A opção --engine escolhe entre auto, docling, pymupdf, mineru e markitdown, com auto como padrão. (fonte: `src/artigo2md/cli.py:120`)
- **FR-007**: A opção --report grava também um relatório JSON por arquivo convertido. (fonte: `src/artigo2md/cli.py:57`)
- **FR-008**: A falha em um PDF não interrompe os demais; ao final o convert mostra a contagem de sucessos e erros e termina com código 1 se houve falha. (fonte: `src/artigo2md/cli.py:51`)
- **FR-009**: O subcomando serve sobe a interface web, por padrão no host 127.0.0.1 e na porta 8777. (fonte: `src/artigo2md/cli.py:126`)
- **FR-010**: Se a porta já está em uso, o serve avisa, sugere outra porta e termina com código 1. (fonte: `src/artigo2md/cli.py:76`)
- **FR-011**: A opção --open abre o navegador e a opção --data-dir define onde ficam os envios e os resultados. (fonte: `src/artigo2md/cli.py:128`)
- **FR-012**: O subcomando doctor lista cada motor com licença e situação e termina com código 0 quando os motores obrigatórios estão instalados, ou 1 caso contrário. (fonte: `src/artigo2md/cli.py:94`)
- **FR-013**: A opção --version mostra a versão do artigo2md. (fonte: `src/artigo2md/cli.py:114`)
- **FR-014**: Quando PDFs de entrada têm o mesmo nome em pastas diferentes, o convert converte todos e grava as saídas em caminhos distintos, repetindo na pasta de saída a estrutura de subpastas da pasta de entrada, sem pular nem sobrescrever por coincidência de nome; um PDF passado diretamente vai para a raiz da pasta de saída. (informado pelo operador) (fonte: `src/artigo2md/cli.py:25`)

### Inferido (nao confirmado)

- Nenhuma inferência em aberto: a antiga INF-001 virou o requisito FR-014 na clarificação.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Todo requisito funcional desta spec tem ao menos um teste automatizado que passa. (informado pelo operador)

## Delta Requirements

**Skip**: spec reconstituida de comportamento ja existente; nao altera comportamento documentado — cstk-init, 2026-11-20
