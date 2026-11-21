# Contracts: command-line-interface

Contrato da linha de comando `artigo2md`. Tudo o que não está marcado como proposta foi extraído do código existente (fonte: `src/artigo2md/cli.py:112`).

## Opção global

| Opção | Efeito |
|-------|--------|
| `--version` | Mostra a versão e encerra (fonte: `src/artigo2md/cli.py:114`) |

Um subcomando é obrigatório (fonte: `src/artigo2md/cli.py:115`).

## `artigo2md convert`

Converte PDFs ou pastas de PDFs. (fonte: `src/artigo2md/cli.py:117`)

### Argumentos e opções

| Nome | Tipo | Obrigatório | Padrão | Descrição |
|------|------|-------------|--------|-----------|
| `inputs` | um ou mais caminhos | sim | — | Arquivos pdf e/ou pastas, com busca recursiva (fonte: `src/artigo2md/cli.py:118`) |
| `-o`, `--output` | caminho | não | `markdown` | Pasta de saída (fonte: `src/artigo2md/cli.py:119`) |
| `-e`, `--engine` | auto, docling, pymupdf, mineru, markitdown | não | `auto` | Motor de conversão (fonte: `src/artigo2md/cli.py:120`) |
| `--overwrite` | flag | não | desligada | Reconverte mesmo se o Markdown já existir (fonte: `src/artigo2md/cli.py:121`) |
| `--report` | flag | não | desligada | Grava também um relatório JSON por arquivo (fonte: `src/artigo2md/cli.py:122`) |

### Saídas

| Saída | Regra |
|-------|-------|
| Markdown | Para PDF encontrado dentro de uma pasta de entrada, o caminho relativo a essa pasta é repetido dentro da pasta de saída, com extensão md (fonte: `src/artigo2md/cli.py:25`); PDF passado diretamente vai para a raiz da pasta de saída (fonte: `src/artigo2md/cli.py:27`) |
| Relatório | Ao lado do Markdown, com o sufixo `.report.json` (fonte: `src/artigo2md/cli.py:58`) |

Colisão entre entradas diferentes da mesma execução (dois arquivos homônimos passados diretamente, ou duas pastas com o mesmo caminho relativo): ainda sem regra; hoje a segunda saída é pulada como já existente (fonte: `src/artigo2md/cli.py:45`). Ver research.md, Decision 2.

### Códigos de saída

| Código | Condição |
|--------|----------|
| 1 | Nenhum PDF encontrado nas entradas (fonte: `src/artigo2md/cli.py:37`) |
| 1 | Ao menos um PDF falhou na conversão (fonte: `src/artigo2md/cli.py:65`) |
| 0 | Demais casos, inclusive quando todos os PDFs são pulados por já terem Markdown (fonte: `src/artigo2md/cli.py:65`) |

## `artigo2md serve`

Abre a interface web. (fonte: `src/artigo2md/cli.py:125`)

### Opções

| Nome | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--host` | endereço | `127.0.0.1` | Endereço de escuta (fonte: `src/artigo2md/cli.py:126`) |
| `--port` | inteiro | `8777` | Porta (fonte: `src/artigo2md/cli.py:127`) |
| `--data-dir` | caminho | pasta `artigo2md-dados` no diretório do usuário | Onde ficam envios e resultados (fonte: `src/artigo2md/cli.py:128`) |
| `--open` | flag | desligada | Abre o navegador automaticamente (fonte: `src/artigo2md/cli.py:129`) |

### Códigos de saída

| Código | Condição |
|--------|----------|
| 1 | A porta já está em uso; a mensagem sugere a porta seguinte (fonte: `src/artigo2md/cli.py:76`) |
| 0 | O servidor foi encerrado (fonte: `src/artigo2md/cli.py:91`) |

## `artigo2md doctor`

Verifica a instalação e os motores disponíveis. (fonte: `src/artigo2md/cli.py:132`)

### Saída

Uma linha de cabeçalho com versão, Python e plataforma, uma linha por motor com nome, licença e situação, e uma linha final de conclusão (fonte: `src/artigo2md/cli.py:98`).

### Códigos de saída

| Código | Condição |
|--------|----------|
| 0 | Todos os motores obrigatórios instalados (fonte: `src/artigo2md/cli.py:109`) |
| 1 | Algum motor obrigatório ausente (fonte: `src/artigo2md/cli.py:109`) |
