# Feature Specification: Esqueleto do app desktop macOS

**Feature**: `desktop-shell`
**Created**: 2026-11-24
**Status**: Draft

> **Origem**: feature 2 do `docs/roadmap.md` (versão desktop macOS, briefing 2.0, constitution 1.1.0; emendada para 1.2.0 durante o execute-task, que ampliou o Princípio II).
> Sem dependência de outras features: o app nasce sem backend e só enxerga o disco local.
> Decisões de infraestrutura: N/A (app local sem agendamento, sem rotação de chaves, sem refresh de token, sem multi-réplica, sem backup próprio e sem endpoint que aceite repetição de requisição).

## Clarifications

### Session 2026-11-24

- Q: Para quais arquiteturas o `.app` deve ser gerado? → A: Somente Apple Silicon (arm64/aarch64); Intel e universal ficam fora desta feature. A versão mínima do macOS não está decidida: será definida na etapa plan a partir da documentação oficial do Tauri 2.
- Q: A barra lateral pode ser recolhida, e o estado e a largura são lembrados? → A: Sim; recolher/exibir é permitido e o estado (aberta ou recolhida) e a largura persistem entre sessões junto das configurações.
- Q: O que acontece ao clicar em links no Markdown renderizado? → A: Links relativos para arquivos `.md` dentro da pasta aberta abrem no visualizador; links externos ficam inativos e não geram conexão de rede.
- Q: Haverá controle para exibir itens ocultos na árvore? → A: Não; itens ocultos são sempre omitidos nesta feature (controle fica para feature futura).
- Q: Quais formatos de imagem local o visualizador exibe? → A: Somente raster (PNG, JPEG, GIF, WebP); SVG e demais formatos mostram o aviso "Formato de imagem não suportado".

## User Scenarios & Testing

### User Story 1 - Abrir uma pasta e navegar pela árvore de arquivos (Priority: P1)

A pessoa abre o app, vê a janela principal com a barra lateral, escolhe uma pasta do próprio Mac e passa a ver a árvore de pastas e arquivos dela, somente para leitura. Na vez seguinte que abrir o app, a última pasta escolhida volta sozinha.

**Why this priority**: O gerenciador de pastas é o coração da experiência desktop (briefing, capacidade 8) e é a porta de entrada para tudo o que vem depois (visualizador, projetos, processamento). Sozinha, esta story já entrega um app utilizável: uma janela que mostra o conteúdo de uma pasta.

**Independent Test**: Iniciar o app, usar "Abrir pasta…" apontando para uma pasta de teste com subpastas e arquivos, e conferir que a árvore reflete exatamente o conteúdo do disco, sem nenhuma ação que altere arquivos.

**Acceptance Scenarios**:

1. **Given** o app aberto pela primeira vez, **When** a janela principal aparece, **Then** a barra lateral mostra as entradas "Arquivos" e "Configurações", e a área de "Arquivos" exibe o convite "Abrir pasta…" em português (cobre FR-001, FR-002, FR-003)
2. **Given** a área de "Arquivos" vazia, **When** a pessoa escolhe uma pasta pelo diálogo nativo, **Then** a árvore mostra as subpastas e os arquivos dessa pasta, com pastas antes de arquivos e ordem alfabética sem distinguir maiúsculas (cobre FR-004, FR-005)
3. **Given** uma árvore exibida, **When** a pessoa expande e recolhe uma subpasta, **Then** os filhos aparecem e somem sem recarregar a árvore inteira (cobre FR-005)
4. **Given** uma árvore exibida, **When** a pessoa examina os controles disponíveis, **Then** não existe nenhuma ação de criar, renomear, mover, apagar ou editar arquivos e pastas (cobre FR-006)
5. **Given** uma pasta escolhida numa sessão anterior que ainda existe, **When** o app é aberto de novo, **Then** a árvore dessa pasta é exibida sem nova escolha (cobre FR-007)
6. **Given** uma pasta que contém arquivos e pastas ocultos (nome iniciado por ponto), **When** a árvore é exibida, **Then** os itens ocultos não aparecem (cobre FR-008)

---

### User Story 2 - Ler um arquivo Markdown com tabelas, fórmulas e imagens (Priority: P2)

