# Data Model: conversion-engines

Estruturas em memória; nada é persistido pelos adaptadores.

## Entity: EngineOutput

Resultado devolvido por um adaptador de motor. (fonte: `src/artigo2md/engines.py:25`)

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| engine | string | | Identificador do motor que produziu a saída; o Docling com OCR acrescenta um sufixo (fonte: `src/artigo2md/engines.py:82`) |
| markdown | string | | Markdown produzido |
| tables | int ou nulo | padrão nulo | Tabelas estruturadas pelo motor; nulo quando o motor não informa |
| extra | dict | padrão vazio | Dados adicionais do motor |

## Entity: ficha de motor

Registro descritivo de cada motor, mostrado na interface e no diagnóstico. (fonte: `src/artigo2md/engines.py:189`)

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | string | docling, markitdown, pymupdf ou mineru | Ordem fixa da lista (fonte: `tests/test_core.py:45`) |
| name | string | | Nome de exibição |
| license | string | | Licença do motor |
| required | bool | | Verdadeiro para os motores incluídos |
| role | string | | Papel do motor |
| weight | string | | Peso de instalação |
| install | string ou nulo | nulo nos obrigatórios | Instrução de instalação dos opcionais |
| installed | bool | acrescentado na consulta | Estado de instalação na máquina (fonte: `src/artigo2md/engines.py:207`) |
| detail | string ou nulo | acrescentado na consulta | Detalhe quando o MinerU está instalado em versão incompatível (fonte: `src/artigo2md/engines.py:209`) |

### Relationships

- Nenhum relacionamento entre as duas estruturas.

### State Transitions (if applicable)

N/A — estruturas sem ciclo de vida.
