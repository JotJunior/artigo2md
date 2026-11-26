<!--
Sync Impact Report (emenda 1.2.0)
- Version: 1.1.1 → 1.2.0
- Bump MINOR: expansão material do Princípio II; nenhuma obrigação anterior removida, a proibição de GPL e AGPL continua igual
- Principios modificados: II — dependências de código aceitam outras licenças permissivas além de MIT, Apache e BSD (ISC, Zlib, 0BSD, Unicode-3.0 ou outra sem cláusula de copyleft); dependência de código transitiva pode ter copyleft fraco no nível de arquivo (ex.: MPL-2.0), sem modificação e registrada no plan; copyleft fraco em dependência direta, ou além do nível de arquivo (ex.: LGPL), continua fora
- Secoes adicionadas: nenhuma
- Secoes removidas: nenhuma
- Artefatos que precisam atualizacao:
  - CLAUDE.md, seção "Regras do projeto" (ainda cita MIT/Apache/BSD): pendente; atualizar na feature desktop-release, junto com a pendência da emenda 1.1.0
  - docs/briefing.md, restrição técnica (cita MIT/Apache/BSD): pendente; registro do operador, atualizar na próxima revisão do briefing
  - feature desktop-shell: a exceção registrada no block-008 (MPL-2.0 e Unicode-3.0 transitivas do Tauri) passa a ser coberta por esta emenda
- TODOs pendentes: nenhum
- Origem: bloqueio block-008 da feature desktop-shell (grafo cargo do Tauri com 5 crates MPL-2.0 e 18 Unicode-3.0, todos transitivos); emenda pedida pelo operador em 2026-11-24
-->
<!--
Sync Impact Report (emenda 1.1.1)
- Version: 1.1.0 → 1.1.1
- Bump PATCH: clarificação do Princípio II, sem obrigação nova nem removida
- Principios modificados: II — numa imagem de container, quem decide o enquadramento da licença (inclusive a proibição de GPL/AGPL) é o software do serviço que a imagem entrega; os componentes do sistema base da imagem são documentados apenas como informação
- Secoes adicionadas: nenhuma
- Secoes removidas: nenhuma
- Artefatos que precisam atualizacao: nenhum (a feature container-stack registrou a mesma interpretação como decisão do operador)
- TODOs pendentes: nenhum
- Origem: emenda confirmada pelo operador em 2026-11-24, ao responder o bloqueio block-001 da feature container-stack
-->
<!--
Sync Impact Report (emenda 1.1.0)
- Version: 1.0.1 → 1.1.0
- Bump MINOR: expansão material dos princípios I a IV para a versão desktop macOS (briefing 2.0). Nenhum princípio foi removido ou redefinido de forma incompatível; as obrigações anteriores permanecem
- Principios modificados:
  - I (Processamento 100% Local): serviços locais só em loopback, sem serviço remoto nem telemetria, imagens baixadas só na instalação ou na atualização
  - II (Licenças): passa a cobrir dependências pip, npm e cargo e também os serviços e imagens em container; serviços sem modificação aceitam copyleft fraco (ex.: MPL-2.0); GPL e AGPL continuam proibidas como obrigatórias
  - III (Português): a regra vale explicitamente para a interface do app desktop
  - IV (Testes): entram os testes do app desktop (cargo e npm) e os testes de integração com o compose, habilitados por variável de ambiente
- Secoes adicionadas: nenhuma
- Secoes removidas: nenhuma
- Artefatos que precisam atualizacao:
  - CLAUDE.md, seção "Regras do projeto": pendente; atualizar na feature desktop-release
  - .github (CI): pendente; incluir os testes do desktop quando o diretório desktop/ existir
  - docs/specs/*: nenhum (as features existentes não tocam serviços nem o app desktop)
- TODOs pendentes:
  - Confirmar a licença do RabbitMQ e da imagem Postgres na etapa plan da feature container-stack (Princípio V)
- Origem: emenda confirmada pelo operador em 2026-11-23
-->
# artigo2md Constitution

## Core Principles

### I. Processamento 100% Local

O artigo2md converte os documentos no computador do próprio usuário. O README afirma que nada é enviado para a internet (fonte: `README.md:4`) e o CLAUDE.md descreve essa promessa do projeto (fonte: `CLAUDE.md`). Na versão desktop, Postgres, RabbitMQ e a API rodam em containers locais e os workers rodam nativos no macOS (fonte: `docs/briefing.md`). Princípio ratificado (informado pelo operador).

**MUST:**

- Nenhum documento do usuário é enviado a serviços externos.
- Todo serviço local (containers e workers) escuta apenas em loopback (`127.0.0.1`); nenhuma porta fica exposta na rede.
- Imagens de container e modelos só são baixados na instalação, na primeira execução ou numa atualização pedida pelo usuário; a conversão em si funciona sem internet.

**MUST NOT:**

- Nenhum componente usa serviço remoto ou envia telemetria.

### II. Dependências com Licença Permissiva

O projeto é MIT (fonte: `pyproject.toml:6`) e, segundo o README, os motores AGPL são opcionais (fonte: `README.md:20`). Princípio ratificado (informado pelo operador).

**MUST:**

- Toda dependência obrigatória de código (pip, npm, cargo), direta ou transitiva, tem licença permissiva: MIT, Apache, BSD, ISC, Zlib, 0BSD, Unicode-3.0 ou outra licença sem cláusula de copyleft.
- Uma dependência de código transitiva pode ter copyleft fraco no nível de arquivo (ex.: MPL-2.0) quando uma dependência direta permissiva a exige e ela entra sem modificação do seu código-fonte; o nome e a licença de cada uma ficam registrados no `plan` da feature que a introduz.
- Todo serviço ou imagem de container obrigatório tem licença permissiva ou copyleft fraco (ex.: MPL-2.0) e roda sem modificação do seu código-fonte.
- Numa imagem de container, o enquadramento acima vale para o software do serviço que a imagem entrega; a licença dos componentes do sistema base da imagem é documentada como informação e não decide o enquadramento.
- A licença de cada nova dependência, serviço ou imagem é verificada em fonte oficial na etapa `plan` da feature que a introduz.

**MUST NOT:**

- Nenhuma dependência ou serviço GPL ou AGPL entra como obrigatório, nem imagem cujo serviço seja GPL ou AGPL; motores AGPL permanecem opcionais.
- Nenhuma dependência de código direta com copyleft fraco entra como obrigatória, nem dependência de código com copyleft fraco que vá além do nível de arquivo (ex.: LGPL).

### III. Mensagens ao Usuário em Português

O CLAUDE.md descreve essa regra do projeto (fonte: `CLAUDE.md`). Princípio ratificado (informado pelo operador).

**MUST:**

- As mensagens ao usuário são escritas em português, na CLI, na interface web e na interface do app desktop.

### IV. Testes Antes do Commit

O CLAUDE.md descreve essa regra do projeto (fonte: `CLAUDE.md`). Princípio ratificado (informado pelo operador).

**MUST:**

- A suíte de testes (`uv run pytest`) passa antes de qualquer commit.
- Quando o commit toca o app desktop, os testes dele (`cargo test` e os testes do frontend via npm) passam antes do commit.
- Os testes de integração que exigem o compose no ar ficam atrás de uma variável de ambiente, como já ocorre com `ARTIGO2MD_SLOW`, e rodam antes do merge das features que tocam serviços.

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

**Version**: 1.2.0 | **Ratified**: 2026-11-20 | **Last Amended**: 2026-11-24