A pessoa clica num arquivo `.md` da árvore e lê o conteúdo formatado no visualizador: títulos, listas, tabelas, fórmulas matemáticas e imagens referenciadas por caminho relativo ao arquivo, exatamente o tipo de conteúdo que o artigo2md gera a partir de PDFs.

**Why this priority**: O visualizador é o segundo pilar da experiência desktop (briefing, capacidade 9). Depende de haver uma pasta aberta (story 1), mas entrega valor isolado: ler o resultado de uma conversão com fidelidade, sem abrir outro programa.

**Independent Test**: Com uma pasta de teste contendo um `.md` com uma tabela, uma fórmula em linha, uma fórmula em bloco e uma imagem em subpasta, abrir o arquivo pela árvore e conferir que os quatro elementos aparecem renderizados.

**Acceptance Scenarios**:

1. **Given** uma pasta aberta com um arquivo `.md`, **When** a pessoa clica nele na árvore, **Then** o conteúdo aparece formatado no visualizador, com títulos, parágrafos, listas, citações e blocos de código (cobre FR-009, FR-010)
2. **Given** um `.md` com uma tabela em sintaxe de tabelas do GitHub, **When** ele é aberto, **Then** a tabela aparece com linhas e colunas alinhadas e cabeçalho distinguível (cobre FR-011)
3. **Given** um `.md` com fórmula em linha e fórmula em bloco em notação LaTeX, **When** ele é aberto, **Then** as duas aparecem como notação matemática renderizada, não como texto cru (cobre FR-012)
4. **Given** um `.md` que referencia `imagens/figura1.png` por caminho relativo e esse arquivo existe a partir da pasta do `.md`, **When** ele é aberto, **Then** a imagem aparece no lugar da referência (cobre FR-013)
5. **Given** um `.md` com imagem de caminho relativo cujo arquivo não existe, **When** ele é aberto, **Then** o texto alternativo e o aviso "Imagem não encontrada" aparecem no lugar da imagem e o resto do documento continua legível (cobre FR-014)
6. **Given** um `.md` com imagem referenciada por endereço de internet, **When** ele é aberto, **Then** nenhuma conexão de rede é feita e o lugar da imagem mostra o aviso "Imagem remota bloqueada" (cobre FR-015, FR-016)
7. **Given** um `.md` com trecho de HTML bruto, **When** ele é aberto, **Then** o HTML não é interpretado nem executado e o texto aparece como texto (cobre FR-017)
8. **Given** um arquivo da árvore que não é `.md`, **When** a pessoa clica nele, **Then** o visualizador mostra "Este tipo de arquivo não pode ser visualizado" e nada é alterado (cobre FR-018)
9. **Given** um `.md` que referencia uma imagem local existente em formato SVG (ou outro formato que não seja PNG, JPEG, GIF ou WebP), **When** ele é aberto, **Then** o lugar da imagem mostra o aviso "Formato de imagem não suportado", o arquivo não é carregado e o resto do documento continua legível (cobre FR-041)
10. **Given** um `.md` com link relativo para outro `.md` dentro da pasta aberta, **When** a pessoa clica no link, **Then** o visualizador abre o arquivo apontado (cobre FR-040)
11. **Given** um `.md` com link para endereço de internet (ou outro destino externo), **When** a pessoa clica no link, **Then** nada é aberto, nenhuma conexão de rede é feita e o link aparece como inativo (cobre FR-015, FR-040)

---

### User Story 3 - Ajustar e manter configurações do app (Priority: P3)

A pessoa abre "Configurações" na barra lateral, muda a aparência e o tamanho do texto do visualizador, fecha o app e, ao reabrir, encontra as mesmas escolhas.

**Why this priority**: Torna o app confortável e prova a persistência local de preferências que as features seguintes (controle da stack, processamento) vão reutilizar, mas não bloqueia navegar nem ler.

**Independent Test**: Alterar cada configuração, fechar e reabrir o app, e conferir que os valores e o efeito visual foram mantidos; depois usar "Restaurar padrões" e conferir o retorno aos valores iniciais.

**Acceptance Scenarios**:

