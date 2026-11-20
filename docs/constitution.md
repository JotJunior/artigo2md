# artigo2md Constitution

## Core Principles

### I. Processamento 100% Local

O artigo2md converte os documentos no computador do próprio usuário; o README afirma que nada é enviado para a internet (fonte: `README.md:4`) e o CLAUDE.md descreve essa promessa do projeto (fonte: `CLAUDE.md:32`). Princípio ratificado (informado pelo operador).

**MUST:**

- Nenhum documento do usuário é enviado a serviços externos.

### II. Dependências Obrigatórias com Licença Permissiva

O projeto é MIT (fonte: `pyproject.toml:6`) e, segundo o README, os motores AGPL são opcionais (fonte: `README.md:20`). Princípio ratificado (informado pelo operador).

**MUST:**

- Toda dependência obrigatória tem licença permissiva (MIT, Apache ou BSD).

**MUST NOT:**

- Nenhuma dependência AGPL ou GPL entra como obrigatória no `pyproject.toml`; motores AGPL permanecem opcionais.

### III. Mensagens ao Usuário em Português

O CLAUDE.md descreve essa regra do projeto (fonte: `CLAUDE.md:35`). Princípio ratificado (informado pelo operador).

**MUST:**

- As mensagens ao usuário são escritas em português.

### IV. Testes Antes do Commit

O CLAUDE.md descreve essa regra do projeto (fonte: `CLAUDE.md:36`). Princípio ratificado (informado pelo operador).

**MUST:**

- A suíte de testes (`uv run pytest`) passa antes de qualquer commit.

### V. Veracidade de Dados — Zero Fabricação (NON-NEGOTIABLE)

Princípio-base proposto pelo cstk e ratificado (informado pelo operador).

**MUST:**

- Nenhum artefato pode conter dado factual inventado. Assinaturas de request/response,
  URLs/endpoints/querystrings e valores concretos (financeiros, quantidades, status,
  IDs, datas, resultados de API) só podem ser escritos se vierem de fonte rastreável
  (código, OpenAPI/Swagger, doc oficial, resposta real observada). Esgotadas as fontes,
  a ação correta é **bloqueio humano**, nunca suposição plausível. Plausibilidade não é
  veracidade.

## Governance

- Esta constitution prevalece sobre convenções contrárias encontradas no projeto.
- Emendas são aprovadas pelo stakeholder de decisão registrado em `docs/briefing.md` e entram por inserção, com Sync Impact Report no topo do arquivo.
- Versionamento semântico: MAJOR para remoção ou redefinição incompatível de princípio; MINOR para novo princípio ou expansão material de seção; PATCH para clarificações de texto.
- Exceção a um princípio exige decisão registrada do stakeholder de decisão antes da mudança.

**Version**: 1.0.0 | **Ratified**: 2026-11-20 | **Last Amended**: 2026-11-20
