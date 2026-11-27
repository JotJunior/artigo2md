# Feature Specification: Infraestrutura local em containers

**Feature**: `container-stack`
**Created**: 2026-11-24
**Status**: Draft

> **Origem**: item 1 do roadmap da versão desktop macOS (fonte: `docs/roadmap.md`), com base no briefing 2.0 e na constitution 1.1.0 (fonte: `docs/briefing.md`) (fonte: `docs/constitution.md`). Esta feature entrega só a base de infraestrutura: nenhuma regra de negócio da versão desktop (projetos, fila de conversão, histórico) entra aqui.

## User Scenarios & Testing

### User Story 1 - Subir e derrubar a infraestrutura local com um comando (Priority: P1)

Quem mantém ou usa a versão desktop executa um script e recebe, na própria máquina, um banco de dados (Postgres), um broker de mensagens (RabbitMQ, com o painel de gestão habilitado na imagem mas não publicado por padrão) e uma API base, todos configurados a partir de um arquivo de exemplo de parâmetros. Outro script derruba tudo sem perder os dados guardados.

**Why this priority**: Fila e banco são a base de todo o processamento paralelo e do histórico da versão desktop; as features `job-store`, `conversion-queue` e `desktop-stack-control` dependem de a infraestrutura existir e ser reproduzível na máquina do usuário (fonte: `docs/roadmap.md`).

**Independent Test**: Num computador com runtime Docker instalado e sem nenhum container do projeto, copiar o arquivo de exemplo de parâmetros, rodar o script de subir e conferir que todos os serviços ficam saudáveis; rodar o script de derrubar e conferir que nenhum container do projeto continua ativo e que os volumes continuam existindo.

**Acceptance Scenarios**:

1. **Given** o arquivo de parâmetros preenchido a partir do exemplo e o runtime Docker ativo, **When** o script de subir roda, **Then** ele aguarda os serviços ficarem saudáveis e termina com código 0, informando em português o estado de cada serviço e que o painel de gestão do RabbitMQ não está publicado; com a opção `--management-panel`, informa também a URL do painel em `127.0.0.1` (cobre FR-001, FR-002, FR-005; mudança por block-004/dec-023/dec-025)
2. **Given** a infraestrutura no ar, **When** o script de derrubar roda sem opções, **Then** os containers do projeto são removidos, os volumes nomeados com os dados do Postgres e do RabbitMQ são mantidos e o script termina com código 0 (cobre FR-002, FR-003)
3. **Given** a infraestrutura derrubada e dados gravados antes, **When** o script de subir roda de novo, **Then** os dados gravados anteriormente continuam disponíveis (cobre FR-003)
4. **Given** a infraestrutura já no ar, **When** o script de subir roda outra vez, **Then** nada é duplicado nem quebrado e o script termina com código 0 informando que já estava no ar (cobre FR-002, FR-015). **Atualização (desktop-stack-control, 2026-11-27)**: o atalho só vale quando a publicação atual do painel de gestão do RabbitMQ coincide com o modo pedido (`stack/up.sh:200-213`); sem `--management-panel` e com o painel publicado, o script chama o `up` e recria só o RabbitMQ sem ele, e com a opção e o painel não publicado recria-o com ele (observado em 11.4, `docs/specs/desktop-stack-control/plan.md`, "Resultado de 11.4")
5. **Given** o arquivo de parâmetros ausente, **When** o script de subir roda, **Then** ele termina com código diferente de 0, sem subir nenhum container, e explica em português como criar o arquivo a partir do exemplo (cobre FR-004)
6. **Given** a infraestrutura no ar, **When** o script de derrubar roda com a opção explícita de apagar os dados, **Then** o script pede confirmação antes de remover os volumes e, confirmado, remove-os (cobre FR-002, FR-003)

---

### User Story 2 - Infraestrutura isolada e sem telemetria (Priority: P1)

A pessoa que processa documentos sensíveis (artigos, diretrizes clínicas) tem a garantia de que a infraestrutura só é alcançável da própria máquina e que nenhum componente envia dados para fora.

**Why this priority**: É a promessa central do projeto: nada é enviado para a internet (fonte: `README.md:4`) (fonte: `docs/constitution.md`, Princípio I). Subir os serviços sem esta garantia anula o valor do produto.