1. **Given** o app aberto, **When** a pessoa escolhe "Configurações" na barra lateral, **Then** a tela mostra, em português, os controles de aparência (claro, escuro ou seguir o sistema) e de tamanho do texto do visualizador, com os valores atuais (cobre FR-019, FR-020)
2. **Given** a tela de configurações, **When** a pessoa muda a aparência para "Escuro", **Then** a janela inteira passa a usar o tema escuro imediatamente, sem reiniciar (cobre FR-021)
3. **Given** a tela de configurações, **When** a pessoa aumenta o tamanho do texto, **Then** o visualizador passa a exibir o Markdown no novo tamanho (cobre FR-021)
4. **Given** configurações alteradas, **When** o app é fechado e aberto de novo, **Then** os mesmos valores estão ativos (cobre FR-022)
5. **Given** o arquivo de configurações ilegível ou com valores inválidos, **When** o app é aberto, **Then** o app abre normalmente com os padrões e mostra o aviso "Não foi possível ler as configurações salvas; os padrões foram restaurados" (cobre FR-023)
6. **Given** configurações alteradas, **When** a pessoa aciona "Restaurar padrões", **Then** todos os controles voltam aos valores iniciais e isso também é persistido (cobre FR-024)
7. **Given** a barra lateral recolhida ou com a largura ajustada, **When** o app é fechado e aberto de novo, **Then** a barra lateral reaparece no mesmo estado (aberta ou recolhida) e com a mesma largura (cobre FR-039)

---

### User Story 4 - Obter o app como um pacote `.app` sem assinatura (Priority: P4)

O mantenedor (ou um colaborador) gera, por um único comando documentado, o pacote `.app` do aplicativo para macOS sem assinatura, e consegue abri-lo num Mac.

**Why this priority**: Fecha a entrega da feature e prova que o esqueleto realmente roda como app de Mac, mas depende de as stories 1 a 3 existirem para haver algo a empacotar. A assinatura e a notarização são pós-MVP (briefing) e o `.dmg` fica para a feature `desktop-release`.

**Independent Test**: Num Mac com as ferramentas instaladas, rodar o comando de build documentado, localizar o `.app` gerado e abri-lo; conferir que a janela principal aparece.

**Acceptance Scenarios**:

1. **Given** o repositório com as ferramentas de build instaladas, **When** o comando de build do app é executado, **Then** um pacote `.app` é gerado em local previsível e documentado, sem exigir conta de desenvolvedor da Apple (cobre FR-025, FR-026)
2. **Given** o `.app` gerado, **When** é aberto num Mac (com a liberação manual do Gatekeeper, por não ter assinatura de desenvolvedor identificado), **Then** a janela principal aparece com a barra lateral e a árvore vazia, em Mac com Apple Silicon (cobre FR-025, FR-027, FR-042)
3. **Given** o `.app` gerado e sem conexão com a internet, **When** a pessoa abre uma pasta e um `.md`, **Then** tudo funciona igual, sem nenhum recurso baixado em tempo de execução (cobre FR-015, FR-016)
4. **Given** o repositório após a feature, **When** se inspeciona a estrutura, **Then** todo o código do app está em `desktop/` e a CLI, a interface web e a suíte Python existentes permanecem sem alteração de comportamento (cobre FR-036)
5. **Given** o conjunto de dependências obrigatórias do app (npm e cargo), **When** a licença de cada uma é conferida em fonte oficial, **Then** todas são MIT, Apache ou BSD e a lista conferida fica registrada no plano (cobre FR-037)
6. **Given** o código do app, **When** `cargo test` e os testes npm são executados, **Then** ambos passam e cobrem listagem da árvore, resolução segura de caminhos de imagem, persistência de configurações e renderização de tabelas e fórmulas (cobre FR-038)

---

### Edge Cases

