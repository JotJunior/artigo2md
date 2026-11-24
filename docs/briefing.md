# Project Briefing: artigo2md

**Data**: 2026-11-23
**Status**: Draft
**Versao**: 2.0 (pivô para app desktop macOS; versão 1.0 de 2026-11-20)

---

## 1. Visao e Proposito

**O que e**: Conversor de PDFs (artigos científicos, livros, diretrizes, capítulos) em Markdown, executado no computador do próprio usuário. (fonte: `README.md:3`) Passa a ter uma versão desktop, exclusiva para Apple por enquanto: um gerenciador de pastas com representação visual dos arquivos e um visualizador dos MDs. Nela é possível criar projetos e indicar ou fazer upload das pastas e arquivos a processar. (informado pelo operador)

**Problema que resolve**: Segundo o README, a conversão por um único motor pode sair ruim (exemplo dado: o artigo cita "Tabela 2" e nenhuma tabela saiu); o artigo2md inspeciona cada PDF, envia ao motor mais adequado, avalia a qualidade e tenta o próximo motor quando o resultado fica ruim. (fonte: `README.md:6`) A versão desktop resolve também a organização: processar pastas inteiras em paralelo e guardar o histórico e os resultados de cada conversão. (informado pelo operador)

**Proposta de valor**: O README afirma que nada é enviado para a internet e que não há custo por página. (fonte: `README.md:4`)

## 2. Usuarios e Stakeholders

| Ator | Papel | Acoes Principais |
|------|-------|-----------------|
| Pesquisador/estudante | Usuário final | Converte PDFs acadêmicos para ler, anotar ou alimentar LLMs, no próprio computador (informado pelo operador) |
| Profissional de saúde | Usuário final | Converte artigos e diretrizes clínicas para consulta local (informado pelo operador) |
| Desenvolvedor | Usuário técnico | Usa a CLI em lote/automação para preparar corpus em Markdown (informado pelo operador) |

O app desktop atende os mesmos usuários de hoje, inclusive os não técnicos. Por isso o app precisa guiar a instalação do runtime Docker, que é pré-requisito. (informado pelo operador)

O app é monousuário e local: não tem login nem contas, cada Mac tem o seu banco e nada é compartilhado entre máquinas. (informado pelo operador)

**Stakeholders de decisao**: O titular do copyright citado no LICENSE decide prioridades e aprova entregas (informado pelo operador) (fonte: `LICENSE:3`)

## 3. Escopo

### MVP (Essencial)

Capacidades já presentes no projeto:

1. Inspeção do PDF antes da conversão: páginas escaneadas, tabelas e fórmulas (fonte: `README.md:6`) (fonte: `tests/test_core.py:9`)
2. Escolha automática do motor, avaliação de qualidade e fallback para o próximo motor, mantendo o resultado de maior nota (fonte: `README.md:79`) (fonte: `tests/test_core.py:116`)
3. Quatro motores: Docling e MarkItDown incluídos; pymupdf4llm e MinerU 3.x opcionais (fonte: `README.md:13`) (fonte: `tests/test_core.py:75`)
4. Linha de comando com os subcomandos convert, serve e doctor (fonte: `CLAUDE.md:10`) (fonte: `README.md:68`)
5. Interface web: envio de PDFs, fila, visualização do Markdown e download de um arquivo .md ou de todos em .zip (fonte: `README.md:55`) (fonte: `tests/test_web.py:29`)

Capacidades novas da versão desktop (informado pelo operador):

6. App desktop para macOS
7. Criação de projetos; em cada projeto, indicar ou fazer upload das pastas e arquivos a processar
8. Gerenciador das pastas com representação visual dos arquivos e do status de cada um [status por arquivo: inferido]
9. Visualizador dos MDs
10. Processamento em paralelo gerenciado por filas do RabbitMQ
11. Banco Postgres para salvar os históricos e os resultados
12. Stack dockerizada
13. A CLI (`convert`, `doctor`) continua funcionando sem a infraestrutura Docker (informado pelo operador)
14. Distribuição por `.dmg` não assinado, com instruções para abrir pelo Gatekeeper (informado pelo operador)

