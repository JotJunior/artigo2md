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

1. **Given** arquivos PDF e pastas como entrada, **When** o subcomando convert roda, **Then** [PENDENTE: qual teste cobre a coleta de PDFs, a gravação do Markdown e as opções de motor e relatório (FR-001, FR-004, FR-006, FR-007)?]
2. **Given** um PDF já convertido na pasta de saída, **When** o subcomando convert roda sem e com a opção de sobrescrita, **Then** [PENDENTE: qual teste cobre o pulo e a reconversão (FR-005)?]
3. **Given** a porta livre ou ocupada, **When** o subcomando serve roda, **Then** [PENDENTE: qual teste cobre a subida da interface, a porta ocupada e as opções de navegador e diretório de dados (FR-009, FR-010, FR-011)?]
4. **Given** motores obrigatórios instalados ou ausentes, **When** o subcomando doctor roda, **Then** [PENDENTE: qual teste cobre a listagem dos motores e o código de saída (FR-012)?]

### Edge Cases

- [PENDENTE: qual teste cobre a entrada ignorada e a execução sem nenhum PDF (FR-002, FR-003)?]
- [PENDENTE: qual teste cobre a falha em um PDF sem interromper os demais (FR-008) e a opção de versão (FR-013)?]
- [PENDENTE: qual teste cobre a conversão de PDFs de mesmo nome em pastas diferentes (FR-014)?]

## Requirements

### Functional Requirements

- **FR-001**: O subcomando convert aceita arquivos PDF e pastas; as pastas são varridas recursivamente por arquivos com extensão pdf. (fonte: `src/artigo2md/cli.py:13`)
- **FR-002**: A entrada que não é PDF ou não existe é ignorada com aviso na saída de erro. (fonte: `src/artigo2md/cli.py:22`)
- **FR-003**: Sem nenhum PDF encontrado, o convert avisa e termina com código 1. (fonte: `src/artigo2md/cli.py:30`)
- **FR-004**: O convert grava um arquivo Markdown por PDF na pasta de saída, que por padrão é a pasta markdown do diretório atual. (fonte: `src/artigo2md/cli.py:111`)
- **FR-005**: O convert pula o PDF cujo Markdown já existe, salvo com a opção --overwrite; pular não é erro, e o convert termina com código 0 mesmo quando todos são pulados (informado pelo operador). (fonte: `src/artigo2md/cli.py:38`)
- **FR-006**: A opção --engine escolhe entre auto, docling, pymupdf, mineru e markitdown, com auto como padrão. (fonte: `src/artigo2md/cli.py:112`)
- **FR-007**: A opção --report grava também um relatório JSON por arquivo convertido. (fonte: `src/artigo2md/cli.py:49`)
- **FR-008**: A falha em um PDF não interrompe os demais; ao final o convert mostra a contagem de sucessos e erros e termina com código 1 se houve falha. (fonte: `src/artigo2md/cli.py:44`)
- **FR-009**: O subcomando serve sobe a interface web, por padrão no host 127.0.0.1 e na porta 8777. (fonte: `src/artigo2md/cli.py:118`)
- **FR-010**: Se a porta já está em uso, o serve avisa, sugere outra porta e termina com código 1. (fonte: `src/artigo2md/cli.py:68`)
- **FR-011**: A opção --open abre o navegador e a opção --data-dir define onde ficam os envios e os resultados. (fonte: `src/artigo2md/cli.py:120`)
- **FR-012**: O subcomando doctor lista cada motor com licença e situação e termina com código 0 quando os motores obrigatórios estão instalados, ou 1 caso contrário. (fonte: `src/artigo2md/cli.py:86`)
- **FR-013**: A opção --version mostra a versão do artigo2md. (fonte: `src/artigo2md/cli.py:106`)
- **FR-014**: Quando PDFs de entrada têm o mesmo nome em pastas diferentes, o convert converte todos e grava as saídas em caminhos distintos, repetindo na pasta de saída a estrutura de subpastas da pasta de entrada, sem pular nem sobrescrever por coincidência de nome; requisito definido na clarificação, ainda não observado no código. (informado pelo operador)

### Inferido (nao confirmado)

- Nenhuma inferência em aberto: a antiga INF-001 virou o requisito FR-014 na clarificação.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Todo requisito funcional desta spec tem ao menos um teste automatizado que passa. (informado pelo operador)

## Delta Requirements

**Skip**: spec reconstituida de comportamento ja existente; nao altera comportamento documentado — cstk-init, 2026-11-20
