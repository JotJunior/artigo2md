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

### Session 2026-11-21

- Q: A fila de conversão deve ter tamanho máximo? → A: Não, sem máximo (informado pelo operador)

## User Scenarios & Testing

### User Story 1 - Enviar PDFs, acompanhar a fila e baixar o Markdown (Priority: P1) (informado pelo operador)

Pela interface web local, a pessoa envia um ou mais PDFs, acompanha cada conversão na fila, lê o Markdown resultante, baixa um arquivo ou todos num zip e apaga o que não quer mais.

**Acceptance Scenarios**:

1. **Given** a aplicação iniciada com um diretório de dados vazio, **When** a página inicial e as informações são pedidas, **Then** a página responde com sucesso e a lista de motores inclui o docling (fonte: `tests/test_web.py:27`) (cobre FR-001, FR-002)
2. **Given** o envio de um PDF válido e de um arquivo que não é PDF com o motor markitdown, **When** os jobs são criados, **Then** o arquivo que não é PDF volta com erro e o PDF é convertido até o estado concluído, com o Markdown disponível para leitura (fonte: `tests/test_web.py:27`) (cobre FR-003, FR-004, FR-005, FR-006, FR-008)
3. **Given** um job concluído, **When** o zip é baixado, **Then** ele contém o Markdown com o nome do arquivo enviado (fonte: `tests/test_web.py:27`) (cobre FR-009)
4. **Given** um job concluído, **When** ele é apagado, **Then** a resposta confirma e a lista de jobs fica vazia (fonte: `tests/test_web.py:27`) (cobre FR-007, FR-010)
5. **Given** um envio com motor inexistente, **When** os jobs são criados, **Then** a resposta é 400 (fonte: `tests/test_web.py:49`) (cobre FR-003)
6. **Given** jobs gravados em fila e em andamento e um job concluído, **When** a aplicação é recriada sobre o mesmo diretório de dados, **Then** os dois primeiros aparecem como erro por interrupção e o concluído permanece (fonte: `tests/test_web.py:124`) (cobre FR-011)

### Edge Cases

- Um job inexistente responde 404 na consulta, no Markdown e na remoção (fonte: `tests/test_web.py:142`) (cobre FR-007)
- Com a conversão em andamento, a etapa do job mostra o aviso de progresso, o Markdown responde 409 e a remoção responde 409; depois de concluir, o job pode ser apagado (fonte: `tests/test_web.py:106`) (cobre FR-008, FR-010, FR-012)
- Dois arquivos de mesmo nome ficam com nomes distintos no zip, que também aceita a escolha de jobs; o nome de saída troca os caracteres fora do conjunto permitido e é limitado a 150 caracteres (fonte: `tests/test_web.py:151`) (fonte: `tests/test_web.py:175`) (fonte: `tests/test_web.py:179`) (cobre FR-009, FR-013)
- As rotas respondem sem credencial e devolvem os campos que a página lê; o serve escuta só na própria máquina por padrão e avisa quando o host não é local (fonte: `tests/test_web.py:184`) (fonte: `tests/test_cli.py:251`) (fonte: `tests/test_cli.py:424`) (cobre FR-014)

## Requirements

### Functional Requirements

- **FR-001**: O sistema serve a página estática da interface em GET /. (fonte: `src/artigo2md/web.py:125`)
- **FR-002**: O sistema devolve em GET /api/info a versão, os motores disponíveis, a ficha dos motores e as opções de motor. (fonte: `src/artigo2md/web.py:129`)
- **FR-003**: O sistema recebe em POST /api/jobs um ou mais arquivos e o motor desejado (auto, docling, pymupdf, mineru ou markitdown; padrão auto) e responde 400 para motor inválido; a interface não impõe limite de tamanho nem de quantidade de arquivos por envio (informado pelo operador). (fonte: `src/artigo2md/web.py:134`)
- **FR-004**: O arquivo cujo conteúdo não começa com a assinatura de PDF (os bytes iniciais %PDF) volta como erro, sem criar job. (fonte: `src/artigo2md/web.py:141`)
- **FR-005**: Os jobs entram numa fila e são convertidos um por vez por um trabalhador em segundo plano; a fila não tem tamanho máximo (informado pelo operador). (fonte: `src/artigo2md/web.py:78`)
- **FR-006**: Cada job guarda, numa pasta própria do diretório de dados, o PDF de origem, os metadados e o Markdown resultante; os arquivos permanecem até a pessoa apagar o job, sem limpeza automática (informado pelo operador). (fonte: `src/artigo2md/web.py:62`)
- **FR-007**: O sistema lista os jobs do mais recente ao mais antigo em GET /api/jobs e devolve um job em GET /api/jobs/{job_id}, com 404 quando não existe. (fonte: `src/artigo2md/web.py:147`)
- **FR-008**: O sistema devolve o Markdown de um job concluído em GET /api/jobs/{job_id}/markdown, como texto ou como download, e responde 409 quando ainda não foi convertido. (fonte: `src/artigo2md/web.py:160`)
- **FR-009**: O sistema devolve em GET /api/download.zip um zip com os Markdown dos jobs concluídos (todos ou os identificadores pedidos), diferenciando nomes repetidos. (fonte: `src/artigo2md/web.py:171`)
- **FR-010**: O sistema apaga um job e seus arquivos em DELETE /api/jobs/{job_id} e responde 409 quando o job está em fila ou em andamento. (fonte: `src/artigo2md/web.py:155`)
- **FR-011**: Ao iniciar, o sistema recarrega os jobs gravados e marca como erro por interrupção os que estavam em fila ou em andamento. (fonte: `src/artigo2md/web.py:46`)
- **FR-012**: O progresso da conversão é gravado na etapa do job. (fonte: `src/artigo2md/web.py:89`)
- **FR-013**: O nome do arquivo de saída é derivado do nome enviado, com caracteres fora do conjunto permitido (letras, dígitos, sublinhado, ponto, hífen e espaço) substituídos e limite de 150 caracteres. (fonte: `src/artigo2md/web.py:25`) (fonte: `src/artigo2md/web.py:22`)
- **FR-014**: A interface não exige autenticação e o servidor escuta apenas na própria máquina por padrão; a exposição na rede depende de escolha explícita de quem inicia o servidor. (informado pelo operador) (fonte: `src/artigo2md/cli.py:152`)

### Inferido (nao confirmado)

- Nenhuma inferência em aberto: a antiga INF-001 foi promovida a FR-014 na clarificação.

### Key Entities

- **Job**: identificador, nome do arquivo, nome de saída, tamanho, motor solicitado, estado, etapa e data de criação; após a conversão, motor usado, análise, tentativas e qualidade. (fonte: `src/artigo2md/web.py:67`)

## Success Criteria

### Measurable Outcomes

- **SC-001**: Todo requisito funcional desta spec tem ao menos um teste automatizado que passa. (informado pelo operador)

## Delta Requirements

**Skip**: spec reconstituida de comportamento ja existente; nao altera comportamento documentado — cstk-init, 2026-11-20