### Pos-MVP (Desejavel)

1. Assinatura e notarização do app pela Apple (informado pelo operador)

### Fora de Escopo

- Windows e Linux para o app desktop, por enquanto (informado pelo operador)
- Nuvem: nenhum serviço remoto, nenhuma hospedagem (informado pelo operador)
- Login, multiusuário e sincronização entre máquinas (informado pelo operador)
- O README registra que o artigo2md recusa o MinerU 4.x (fonte: `README.md:108`)

## 4. Prioridades e Trade-offs

**Ordem de prioridade**: 1º qualidade e robustez; 2º experiência do usuário (informado pelo operador); demais posições: [PENDENTE: ordem entre velocidade de entrega e escopo]

**Decisoes explicitas**:
- Segundo o README, os motores AGPL são opcionais, nunca dependência obrigatória, e por isso o projeto continua MIT (fonte: `README.md:20`)
- Segundo o CLAUDE.md, a inspeção usa pypdfium2 e não PyMuPDF por causa da licença (fonte: `CLAUDE.md:36`)
- A interface web atual processa uma conversão por vez (fonte: `CLAUDE.md:11`); na versão desktop o processamento passa a ser paralelo, por fila (informado pelo operador)
- App desktop em Tauri 2 em vez de SwiftUI ou Electron. Motivos: o visualizador renderiza tabelas e fórmulas com bibliotecas web, o app fica leve, e Windows e Linux continuam possíveis no futuro (informado pelo operador)
- Arquitetura híbrida: Postgres, RabbitMQ e a API rodam no Docker, e os workers de conversão rodam nativos no macOS. Num container, o Docling não tem acesso à GPU Apple (MPS) nem ao OCR do Vision (ocrmac) (informado pelo operador)
- Os workers não acessam o banco. Recebem os jobs pela fila e publicam eventos de progresso e de resultado, e quem grava no Postgres é a API (informado pelo operador)

## 5. Restricoes

| Restricao | Valor | Notas |
|-----------|-------|-------|
| Prazo | flexivel — sem prazo fixo (informado pelo operador) | Projeto open source comunitário (informado pelo operador) |
| Equipe | Mantenedor + colaboradores voluntários (informado pelo operador) | O LICENSE cita "e colaboradores" (fonte: `LICENSE:3`) |
| Budget | Sem orçamento (informado pelo operador) | O README afirma que não há custo por página (fonte: `README.md:4`). Por isso não há conta Apple Developer e o `.dmg` não é assinado no MVP (informado pelo operador) |
| Tecnica | Processamento 100% local, sem envio de documentos a serviços externos; dependências obrigatórias só com licença permissiva (MIT/Apache/BSD) (informado pelo operador) | O CLAUDE.md descreve as mesmas regras (fonte: `CLAUDE.md:34`); Python >= 3.10 (fonte: `pyproject.toml:7`) |
| Plataforma | App desktop só para macOS por enquanto; runtime Docker instalado é pré-requisito (informado pelo operador) | — |

## 6. Stack Tecnica