**Independent Test**: Com a infraestrutura no ar, listar as portas publicadas e as conexões de saída dos serviços; verificar que toda porta publicada está ligada só ao endereço de loopback e que nenhum serviço faz conexão de saída para a internet durante a operação.

**Acceptance Scenarios**:

1. **Given** a infraestrutura no ar, **When** se inspecionam as portas publicadas de todos os serviços (o painel de gestão do RabbitMQ só aparece quando a stack foi iniciada com `--management-panel`; por padrão a 15672 não é publicada), **Then** todas estão ligadas apenas a `127.0.0.1` e nenhuma é alcançável a partir de outra máquina da rede (cobre FR-006)
2. **Given** as imagens já baixadas e a máquina sem acesso à internet, **When** o script de subir roda, **Then** a infraestrutura sobe normalmente e fica saudável (cobre FR-007)
3. **Given** a infraestrutura em operação, **When** se observa o tráfego de saída dos serviços, **Then** nenhuma conexão para fora da máquina é aberta e nenhum serviço está configurado para enviar telemetria, relatório de uso ou verificação de atualização (cobre FR-007)
4. **Given** a configuração da stack, **When** ela é revisada, **Then** nenhum serviço publica porta em endereço diferente de loopback, mesmo que o arquivo de parâmetros seja editado apenas nos valores de porta (cobre FR-006)

---

### User Story 3 - Saber se a infraestrutura está acessível pelo `artigo2md doctor` (Priority: P2)

Quem roda `artigo2md doctor` passa a ver, além dos motores de conversão, se Postgres, RabbitMQ e a API base estão acessíveis, e recebe a instrução para subir a infraestrutura quando não estão. O `convert` continua funcionando como hoje, sem Docker e sem a infraestrutura.

**Why this priority**: O `doctor` é o ponto de diagnóstico do projeto (fonte: `docs/specs/command-line-interface/spec.md`, FR-012); a CLI sem a infraestrutura é requisito do briefing (item 13 do MVP, fonte: `docs/briefing.md`). Depende da P1 só para ver o estado "acessível"; o estado "inacessível" pode ser testado sem ela.

**Independent Test**: Rodar `artigo2md doctor` com a infraestrutura derrubada (e, em outro cenário, com ela no ar) e conferir a seção de infraestrutura; rodar `artigo2md convert` num PDF com a infraestrutura derrubada e, em outro cenário, sem Docker instalado, e conferir que o Markdown é gerado.

**Acceptance Scenarios**:

1. **Given** a infraestrutura no ar, **When** o `doctor` roda, **Then** ele mostra uma seção de infraestrutura em português com Postgres, RabbitMQ e a API base como acessíveis (cobre FR-009)
2. **Given** a infraestrutura derrubada ou o Docker ausente, **When** o `doctor` roda, **Then** a seção de infraestrutura mostra cada serviço como inacessível com a instrução de como subir a stack, sem tratar isso como erro: o código de saída continua dependendo só dos motores obrigatórios (cobre FR-009)
3. **Given** a infraestrutura derrubada, **When** o `doctor` roda, **Then** ele termina em poucos segundos, sem ficar esperando o fim de um tempo limite longo de conexão (cobre FR-009)
4. **Given** a infraestrutura derrubada e o Docker não instalado, **When** o `convert` roda num PDF, **Then** a conversão termina como hoje, sem erro, sem aviso sobre a infraestrutura e sem tentar conectar a nenhum serviço da stack (cobre FR-010)
5. **Given** a infraestrutura no ar apenas parcialmente (por exemplo, só o Postgres), **When** o `doctor` roda, **Then** cada serviço é reportado individualmente com o seu próprio estado (cobre FR-009)

---

### User Story 4 - Imagem base da API que só responde à checagem de saúde (Priority: P3)

A equipe de desenvolvimento dispõe de uma imagem base da API Python, construída a partir do código do projeto, que sobe junto com a infraestrutura e por ora apenas informa que está viva. As features seguintes (`project-api`) acrescentam as rotas de negócio sobre esta base.

**Why this priority**: Entrega o pipeline de construção e execução da API em container sem antecipar regras de negócio; tem valor isolado como base verificável, mas nenhum usuário final depende dela ainda.