- A pasta escolhida na sessão anterior foi apagada, movida ou desmontada: o app abre normalmente, mostra o aviso "A última pasta aberta não está mais disponível" e volta ao convite "Abrir pasta…" (cobre FR-007, FR-028)
- A pessoa não tem permissão de leitura sobre a pasta ou sobre uma subpasta: o item aparece marcado como inacessível com a mensagem "Sem permissão para ler esta pasta", e o resto da árvore continua utilizável (cobre FR-028)
- A pasta tem milhares de itens: a árvore carrega o nível expandido sem travar a janela e a pessoa continua podendo rolar e interagir enquanto o carregamento ocorre (cobre FR-029)
- A árvore contém atalhos simbólicos (links) que apontam para fora da pasta escolhida ou formam ciclo: o atalho aparece identificado como tal, não é seguido para fora da pasta escolhida e não gera repetição infinita (cobre FR-030)
- O `.md` é aberto e, depois, apagado ou alterado no disco por outro programa: ao reabri-lo (ou ao acionar "Recarregar"), o visualizador mostra o conteúdo atual, ou o aviso "Arquivo não encontrado" se foi apagado (cobre FR-031)
- O `.md` referencia uma imagem com `..` que sairia da pasta escolhida (ex.: `../../segredo.png`): a imagem não é carregada e o lugar mostra "Imagem fora da pasta aberta" (cobre FR-032)
- Uma fórmula LaTeX está inválida: apenas aquela fórmula aparece como erro, em destaque, com o texto original, e o restante do documento é renderizado normalmente (cobre FR-033)
- O `.md` é muito grande (vários megabytes): o visualizador mostra o aviso "Arquivo grande: a renderização pode demorar" e continua respondendo, sem congelar a barra lateral (cobre FR-029, FR-034)
- O `.md` contém bytes que não são texto válido: o visualizador mostra "Não foi possível ler este arquivo como texto" em vez de falhar (cobre FR-035)
- Duas imagens ou dois arquivos com nomes que diferem só por acentuação ou maiúsculas: cada referência resolve para o arquivo de nome exatamente igual no disco (cobre FR-013)
- O `.md` referencia uma imagem local em SVG ou em outro formato fora de PNG, JPEG, GIF e WebP: a imagem não é carregada e o lugar mostra "Formato de imagem não suportado" (cobre FR-041)
- O `.md` tem link relativo para um `.md` que não existe, ou cujo caminho resolvido sai da pasta aberta: nada é aberto e o visualizador mostra "Arquivo não encontrado" ou "Link fora da pasta aberta", respectivamente (cobre FR-031, FR-040)
- A janela é redimensionada ou a barra lateral é recolhida: o visualizador e a árvore se adaptam e nenhum texto fica cortado sem rolagem (cobre FR-001)

## Requirements

### Functional Requirements

