# Roadmap: artigo2md

**Gerado por**: /agente-00c (modo roadmap)
**Atualizado em**: 2026-11-24

Roadmap da versão desktop macOS (briefing 2.0, constitution 1.1.0). Arquitetura híbrida: Postgres, RabbitMQ e a API rodam em Docker Compose, só em loopback; os workers de conversão rodam nativos no macOS (MPS e ocrmac), consomem a fila e não acessam o banco; o app desktop é feito em Tauri 2. Features sem dependência entre si rodam em paralelo, via `/roadmap-wave`.

## Ordem sugerida

| # | Feature | Depende de | Descricao (resumo) |
|---|---------|------------|--------------------|
| 1 | `container-stack` | - | Criar a infraestrutura local da versão desktop em Docker Compose: Postgres, RabbitMQ (com painel de gestão) e uma imagem base da API Python que por ora só responde a um endpoint de saúde. A stack usa volumes nomeados, `.env.example`, healthchecks, scripts de subir e derrubar, portas publicadas só em 127.0.0.1 e nenhuma telemetria (Princípio I). O `artigo2md doctor` passa a informar se a infraestrutura está acessível, e a CLI `convert` continua funcionando sem ela. Confirmar em fonte oficial a licença de cada imagem e serviço (Princípio II: permissiva, ou copyleft fraco usado sem modificação). |
| 2 | `desktop-shell` | - | Criar o esqueleto do app desktop macOS em Tauri 2, no diretório `desktop/`. O app tem uma janela principal com sidebar, uma tela de configurações persistidas, uma árvore somente leitura da pasta local escolhida pelo usuário e um visualizador de Markdown que renderiza tabelas GFM, fórmulas LaTeX e imagens com caminho relativo. A entrega inclui um build `.app` sem assinatura. Regras: interface toda em português (Princípio III); dependências npm e cargo só com licença MIT, Apache ou BSD (Princípio II); testes com `cargo test` e npm (Princípio IV); nenhuma conexão com backend nesta feature. |
| 3 | `job-store` | `container-stack` | Modelar a persistência da versão desktop no Postgres do compose. O schema e as migrações versionadas cobrem projetos, fontes (pastas ou arquivos indicados no lugar ou copiados para o projeto), documentos, jobs de conversão, tentativas por motor, resultados e eventos de histórico; os resultados guardam o caminho do MD, o motor, as métricas de qualidade de `router.Quality` e os avisos. A feature inclui uma camada de repositório async, com driver de licença permissiva (Princípio II), e testes de integração contra o Postgres do compose, habilitados por variável de ambiente. Os workers não acessam o banco: só a project-api usa esta camada. |
| 4 | `conversion-queue` | `container-stack` | Implementar o processamento paralelo pelo RabbitMQ do compose: topologia com exchange e fila de jobs, retry com atraso e fila de mensagens mortas, mais um contrato de mensagens JSON versionado para jobs, progresso e resultado. O comando `artigo2md worker` roda nativo no macOS, para usar MPS e ocrmac, com prefetch 1, pool configurável de N processos e proteção de RAM. Ele reusa `pdfscan.analyze`, `router.convert` e o padrão de processo auxiliar de `worker.py`, lê o PDF direto do disco do host, grava o MD no diretório de dados do app e publica eventos sem acessar o banco. Medir o uso de RAM antes de fixar o número padrão de workers. |
| 5 | `desktop-stack-control` | `container-stack`, `desktop-shell` | Fazer o app desktop cuidar da infraestrutura. O app detecta o runtime Docker (Docker Desktop, OrbStack ou colima) e, quando ele falta, guia passo a passo a instalação para usuários não técnicos. Também sobe e derruba o compose da container-stack, inicia e para o pool de workers nativos como processos auxiliares, e mostra um painel de saúde dos serviços e o progresso do download dos modelos na primeira execução. Mensagens em português e nenhum serviço remoto (Princípio I). |
| 6 | `project-api` | `job-store`, `conversion-queue` | Implementar a API FastAPI da versão desktop, em container. Ela oferece CRUD de projetos, registro de fontes (indicadas no lugar ou copiadas para o projeto), enfileiramento dos documentos no RabbitMQ, progresso ao vivo por SSE, reprocessamento com outro motor e entrega do MD e dos assets; além disso, consome os eventos dos workers e grava o histórico e os resultados via job-store. A API não enxerga o disco do host: a lista dos arquivos das pastas indicadas vem do app desktop, e só o diretório de dados do app é montado no container. Ela nunca importa o docling e escuta só em 127.0.0.1. |
| 7 | `desktop-projects` | `desktop-shell`, `project-api` | Integrar ao app desktop, via project-api, a gestão de projetos: criar, renomear e arquivar projetos; adicionar pastas e arquivos por diálogo nativo ou arrastar e soltar, escolhendo entre indicar no lugar e copiar para o projeto. Nas pastas indicadas, quem lista os PDFs é o próprio app. O app mostra a árvore visual dos arquivos com o status de cada um e abre o resultado no visualizador de Markdown. Usar só os contratos que a project-api de fato expõe. |
| 8 | `desktop-processing` | `desktop-shell`, `project-api` | Criar no app desktop a área de processamento integrada à project-api. Ela tem um monitor da fila com os jobs em paralelo e o progresso ao vivo por SSE, o histórico por documento (tentativas, motor usado, nota de qualidade e avisos), as ações de tentar de novo, cancelar e reprocessar com outro motor, e a exportação dos MDs de um projeto em zip. Usar só os contratos que a project-api de fato expõe. |
| 9 | `desktop-release` | `desktop-stack-control`, `desktop-projects`, `desktop-processing` | Fechar a versão desktop. O teste ponta a ponta cobre o fluxo completo: subir a stack pelo app, criar um projeto, adicionar uma pasta de PDFs, processar em paralelo, conferir o histórico no Postgres e abrir o MD no visualizador. Gerar um `.dmg` não assinado, com instruções para abrir pelo Gatekeeper, atualizar o README e o CLAUDE.md e incluir os testes do desktop no CI. Por fim, decidir com o operador o destino do comando `serve` legado. |