**Independent Test**: Construir a imagem, subir só o serviço da API e consultar o endpoint de saúde; consultar qualquer outra rota e conferir que não há outra funcionalidade exposta.

**Acceptance Scenarios**:

1. **Given** a imagem construída, **When** o serviço da API sobe, **Then** o endpoint de saúde responde com sucesso e indica que o serviço está vivo (cobre FR-008)
2. **Given** o serviço da API no ar, **When** se consulta uma rota que não seja a de saúde, **Then** a resposta indica que a rota não existe: a API base não expõe nenhuma outra funcionalidade (cobre FR-008)
3. **Given** a imagem da API, **When** o processo sobe, **Then** nenhum motor de conversão pesado (Docling, pymupdf4llm) é carregado no processo da API (cobre FR-008)
4. **Given** a infraestrutura no ar, **When** se consulta o estado do healthcheck do serviço da API, **Then** ele está saudável (cobre FR-005, FR-008)

---

### User Story 5 - Licença de cada imagem e serviço confirmada em fonte oficial (Priority: P2)

Quem mantém o projeto consulta um registro que informa, para cada imagem e serviço da stack, a licença confirmada e o endereço da fonte oficial onde ela foi confirmada, e fica seguro de que nenhum componente viola o Princípio II.

**Why this priority**: A constitution exige a verificação da licença de cada novo serviço ou imagem em fonte oficial na etapa `plan` da feature que o introduz e registra a confirmação do RabbitMQ e da imagem do Postgres como pendência desta feature (fonte: `docs/constitution.md`, Princípio II e TODO do Sync Impact Report). Sem isso a feature não pode ser entregue.

**Independent Test**: Abrir o registro e conferir que cada serviço e imagem da stack aparece com licença, enquadramento no Princípio II e link da fonte oficial; conferir que nenhum item aparece com a licença apenas "presumida".

**Acceptance Scenarios**:

1. **Given** a stack definida, **When** se consulta o registro de licenças, **Then** cada serviço e imagem tem licença, indicação de uso sem modificação do código-fonte e fonte oficial consultada (cobre FR-011)
2. **Given** um serviço ou imagem cuja licença não pôde ser confirmada em fonte oficial, **When** o registro é montado, **Then** o item não é adotado como obrigatório e a pendência vira bloqueio para decisão do operador, nunca uma suposição (cobre FR-011)
3. **Given** um serviço ou imagem com licença GPL ou AGPL como componente obrigatório, **When** o registro é montado, **Then** o item é rejeitado e a decisão é levada ao operador (cobre FR-011)

---

### Edge Cases

- Runtime Docker não instalado ou daemon parado, ou `docker` presente sem o subcomando `docker compose`: os scripts terminam com código diferente de 0 e uma mensagem em português dizendo que o Docker (ou o Docker Compose) não está disponível, sem stack trace e sem repassar ao usuário a mensagem de erro em inglês do cliente do Docker; nos demais passos, a saída original do Docker Compose é exibida sem alteração, sempre acompanhada de uma mensagem do script em português que explica o resultado (cobre FR-012)
- Porta publicada já ocupada por outro programa: o script de subir não derruba nem altera o programa em conflito, informa qual porta está ocupada e qual parâmetro do arquivo de exemplo permite trocá-la (cobre FR-013)
- Serviço que não fica saudável dentro do tempo de espera do script de subir: o script termina com código diferente de 0, informa qual serviço falhou e como ver os logs, e não deixa a infraestrutura em estado parcial silencioso (cobre FR-005, FR-002)
- Apagar dados: a remoção dos volumes nunca acontece sem a opção explícita e sem confirmação (cobre FR-003)
- Arquivo de parâmetros real com credenciais: ele nunca é versionado; só o arquivo de exemplo, sem credenciais reais, entra no repositório (cobre FR-004)
- `doctor` com a infraestrutura em estado intermediário (serviços ainda subindo): o serviço que ainda não responde ao handshake do protocolo (conexão recusada, sem resposta no tempo limite ou resposta fora do protocolo) aparece como inacessível; um serviço que já responde ao handshake aparece como acessível, mesmo que o healthcheck do Docker ainda não o considere saudável, pois "acessível" é definido pelo handshake (Clarifications, dec-014). Em nenhum caso há erro nem travamento. Que o Postgres responda ao handshake durante a inicialização é hipótese a observar no teste de integração (`tasks.md` 6.2.2) (cobre FR-009)
- Suíte de testes sem Docker: os testes do `doctor` e da CLI rodam sem Docker e passam; os testes de integração que exigem a infraestrutura só rodam quando uma variável de ambiente os habilita (cobre FR-014)
- Máquina sem internet depois da instalação: subir, derrubar e usar a stack continua funcionando; só baixar ou atualizar imagens exige rede. Pedido explícito do usuário = executar o script de subir (que baixa só as imagens que faltam, na primeira execução) ou passar `--atualizar-imagens` (único caminho que busca versão nova de imagem já baixada); nenhuma outra ação do script, do `doctor` ou do `convert` baixa imagem (cobre FR-007)

