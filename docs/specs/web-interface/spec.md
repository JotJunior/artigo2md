# Feature Specification: Interface web local

<!-- cstk-init:reconstructed -->
> **Origem**: spec reconstituida a partir do projeto existente por `cstk-init` em
> 2026-11-20. Nao foi escrita antes da implementacao; descreve comportamento observado.

**Feature**: `web-interface`
**Created**: 2026-11-20
**Status**: Draft

## Clarifications

### Session 2026-11-20

- Q: Qual a prioridade da User Story 1? → A: P1 (informado pelo operador)
- Q: Qual o requisito de controle de acesso da interface? → A: Sem senha, uso local por padrão (informado pelo operador)
- Q: Há limite de tamanho ou de quantidade de arquivos por envio? → A: Sem limite (informado pelo operador)
- Q: Qual a retenção dos arquivos no diretório de dados? → A: Só remoção manual (informado pelo operador)
- Q: Qual a meta mensurável da capacidade? → A: Cobertura por testes automatizados (informado pelo operador)

## User Scenarios & Testing

### User Story 1 - Enviar PDFs, acompanhar a fila e baixar o Markdown (Priority: P1) (informado pelo operador)

Pela interface web local, a pessoa envia um ou mais PDFs, acompanha cada conversão na fila, lê o Markdown resultante, baixa um arquivo ou todos num zip e apaga o que não quer mais.

**Acceptance Scenarios**:

1. **Given** a aplicação iniciada com um diretório de dados vazio, **When** a página inicial e as informações são pedidas, **Then** a página responde com sucesso e a lista de motores inclui o docling (fonte: `tests/test_web.py:20`) (cobre FR-001, FR-002)
2. **Given** o envio de um PDF válido e de um arquivo que não é PDF com o motor markitdown, **When** os jobs são criados, **Then** o arquivo que não é PDF volta com erro e o PDF é convertido até o estado concluído, com o Markdown disponível para leitura (fonte: `tests/test_web.py:20`) (cobre FR-003, FR-004, FR-005, FR-006, FR-008)
3. **Given** um job concluído, **When** o zip é baixado, **Then** ele contém o Markdown com o nome do arquivo enviado (fonte: `tests/test_web.py:20`) (cobre FR-009)
4. **Given** um job concluído, **When** ele é apagado, **Then** a resposta confirma e a lista de jobs fica vazia (fonte: `tests/test_web.py:20`) (cobre FR-007, FR-010)
5. **Given** um envio com motor inexistente, **When** os jobs são criados, **Then** a resposta é 400 (fonte: `tests/test_web.py:42`) (cobre FR-003)
6. **Given** jobs em fila ou em andamento, **When** o servidor é reiniciado, **Then** [PENDENTE: qual teste cobre a marcação de interrompido (FR-011)?]

### Edge Cases

- [PENDENTE: qual teste cobre o 404 de job inexistente (FR-007) e o 409 ao apagar job em andamento (FR-010)?]
- [PENDENTE: qual teste cobre o 409 de Markdown ainda não convertido (FR-008) e os nomes repetidos no zip (FR-009)?]
- [PENDENTE: qual teste cobre o registro de progresso (FR-012) e a sanitização do nome de saída (FR-013)?]
- [PENDENTE: qual teste cobre o acesso sem autenticação e a escuta local por padrão (FR-014)?]

## Requirements

### Functional Requirements

- **FR-001**: O sistema serve a página estática da interface em GET /. (fonte: `src/artigo2md/web.py:125`)
- **FR-002**: O sistema devolve em GET /api/info a versão, os motores disponíveis, a ficha dos motores e as opções de motor. (fonte: `src/artigo2md/web.py:129`)
- **FR-003**: O sistema recebe em POST /api/jobs um ou mais arquivos e o motor desejado (padrão auto) e responde 400 para motor inválido; a interface não impõe limite de tamanho nem de quantidade de arquivos por envio (informado pelo operador). (fonte: `src/artigo2md/web.py:134`)
- **FR-004**: O arquivo cujo conteúdo não começa com a assinatura de PDF volta como erro, sem criar job. (fonte: `src/artigo2md/web.py:141`)
- **FR-005**: Os jobs entram numa fila e são convertidos um por vez por um trabalhador em segundo plano. (fonte: `src/artigo2md/web.py:78`)
- **FR-006**: Cada job guarda, numa pasta própria do diretório de dados, o PDF de origem, os metadados e o Markdown resultante; os arquivos permanecem até a pessoa apagar o job, sem limpeza automática (informado pelo operador). (fonte: `src/artigo2md/web.py:62`)
- **FR-007**: O sistema lista os jobs do mais recente ao mais antigo em GET /api/jobs e devolve um job em GET /api/jobs/{job_id}, com 404 quando não existe. (fonte: `src/artigo2md/web.py:147`)
- **FR-008**: O sistema devolve o Markdown de um job concluído em GET /api/jobs/{job_id}/markdown, como texto ou como download, e responde 409 quando ainda não foi convertido. (fonte: `src/artigo2md/web.py:160`)
- **FR-009**: O sistema devolve em GET /api/download.zip um zip com os Markdown dos jobs concluídos (todos ou os identificadores pedidos), diferenciando nomes repetidos. (fonte: `src/artigo2md/web.py:171`)
- **FR-010**: O sistema apaga um job e seus arquivos em DELETE /api/jobs/{job_id} e responde 409 quando o job está em fila ou em andamento. (fonte: `src/artigo2md/web.py:155`)
- **FR-011**: Ao iniciar, o sistema recarrega os jobs gravados e marca como erro por interrupção os que estavam em fila ou em andamento. (fonte: `src/artigo2md/web.py:46`)
- **FR-012**: O progresso da conversão é gravado na etapa do job. (fonte: `src/artigo2md/web.py:89`)
- **FR-013**: O nome do arquivo de saída é derivado do nome enviado, com caracteres fora do conjunto permitido substituídos e limite de 150 caracteres. (fonte: `src/artigo2md/web.py:25`)
- **FR-014**: A interface não exige autenticação e o servidor escuta apenas na própria máquina por padrão; a exposição na rede depende de escolha explícita de quem inicia o servidor. (informado pelo operador) (fonte: `src/artigo2md/cli.py:126`)

### Inferido (nao confirmado)

- Nenhuma inferência em aberto: a antiga INF-001 foi promovida a FR-014 na clarificação.

### Key Entities

- **Job**: identificador, nome do arquivo, nome de saída, tamanho, motor solicitado, estado, etapa e data de criação; após a conversão, motor usado, análise, tentativas e qualidade. (fonte: `src/artigo2md/web.py:67`)

## Success Criteria

### Measurable Outcomes

- **SC-001**: Todo requisito funcional desta spec tem ao menos um teste automatizado que passa. (informado pelo operador)

## Delta Requirements

**Skip**: spec reconstituida de comportamento ja existente; nao altera comportamento documentado — cstk-init, 2026-11-20