- **FR-001**: O sistema MUST exibir uma janela principal de app macOS, redimensionável, com barra lateral à esquerda e área de conteúdo à direita, e MUST manter o conteúdo legível e rolável quando a janela é redimensionada ou a barra lateral é recolhida (ver FR-039).
- **FR-002**: A barra lateral MUST ter as entradas "Arquivos" e "Configurações" e MUST destacar a entrada atualmente selecionada.
- **FR-003**: Todo texto visível ao usuário (rótulos, botões, avisos, mensagens de erro, estados vazios) MUST estar em português do Brasil (Princípio III da constitution).
- **FR-004**: O usuário MUST poder escolher uma pasta local por diálogo nativo do macOS, acionado por "Abrir pasta…".
- **FR-005**: O sistema MUST exibir a pasta escolhida como árvore hierárquica expansível, com pastas antes de arquivos e ordem alfabética sem distinguir maiúsculas, e MUST permitir expandir e recolher cada pasta.
- **FR-006**: A árvore MUST ser somente leitura: o app MUST NOT oferecer nem executar ação de criar, renomear, mover, copiar, apagar ou editar arquivos e pastas do usuário.
- **FR-007**: O sistema MUST lembrar a última pasta escolhida e MUST reabri-la automaticamente na inicialização seguinte quando ela ainda existir.
- **FR-008**: A árvore MUST omitir itens ocultos (nome iniciado por ponto); não há controle para exibi-los nesta feature.
- **FR-009**: Ao selecionar um arquivo `.md` na árvore, o sistema MUST exibir seu conteúdo formatado no visualizador.
- **FR-010**: O visualizador MUST renderizar os elementos básicos de Markdown: títulos, parágrafos, ênfase, listas ordenadas e não ordenadas, citações, blocos de código, código em linha, links e linhas horizontais.
- **FR-011**: O visualizador MUST renderizar tabelas na sintaxe de tabelas do GitHub (GFM), com cabeçalho e alinhamento de coluna.
- **FR-012**: O visualizador MUST renderizar fórmulas matemáticas em notação LaTeX, em linha e em bloco.
- **FR-013**: O visualizador MUST exibir imagens referenciadas por caminho relativo ao diretório do arquivo `.md`, resolvendo o nome exatamente como está no disco.
- **FR-014**: Quando a imagem referenciada não existe, o visualizador MUST mostrar o texto alternativo e o aviso "Imagem não encontrada" no lugar dela, sem interromper a renderização do restante.
- **FR-015**: O app MUST NOT estabelecer nenhuma conexão de rede nesta feature: nem com backend, nem com serviços externos, nem para carregar imagens, fontes, scripts ou qualquer outro recurso (Princípio I da constitution).
- **FR-016**: Referências a imagens por endereço de internet MUST NOT ser carregadas; o visualizador MUST mostrar o aviso "Imagem remota bloqueada" no lugar delas.
- **FR-017**: HTML bruto dentro do Markdown MUST NOT ser interpretado nem executado; MUST ser exibido como texto.
- **FR-018**: Ao selecionar um arquivo que não é `.md`, o visualizador MUST mostrar "Este tipo de arquivo não pode ser visualizado" sem alterar o arquivo.
- **FR-019**: O sistema MUST oferecer uma tela de configurações acessível pela barra lateral.
- **FR-020**: A tela de configurações MUST oferecer, nesta feature, a escolha de aparência (claro, escuro ou seguir o sistema; padrão: seguir o sistema) e do tamanho do texto do visualizador (padrão: tamanho médio, com ao menos três níveis); não há controle para exibir itens ocultos.
- **FR-021**: A alteração de qualquer configuração MUST ter efeito imediato na interface, sem reiniciar o app.
- **FR-022**: As configurações MUST ser gravadas localmente e MUST ser restauradas na inicialização seguinte, assim como a última pasta aberta.
- **FR-023**: Quando as configurações salvas estiverem ilegíveis ou inválidas, o app MUST abrir normalmente com os valores padrão e MUST avisar a pessoa em português.
- **FR-024**: A tela de configurações MUST oferecer "Restaurar padrões", que devolve todas as configurações aos valores iniciais e persiste essa mudança.
- **FR-025**: O build do app MUST gerar um pacote `.app` de macOS funcional que abre a janela principal, para a arquitetura Apple Silicon (arm64) (ver FR-042).
- **FR-026**: O build MUST funcionar sem conta de desenvolvedor da Apple, sem identidade de desenvolvedor e sem notarização, e MUST ser reproduzível por um único comando documentado no repositório. O bundle MUST sair com assinatura **ad hoc** válida (`codesign --verify --deep --strict` passa), verificada no build; a assinatura só do linker, sem selar `Info.plist` e recursos, faz o macOS recusar o `.app` em quarentena como danificado (esclarecimento da onda-018, block-011, research.md R9.13).
- **FR-027**: O pacote `.app` sem assinatura de desenvolvedor (só ad hoc) MUST abrir num Mac após a liberação manual do Gatekeeper; as instruções de distribuição por `.dmg` ficam fora desta feature.
- **FR-028**: O sistema MUST tratar com mensagem em português, sem encerrar o app, os erros de acesso ao disco: pasta ausente, pasta ou subpasta sem permissão de leitura e arquivo inexistente.
- **FR-029**: A árvore e o visualizador MUST manter a janela responsiva durante o carregamento de pastas com muitos itens e de arquivos grandes.
- **FR-030**: Atalhos simbólicos MUST aparecer identificados na árvore e MUST NOT ser seguidos para fora da pasta escolhida; ciclos MUST NOT causar repetição infinita.
- **FR-031**: O visualizador MUST mostrar o conteúdo atual do arquivo ao reabri-lo ou ao acionar "Recarregar", e "Arquivo não encontrado" quando ele já não existir.
- **FR-032**: O visualizador MUST recusar carregar imagem cujo caminho resolvido saia da pasta escolhida, mostrando "Imagem fora da pasta aberta".
- **FR-033**: Uma fórmula LaTeX inválida MUST aparecer como erro localizado, com o texto original, sem impedir a renderização do restante do documento.
- **FR-034**: Para arquivos `.md` acima de um limite de tamanho (valor padrão a definir na etapa plan), o visualizador MUST avisar "Arquivo grande: a renderização pode demorar" e MUST continuar respondendo.
- **FR-035**: Arquivo `.md` com conteúdo que não seja texto válido MUST resultar em "Não foi possível ler este arquivo como texto", sem falha do app.
- **FR-036**: O código do app MUST ser organizado em `desktop/` na raiz do repositório, sem alterar o comportamento da CLI nem da interface web existentes.
- **FR-037**: Toda dependência obrigatória do app (npm e cargo) MUST ter licença MIT, Apache ou BSD, verificada em fonte oficial na etapa plan (Princípio II da constitution). Desde a constitution 1.2.0, o Princípio II aceita também outras licenças permissivas sem copyleft e, em dependência transitiva, MPL-2.0 sem modificação, registrada por nome e licença no plan (ver plan.md §"Licenças transitivas registradas").
- **FR-038**: O app MUST ter testes automatizados do lado nativo (`cargo test`) e do lado da interface (npm) cobrindo ao menos a listagem da árvore, a resolução segura de caminhos de imagem, a persistência de configurações e a renderização de tabelas e fórmulas; esses testes MUST passar antes de qualquer commit que toque `desktop/` (Princípio IV da constitution).
- **FR-039**: O usuário MUST poder recolher e exibir a barra lateral, e o sistema MUST persistir localmente o estado (aberta ou recolhida) e a largura da barra lateral, restaurando ambos na inicialização seguinte, junto das demais configurações.
- **FR-040**: No visualizador, links relativos que apontam para arquivos `.md` dentro da pasta aberta MUST abrir o arquivo apontado no visualizador; links externos (endereço de internet ou qualquer destino fora da pasta aberta) MUST ficar inativos, sem abrir nada e sem conexão de rede. O caminho resolvido do link MUST ficar confinado à pasta aberta (mesma regra do FR-032); o detalhamento fica para a etapa plan.
- **FR-041**: O visualizador MUST exibir somente imagens locais nos formatos PNG, JPEG, GIF e WebP; para imagem local existente em qualquer outro formato (incluindo SVG), MUST mostrar o aviso "Formato de imagem não suportado" no lugar dela, sem carregar o arquivo e sem interromper a renderização do restante. A lista de formatos efetivamente suportados pelo webview MUST ser confirmada em documentação oficial na etapa plan.
- **FR-042**: O `.app` MUST ser gerado somente para Apple Silicon (arm64/aarch64); Intel e binário universal estão fora desta feature. A versão mínima do macOS MUST ser definida na etapa plan a partir da documentação oficial do Tauri 2, sem suposição.