## Requirements

### Functional Requirements

- **FR-001**: O sistema MUST definir a infraestrutura local da versão desktop como uma stack composta por três serviços: banco de dados Postgres, broker de mensagens RabbitMQ com o plugin de painel de gestão habilitado na imagem, e a API base do projeto. Os três serviços são descritos no arquivo de composição versionado no repositório; o painel de gestão NÃO é publicado por padrão: a publicação da porta 15672 vive num arquivo de composição adicional (`stack/compose.management.yaml`), acrescentado só pela opção `--management-panel` do script de subir (decisão do operador: block-004, dec-023 e dec-025; motivo: risco de DNS rebinding).
- **FR-002**: O sistema MUST fornecer um script para subir e outro para derrubar a stack; o script de subir MUST aguardar os serviços ficarem saudáveis (dentro de um tempo limite) e informar o resultado de cada um, e MUST ser seguro para rodar mais de uma vez; o script de derrubar sem opções MUST preservar os dados.
- **FR-003**: O sistema MUST guardar os dados do Postgres e do RabbitMQ em volumes nomeados que sobrevivem a derrubar e subir; a remoção dos volumes MUST exigir uma opção explícita do script de derrubar e uma confirmação do usuário.
- **FR-004**: O sistema MUST fornecer um arquivo de exemplo de parâmetros (`.env.example`) que lista todos os parâmetros configuráveis da stack com comentários em português e sem credenciais reais; o arquivo real de parâmetros MUST ficar fora do versionamento; sem o arquivo real, o script de subir MUST recusar a execução com instrução em português de como criá-lo. As credenciais de Postgres e RabbitMQ MUST ser preenchidas pelo usuário no arquivo real, copiado do `.env.example`; o script de subir MUST recusar a execução, com mensagem em português, enquanto alguma credencial estiver vazia ou com o valor de exemplo, ou enquanto uma senha tiver menos de 12 caracteres ou contiver `$` (o `$` seria interpolado pelo Docker Compose; fonte: `research.md` Decision 11 item 1 e S28c). As mensagens de recusa MUST citar só o nome da variável, nunca o valor. O script de subir MUST NOT gerar credenciais (a geração automática pelo app desktop, se vier, fica a cargo da `desktop-stack-control`).
- **FR-005**: O sistema MUST definir um healthcheck para cada serviço da stack, de modo que o estado "saudável" de cada um seja verificável pelo runtime Docker e pelos scripts.
- **FR-006**: O sistema MUST publicar toda porta de todos os serviços apenas no endereço de loopback `127.0.0.1`, inclusive a do painel de gestão do RabbitMQ quando ela é publicada (somente com `--management-panel`; o IP literal `127.0.0.1` é o mesmo nos dois arquivos de composição); nenhum parâmetro editável do arquivo de exemplo pode alterar o endereço de publicação para fora do loopback.
- **FR-007**: O sistema MUST NOT incluir nenhuma telemetria nem serviço remoto na stack: nenhum serviço faz conexão de saída durante a operação, os serviços MUST ser configurados para desativar relatório de uso e verificação de atualização quando existirem tais recursos, e subir, derrubar e operar a stack MUST funcionar com as imagens já baixadas e sem internet. A ausência de conexões de saída é garantida por configuração dos serviços e verificada por observação (SC-003); a stack MUST NOT depender de bloqueio de rede para cumprir este requisito.
- **FR-008**: O sistema MUST fornecer uma imagem base da API Python, construída a partir do código do projeto e executada apenas em loopback, que expõe um único endpoint de saúde e nenhuma outra rota; a API MUST NOT carregar Docling nem pymupdf4llm no processo.
- **FR-009**: O subcomando `doctor` MUST acrescentar uma seção de infraestrutura que informa, em português e para cada um de Postgres, RabbitMQ e API base, se está acessível (acessível = o handshake do protocolo ou o endpoint de saúde do serviço responde, sem credenciais; ver Clarifications); quando algum não está, MUST indicar como subir a stack; a inacessibilidade MUST NOT alterar o código de saída do `doctor` (que continua dependendo só dos motores obrigatórios, conforme `command-line-interface` FR-012) e a verificação MUST terminar em poucos segundos mesmo com todos os serviços fora do ar.
- **FR-010**: O sistema MUST manter a CLI (`convert` e `doctor`) funcionando sem a infraestrutura e sem o Docker instalado: o `convert` MUST NOT tentar conectar a nenhum serviço da stack, nem exibir aviso sobre a infraestrutura, e o comportamento dos subcomandos existentes MUST permanecer idêntico ao atual.
- **FR-011**: O sistema MUST manter um registro, no repositório, da licença de cada serviço e imagem da stack, com o enquadramento no Princípio II (permissiva, ou copyleft fraco usado sem modificação do código-fonte) e o endereço da fonte oficial consultada; o enquadramento considera a licença do software do serviço, e a licença do sistema operacional base de cada imagem MUST constar no registro apenas como informação documentada (ver Clarifications); nenhum item MUST ser adotado como obrigatório sem licença confirmada em fonte oficial, e um item GPL ou AGPL obrigatório MUST ser rejeitado.
- **FR-012**: O sistema MUST emitir em português todas as mensagens dos scripts, e MUST tratar Docker ausente ou daemon parado com mensagem clara e código de saída diferente de 0.
- **FR-013**: O sistema MUST detectar porta já ocupada no momento de subir, terminar com código diferente de 0, informar a porta ocupada e o parâmetro que permite trocá-la, sem afetar o programa que a ocupa.
- **FR-014**: O sistema MUST cobrir a feature com testes automatizados: os testes do `doctor` e da CLI MUST rodar sem Docker; os testes de integração que exigem a infraestrutura no ar MUST ficar atrás de uma variável de ambiente, conforme o Princípio IV da constitution.
- **FR-015**: O sistema MUST permitir reexecutar o script de subir sobre uma stack já no ar sem duplicar serviços, recriar volumes nem perder dados.