| Camada | Tecnologia | Justificativa |
|--------|-----------|---------------|
| Desktop | Tauri 2: Rust e interface web (informado pelo operador) | Visualizador de MD com bibliotecas web para tabelas e LaTeX; app leve (informado pelo operador) |
| Backend | Python >= 3.10 (fonte: `pyproject.toml:7`); FastAPI e uvicorn (fonte: `pyproject.toml:12`) | A API da versão desktop roda em container (informado pelo operador) |
| Frontend (legado) | Página estática index.html servida pela aplicação web (fonte: `CLAUDE.md:11`) | O destino do `serve` legado será decidido no fim da migração (informado pelo operador) |
| Fila | RabbitMQ em container (informado pelo operador) | Gerencia os processamentos em paralelo (informado pelo operador) |
| Banco de dados | Postgres em container (informado pelo operador) | Salva os históricos e os resultados (informado pelo operador). Hoje não há banco no manifesto (fonte: `pyproject.toml:8`) |
| Workers | Processos nativos no macOS, que reusam o roteador e os motores atuais (informado pelo operador) | Acesso a MPS e ocrmac (informado pelo operador) |
| Infraestrutura | Docker Compose local com serviços só em loopback (informado pelo operador); execução local com uv (fonte: `README.md:34`); build com hatchling (fonte: `pyproject.toml:27`); pacote na versão 0.1.0 (fonte: `pyproject.toml:3`), licença MIT (fonte: `pyproject.toml:6`) | Testado em macOS com Apple Silicon e Linux, segundo o README (fonte: `README.md:35`) |
| Integracoes | Docling, pypdfium2 e MarkItDown como dependências obrigatórias (fonte: `pyproject.toml:9`); ocrmac no macOS (fonte: `pyproject.toml:15`); pymupdf4llm como extra opcional (fonte: `pyproject.toml:20`); MinerU 3.x chamado como processo externo (fonte: `CLAUDE.md:8`) | Papel de cada motor descrito na tabela do README (fonte: `README.md:13`) |

## 7. Qualidade e Padroes

**Padroes adotados**:
- Testes automatizados com pytest, com a suíte no diretório tests (fonte: `pyproject.toml:34`)
- A suíte de testes passa antes de qualquer commit (informado pelo operador)
- Mensagens ao usuário em português, inclusive na interface do app desktop (informado pelo operador)
- O teste real com Docling é opcional, habilitado pela variável de ambiente ARTIGO2MD_SLOW (fonte: `tests/test_core.py:129`)
- Cada conversão recebe uma nota de qualidade; segundo o README, abaixo de 65/100 o próximo motor é tentado (fonte: `README.md:83`)

**Compliance**: nenhum especifico (informado pelo operador)

## 8. Visao de Futuro

**6 meses**: Versão desktop macOS entregue com projetos, fila paralela, histórico e visualizador (informado pelo operador); continuar crescendo em features: novos formatos, motores ou recursos de qualidade (informado pelo operador)

**12 meses**: Possível suporte a Windows e Linux no app desktop, já que a exclusividade Apple é "por enquanto" (informado pelo operador) [inferido: horizonte de 12 meses]

**Riscos conhecidos**:
- Mudanças nos motores de terceiros: versões novas podem quebrar a conversão ou mudar licença/telemetria (informado pelo operador). O README registra que o MinerU 4.x é recusado (fonte: `README.md:108`) e que o MinerU 3.4.5 usa o pacote six sem declará-lo (fonte: `README.md:109`)
- Peso dos modelos: downloads de vários GB e tempo de conversão podem afastar usuários (informado pelo operador). O README cita cerca de 3 GB livres para os modelos do Docling (fonte: `README.md:34`)
- Sem autenticação: exposição indevida da interface web na rede (informado pelo operador). O README afirma que não há senha (fonte: `README.md:59`)
- O Docker é pré-requisito para usuários não técnicos: a instalação pode afastá-los se não for bem guiada (informado pelo operador)
- RAM: vários workers em paralelo carregam os modelos ao mesmo tempo [inferido]

---

## Itens a Definir

| Item | Dimensao | Impacto |
|------|----------|---------|
| Ordem entre velocidade de entrega e escopo, depois de qualidade e experiência do usuário | Prioridades e Trade-offs | Baixo |
| [PENDENTE: quais features (formatos, motores, recursos de qualidade) compõem o crescimento previsto para 6 a 12 meses?] | Visao de Futuro | Medio |
| Número padrão de workers em paralelo, que depende da RAM disponível | Restricoes | Medio |

---

**Proximo passo recomendado**: `/constitution` para emendar os princípios conforme a versão desktop