## Features

### 1. container-stack

- **short-name**: `container-stack`
- **ordem**: 1
- **depende-de**: -

**Descricao**: Criar a infraestrutura local da versão desktop em Docker Compose: Postgres, RabbitMQ (com painel de gestão) e uma imagem base da API Python que por ora só responde a um endpoint de saúde. A stack usa volumes nomeados, `.env.example`, healthchecks, scripts de subir e derrubar, portas publicadas só em 127.0.0.1 e nenhuma telemetria (Princípio I). O `artigo2md doctor` passa a informar se a infraestrutura está acessível, e a CLI `convert` continua funcionando sem ela. Confirmar em fonte oficial a licença de cada imagem e serviço (Princípio II: permissiva, ou copyleft fraco usado sem modificação).

**Justificativa**: Fila e banco são a base de todo o processamento paralelo e do histórico; job-store, conversion-queue e desktop-stack-control dependem de a infraestrutura existir e ser reproduzível na máquina do usuário.

### 2. desktop-shell

- **short-name**: `desktop-shell`
- **ordem**: 2
- **depende-de**: -

**Descricao**: Criar o esqueleto do app desktop macOS em Tauri 2, no diretório `desktop/`. O app tem uma janela principal com sidebar, uma tela de configurações persistidas, uma árvore somente leitura da pasta local escolhida pelo usuário e um visualizador de Markdown que renderiza tabelas GFM, fórmulas LaTeX e imagens com caminho relativo. A entrega inclui um build `.app` sem assinatura. Regras: interface toda em português (Princípio III); dependências npm e cargo só com licença MIT, Apache ou BSD (Princípio II); testes com `cargo test` e npm (Princípio IV); nenhuma conexão com backend nesta feature.

**Justificativa**: O gerenciador de pastas e o visualizador dos MDs são o coração da experiência desktop e podem ser construídos sem backend, em paralelo à infraestrutura.

### 3. job-store

- **short-name**: `job-store`
- **ordem**: 3
- **depende-de**: `container-stack`

**Descricao**: Modelar a persistência da versão desktop no Postgres do compose. O schema e as migrações versionadas cobrem projetos, fontes (pastas ou arquivos indicados no lugar ou copiados para o projeto), documentos, jobs de conversão, tentativas por motor, resultados e eventos de histórico; os resultados guardam o caminho do MD, o motor, as métricas de qualidade de `router.Quality` e os avisos. A feature inclui uma camada de repositório async, com driver de licença permissiva (Princípio II), e testes de integração contra o Postgres do compose, habilitados por variável de ambiente. Os workers não acessam o banco: só a project-api usa esta camada.

**Justificativa**: O operador quer salvar os históricos e os resultados de cada conversão; o modelo de dados precisa existir antes da API que o expõe.

### 4. conversion-queue

- **short-name**: `conversion-queue`
- **ordem**: 4
- **depende-de**: `container-stack`