> Decisões de infraestrutura: backup/restore, rotação de chaves, scheduling e mutex multi-pod não se aplicam (stack local, monousuário, sem réplicas, sem dados de produção); a persistência dos dados em volumes nomeados é tratada por FR-003. Rotação e armazenamento seguro das credenciais da stack ficam a cargo do usuário (FR-004) e, se vier, da `desktop-stack-control`.

### Clarifications

#### Session 2026-11-24

- Q: Como as credenciais de Postgres e RabbitMQ passam a existir no `.env` real? → A: o usuário as preenche a partir do `.env.example`; o script de subir recusa credencial vazia ou com valor de exemplo e não gera credenciais (feature-00c, clarify-answerer, dec-009; fonte: FR-004 e US1 cenário 5)
- Q: Como a ausência de conexões de saída (FR-007) é garantida? → A: por configuração dos serviços (telemetria e verificação de atualização desligadas) e verificação por observação (SC-003); sem bloqueio de rede na stack. Reavaliável no `plan` se a documentação oficial confirmar a viabilidade de uma rede sem saída compatível com portas em loopback (feature-00c, clarify-answerer, dec-010; fonte: FR-007 e SC-003)
- Q: Ao aplicar o Princípio II a uma "imagem", vale a licença do software do serviço ou também a do sistema operacional base da imagem? → A: vale a licença do software do serviço (Postgres, RabbitMQ, API base) para o enquadramento no Princípio II; a licença do sistema operacional base de cada imagem é apenas documentada no registro de licenças (FR-011), sem entrar no enquadramento nem causar rejeição. Esta interpretação ainda precisa ser ratificada pelo operador por nota ou emenda na `docs/constitution.md` (a feature NÃO edita a constitution); até lá, é a interpretação adotada pela spec (feature-00c, operador, block-001, dec-013)
- Q: Como a spec trata a política de senha, a saída do Docker Compose, o SC-001 e o SC-003 (reconciliação das tarefas 1.1.x/1.2.x)? → A: política de senha (12+ caracteres, sem `$`) entra no FR-004; a saída do Compose é exibida sem alteração, com mensagem própria em português; SC-001 pressupõe imagens baixadas e imagem da API já construída; SC-003 é verificação manual do mantenedor, ferramenta registrada no PR com fonte; "pedido explícito" de download = executar o script de subir ou `--atualizar-imagens` (feature-00c, execute-task fase 1; fonte: checklists/security.md CHK010, CHK017, CHK018; checklists/api.md CHK021, CHK022)
- Q: Qual o critério de "acessível" que o `doctor` usa para Postgres, RabbitMQ e API base? → A: acessível = o handshake do protocolo ou o endpoint de saúde do serviço responde, sem credenciais (não basta conexão TCP aberta; não há checagem autenticada). As portas e os endpoints concretos vêm do `plan`, a partir de fonte oficial (feature-00c, operador, block-002, dec-014; fonte: FR-009)