### Key Entities

- **Pasta aberta**: a pasta local escolhida pela pessoa; guarda o caminho e é a raiz da árvore e o limite para resolução de imagens. Uma só por vez nesta feature.
- **Item da árvore**: pasta, arquivo ou atalho simbólico dentro da pasta aberta; tem nome, tipo, estado de acesso (acessível ou sem permissão) e, para pastas, os filhos carregados sob demanda.
- **Documento Markdown**: o arquivo `.md` selecionado; o conteúdo exibido reflete o disco no momento da abertura ou do último "Recarregar".
- **Configurações do app**: aparência, tamanho do texto, última pasta aberta, estado (aberta ou recolhida) e largura da barra lateral; gravadas localmente, com valores padrão conhecidos.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Uma pessoa que nunca usou o app abre uma pasta e lê um `.md` renderizado em até 30 segundos, sem instrução externa.
- **SC-002**: Numa pasta de teste com 5.000 itens, a árvore do primeiro nível aparece em até 2 segundos e a janela continua respondendo a cliques durante o carregamento.
- **SC-003**: Um `.md` de teste com tabela, fórmula em linha, fórmula em bloco e imagem relativa tem 100% desses quatro elementos renderizados corretamente, verificado por teste automatizado.
- **SC-004**: Zero conexões de rede são feitas durante a execução do app, inclusive ao abrir `.md` com imagens remotas, verificado por teste automatizado e por observação do tráfego em uma sessão de uso completa.
- **SC-005**: 100% das configurações alteradas estão presentes após fechar e reabrir o app, verificado por teste automatizado de persistência.
- **SC-006**: 100% do texto visível ao usuário está em português, verificado por revisão de todas as telas e mensagens.
- **SC-007**: O comando de build documentado gera o pacote `.app` sem credenciais da Apple em uma única execução, e o pacote abre a janela principal em 100% das tentativas de uma verificação manual de aceitação.
- **SC-008**: Os testes nativos e os testes da interface passam em 100% antes do commit que fecha a feature, e a suíte Python existente (`uv run pytest`) continua passando sem alterações.