**Descricao**: Implementar o processamento paralelo pelo RabbitMQ do compose: topologia com exchange e fila de jobs, retry com atraso e fila de mensagens mortas, mais um contrato de mensagens JSON versionado para jobs, progresso e resultado. O comando `artigo2md worker` roda nativo no macOS, para usar MPS e ocrmac, com prefetch 1, pool configurável de N processos e proteção de RAM. Ele reusa `pdfscan.analyze`, `router.convert` e o padrão de processo auxiliar de `worker.py`, lê o PDF direto do disco do host, grava o MD no diretório de dados do app e publica eventos sem acessar o banco. Medir o uso de RAM antes de fixar o número padrão de workers.

**Justificativa**: A fila é o que permite processar pastas inteiras em paralelo; manter o worker sem acesso ao banco desacopla esta feature da job-store e permite as duas em paralelo.

### 5. desktop-stack-control

- **short-name**: `desktop-stack-control`
- **ordem**: 5
- **depende-de**: `container-stack`, `desktop-shell`

**Descricao**: Fazer o app desktop cuidar da infraestrutura. O app detecta o runtime Docker (Docker Desktop, OrbStack ou colima) e, quando ele falta, guia passo a passo a instalação para usuários não técnicos. Também sobe e derruba o compose da container-stack, inicia e para o pool de workers nativos como processos auxiliares, e mostra um painel de saúde dos serviços e o progresso do download dos modelos na primeira execução. Mensagens em português e nenhum serviço remoto (Princípio I).

**Justificativa**: Os usuários de hoje incluem não técnicos; o app precisa esconder a infraestrutura e cuidar do ciclo de vida da stack e dos workers.

### 6. project-api

- **short-name**: `project-api`
- **ordem**: 6
- **depende-de**: `job-store`, `conversion-queue`

**Descricao**: Implementar a API FastAPI da versão desktop, em container. Ela oferece CRUD de projetos, registro de fontes (indicadas no lugar ou copiadas para o projeto), enfileiramento dos documentos no RabbitMQ, progresso ao vivo por SSE, reprocessamento com outro motor e entrega do MD e dos assets; além disso, consome os eventos dos workers e grava o histórico e os resultados via job-store. A API não enxerga o disco do host: a lista dos arquivos das pastas indicadas vem do app desktop, e só o diretório de dados do app é montado no container. Ela nunca importa o docling e escuta só em 127.0.0.1.

**Justificativa**: É o contrato único entre o app desktop, a fila e o banco; as telas de projetos e de processamento dependem dela.

### 7. desktop-projects

- **short-name**: `desktop-projects`
- **ordem**: 7
- **depende-de**: `desktop-shell`, `project-api`

**Descricao**: Integrar ao app desktop, via project-api, a gestão de projetos: criar, renomear e arquivar projetos; adicionar pastas e arquivos por diálogo nativo ou arrastar e soltar, escolhendo entre indicar no lugar e copiar para o projeto. Nas pastas indicadas, quem lista os PDFs é o próprio app. O app mostra a árvore visual dos arquivos com o status de cada um e abre o resultado no visualizador de Markdown. Usar só os contratos que a project-api de fato expõe.

**Justificativa**: Atende o pedido de criar projetos e neles indicar ou enviar pastas e arquivos, com representação visual dos arquivos.

### 8. desktop-processing

- **short-name**: `desktop-processing`
- **ordem**: 8
- **depende-de**: `desktop-shell`, `project-api`

**Descricao**: Criar no app desktop a área de processamento integrada à project-api. Ela tem um monitor da fila com os jobs em paralelo e o progresso ao vivo por SSE, o histórico por documento (tentativas, motor usado, nota de qualidade e avisos), as ações de tentar de novo, cancelar e reprocessar com outro motor, e a exportação dos MDs de um projeto em zip. Usar só os contratos que a project-api de fato expõe.

**Justificativa**: Dá visibilidade ao processamento paralelo e ao histórico salvo no Postgres, que são o ganho central da versão desktop.

### 9. desktop-release

- **short-name**: `desktop-release`
- **ordem**: 9
- **depende-de**: `desktop-stack-control`, `desktop-projects`, `desktop-processing`

**Descricao**: Fechar a versão desktop. O teste ponta a ponta cobre o fluxo completo: subir a stack pelo app, criar um projeto, adicionar uma pasta de PDFs, processar em paralelo, conferir o histórico no Postgres e abrir o MD no visualizador. Gerar um `.dmg` não assinado, com instruções para abrir pelo Gatekeeper, atualizar o README e o CLAUDE.md e incluir os testes do desktop no CI. Por fim, decidir com o operador o destino do comando `serve` legado.

**Justificativa**: Valida a integração de todas as features e entrega o app aos usuários.