### Key Entities

- **Stack local**: o conjunto de serviços (Postgres, RabbitMQ com o painel de gestão habilitado mas não publicado por padrão, API base) descrito pelo arquivo de composição (mais o `compose.management.yaml` quando `--management-panel` é usado), com seu estado (no ar, saudável, derrubada).
- **Serviço**: cada componente da stack, com imagem, healthcheck, porta publicada em loopback e volume (quando guarda dados).
- **Parâmetros de ambiente**: o conjunto de valores configuráveis (portas, credenciais, nomes) listados no arquivo de exemplo e preenchidos no arquivo real não versionado.
- **Registro de licenças**: tabela por serviço e imagem com licença, enquadramento no Princípio II e fonte oficial consultada.
- **Relatório de infraestrutura do `doctor`**: estado de acessibilidade de cada serviço apresentado ao usuário.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Numa máquina com runtime Docker instalado, com as imagens oficiais já baixadas e a imagem local da API (`artigo2md-api:local`) já construída, 100% das execuções do script de subir a partir do zero (sem containers do projeto) terminam com todos os serviços saudáveis, em até 2 minutos. O download das imagens e a primeira construção da imagem da API ficam fora da medição.
- **SC-002**: Em 100% das verificações, toda porta publicada pela stack está ligada apenas a `127.0.0.1`, e nenhuma é alcançável de outra máquina da rede.
- **SC-003**: Zero conexões de saída para a internet são registradas pelos serviços da stack durante 10 minutos de operação ociosa, com as imagens já baixadas. Método de observação: verificação manual feita pelo mantenedor antes do merge, com captura de tráfego no host (interface de rede dos containers), fora da suíte automatizada; a ferramenta usada e a fonte oficial dela são registradas no PR (nenhuma ferramenta é presumida por esta spec).
- **SC-004**: Após derrubar e subir a stack 3 vezes seguidas, 100% dos dados gravados antes continuam disponíveis.
- **SC-005**: Com a infraestrutura fora do ar, o `artigo2md doctor` termina em até 5 segundos e o `artigo2md convert` produz o mesmo resultado que produzia antes desta feature, em 100% dos casos testados.
- **SC-006**: 100% dos serviços e imagens da stack constam no registro de licenças com fonte oficial citada e enquadramento no Princípio II; zero itens com licença não confirmada.
- **SC-007**: A suíte `uv run pytest` passa sem Docker instalado; os testes de integração com a stack passam quando habilitados pela variável de ambiente.

## Delta Requirements

**Skip**: o corpus canônico `docs/specs/current/` não existe neste projeto, e o acréscimo da seção de infraestrutura ao `doctor` é aditivo (FR-012 de `command-line-interface` permanece válido) — feature-00c, 2026-11-24
