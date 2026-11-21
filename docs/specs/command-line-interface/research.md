# Research: command-line-interface

Documento produzido no Phase 0 do `/plan`. Resolve os unknowns do Technical Context antes do design.
Escopo do plano: atender FR-014 (PDFs homônimos em pastas diferentes) e criar testes para todos os
requisitos da linha de comando, exigidos por SC-001. Hoje nenhum teste chama a linha de comando.

## Decision 1: Como tornar distintas as saídas de PDFs homônimos (FR-014)

**Decision**: A saída repete, dentro da pasta de saída, a estrutura de subpastas da pasta de entrada (informado pelo operador). Um PDF passado diretamente como arquivo continua indo para a raiz da pasta de saída.
**Rationale**: Decisão do operador em 2026-11-20. O caminho de saída passa a depender só da posição do PDF na pasta de entrada, então é estável entre execuções e o pulo de arquivo já convertido (FR-005) continua correto. Hoje o destino usa só o nome do PDF (fonte: `src/artigo2md/cli.py:37`), e o segundo homônimo é pulado como já existente (fonte: `src/artigo2md/cli.py:38`).
**Alternatives considered**: Sufixo numérico no nome (depende da ordem dos arquivos e muda quando um PDF novo aparece); prefixo com o nome da subpasta só quando há colisão (o nome de um arquivo mudaria conforme os vizinhos).

**Consequência a tratar no backlog**: o layout de saída muda para quem converte pastas com subpastas. O README descreve a conversão de pasta inteira (fonte: `README.md:65`) e precisa ser atualizado junto.

## Decision 2: Colisões que o espelhamento não resolve

**Decision**: `[PROPOSTA — a validar na implementacao]` quando duas entradas diferentes da mesma execução apontam para o mesmo caminho de saída (dois arquivos homônimos passados diretamente, ou duas pastas de entrada com o mesmo caminho relativo), o convert detecta a colisão antes de converter e separa as saídas em subpastas com o nome da pasta de origem de cada entrada.
**Rationale**: FR-014 pede que nenhum PDF fique de fora por coincidência de nome; o espelhamento cobre uma pasta de entrada, não várias entradas.
**Alternatives considered**: Recusar a execução com mensagem de erro (não converte todos, contraria FR-014); ignorar o caso (mantém o defeito para várias entradas).

**Ponto a confirmar com o operador na implementação**: a regra de separação para várias entradas é proposta deste plano, não decisão registrada.

## Decision 3: Relatório JSON com o novo layout

**Decision**: O relatório continua ao lado do Markdown, no mesmo subdiretório espelhado.
**Rationale**: O relatório já é derivado do caminho do Markdown (fonte: `src/artigo2md/cli.py:50`); manter a regra evita mudança adicional.
**Alternatives considered**: Pasta separada para relatórios (mudança sem pedido).

## Decision 4: Como testar a linha de comando

**Decision**: Testes com pytest chamando a função de entrada da linha de comando em processo, com a lista de argumentos, capturando saída e código de retorno; pastas temporárias para entradas e saídas.
**Rationale**: A função de entrada aceita a lista de argumentos e devolve o código de saída (fonte: `src/artigo2md/cli.py:104`), o que dispensa subprocesso. Os subcomandos importam suas dependências no momento da chamada (fonte: `src/artigo2md/cli.py:27`) (fonte: `src/artigo2md/cli.py:63`) (fonte: `src/artigo2md/cli.py:87`), o que permite substituí-las nos testes.
**Alternatives considered**: Executar o comando instalado em subprocesso (mais lento e dependente do ambiente).

## Decision 5: Testes do convert

**Decision**: Um teste de ponta a ponta com o motor markitdown sobre a fixture de artigo e, para os demais casos (pulo, sobrescrita, relatório, falha isolada, entrada ignorada, nenhum PDF, homônimos), substituir a função de conversão por uma simulação rápida.
**Rationale**: O markitdown já é usado em teste de ponta a ponta sem baixar modelos (fonte: `tests/test_core.py:80`); os demais casos testam a lógica da linha de comando, não a conversão.
**Alternatives considered**: Usar conversão real em todos os testes (lento, sem ganho de cobertura da linha de comando).

## Decision 6: Testes do serve

**Decision**: Substituir a execução do servidor e a abertura do navegador por simulações e verificar host, porta, diretório de dados e a opção de abrir o navegador; para a porta ocupada, abrir um socket local numa porta livre e chamar o serve com essa porta.
**Rationale**: O serve verifica a porta com uma tentativa de conexão local antes de subir o servidor (fonte: `src/artigo2md/cli.py:68`), então um socket local em escuta reproduz o caso sem depender de outra aplicação.
**Alternatives considered**: Subir o servidor de verdade no teste (bloqueia a execução e exige encerramento controlado).

## Decision 7: Testes do doctor e da opção de versão

**Decision**: Substituir a ficha dos motores por listas simuladas com e sem motor obrigatório ausente e verificar o texto e o código de saída; para a versão, verificar que a opção imprime a versão e encerra.
**Rationale**: O doctor decide o código de saída só pela ficha dos motores (fonte: `src/artigo2md/cli.py:99`).
**Alternatives considered**: Depender dos motores realmente instalados (resultado variaria por máquina).
