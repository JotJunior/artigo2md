# Contracts: command-line-interface

Contrato da linha de comando `artigo2md`. Tudo o que não está marcado como proposta foi extraído do código existente (fonte: `src/artigo2md/cli.py:104`).

## Opção global

| Opção | Efeito |
|-------|--------|
| `--version` | Mostra a versão e encerra (fonte: `src/artigo2md/cli.py:106`) |

Um subcomando é obrigatório (fonte: `src/artigo2md/cli.py:107`).

## `artigo2md convert`

Converte PDFs ou pastas de PDFs. (fonte: `src/artigo2md/cli.py:109`)

### Argumentos e opções

| Nome | Tipo | Obrigatório | Padrão | Descrição |
|------|------|-------------|--------|-----------|
| `inputs` | um ou mais caminhos | sim | — | Arquivos pdf e/ou pastas, com busca recursiva (fonte: `src/artigo2md/cli.py:110`) |
| `-o`, `--output` | caminho | não | `markdown` | Pasta de saída (fonte: `src/artigo2md/cli.py:111`) |
| `-e`, `--engine` | auto, docling, pymupdf, mineru, markitdown | não | `auto` | Motor de conversão (fonte: `src/artigo2md/cli.py:112`) |
| `--overwrite` | flag | não | desligada | Reconverte mesmo se o Markdown já existir (fonte: `src/artigo2md/cli.py:113`) |
| `--report` | flag | não | desligada | Grava também um relatório JSON por arquivo (fonte: `src/artigo2md/cli.py:114`) |

### Saídas

| Saída | Regra atual | Regra após FR-014 |
|-------|-------------|-------------------|
| Markdown | nome do PDF com extensão md, direto na pasta de saída (fonte: `src/artigo2md/cli.py:37`) | `[PROPOSTA — a validar na implementacao]` para PDF encontrado dentro de uma pasta de entrada, o caminho relativo a essa pasta é repetido dentro da pasta de saída; PDF passado diretamente continua na raiz da pasta de saída |
| Relatório | ao lado do Markdown, com o sufixo `.report.json` (fonte: `src/artigo2md/cli.py:50`) | sem mudança de regra: continua ao lado do Markdown |

### Códigos de saída

| Código | Condição |
|--------|----------|
| 1 | Nenhum PDF encontrado nas entradas (fonte: `src/artigo2md/cli.py:30`) |
| 1 | Ao menos um PDF falhou na conversão (fonte: `src/artigo2md/cli.py:57`) |
| 0 | Demais casos, inclusive quando todos os PDFs são pulados por já terem Markdown (fonte: `src/artigo2md/cli.py:57`) |

## `artigo2md serve`

Abre a interface web. (fonte: `src/artigo2md/cli.py:117`)

### Opções

| Nome | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--host` | endereço | `127.0.0.1` | Endereço de escuta (fonte: `src/artigo2md/cli.py:118`) |
| `--port` | inteiro | `8777` | Porta (fonte: `src/artigo2md/cli.py:119`) |
| `--data-dir` | caminho | pasta `artigo2md-dados` no diretório do usuário | Onde ficam envios e resultados (fonte: `src/artigo2md/cli.py:120`) |
| `--open` | flag | desligada | Abre o navegador automaticamente (fonte: `src/artigo2md/cli.py:121`) |

### Códigos de saída

| Código | Condição |
|--------|----------|
| 1 | A porta já está em uso; a mensagem sugere a porta seguinte (fonte: `src/artigo2md/cli.py:68`) |
| 0 | O servidor foi encerrado (fonte: `src/artigo2md/cli.py:83`) |

## `artigo2md doctor`

Verifica a instalação e os motores disponíveis. (fonte: `src/artigo2md/cli.py:124`)

### Saída

Uma linha de cabeçalho com versão, Python e plataforma, uma linha por motor com nome, licença e situação, e uma linha final de conclusão (fonte: `src/artigo2md/cli.py:90`).

### Códigos de saída

| Código | Condição |
|--------|----------|
| 0 | Todos os motores obrigatórios instalados (fonte: `src/artigo2md/cli.py:101`) |
| 1 | Algum motor obrigatório ausente (fonte: `src/artigo2md/cli.py:101`) |
