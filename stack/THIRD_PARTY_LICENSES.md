# Licenças de terceiros da infraestrutura local

Registro exigido pela FR-011 da feature `container-stack`: cada serviço, runtime, ferramenta de build e
dependência Python embarcada na stack, com a licença **como escrita na fonte oficial**, o enquadramento no
Princípio II da constitution e a fonte consultada. Nada aqui foi presumido: item sem fonte lida não entra
no registro (vira pendência para o operador).

Enquadramento (Princípio II): `permissiva` · `copyleft fraco sem modificação` · `documentado (SO base, dec-013)`
(licença do sistema operacional base, só informada, sem entrar no enquadramento) · `rejeitado` (GPL/AGPL
obrigatório, exige decisão do operador). Hoje nenhum item está `rejeitado`.

Os READMEs das três imagens oficiais avisam que elas "likely also contain other software which may be under
other licenses" e que verificar a conformidade é responsabilidade de quem usa a imagem; isso é registrado
aqui como informação.

## Serviços, runtimes e ferramentas de build

| Componente | Imagem | Licença (como na fonte) | Princípio II | Fonte oficial | Consultado em |
|---|---|---|---|---|---|
| PostgreSQL (servidor) | `postgres:18-trixie` | PostgreSQL License: "a liberal Open Source license, similar to the BSD or MIT licenses" | permissiva | https://www.postgresql.org/about/licence/ | 2026-11-24 |
| RabbitMQ (servidor e plugins tier 1, inclui o painel de gestão) | `rabbitmq:4.4-management` | MPL 2.0 ("source code is licensed under the MPL 2.0"); alguns arquivos OCF Apache-2.0 | copyleft fraco sem modificação | https://raw.githubusercontent.com/rabbitmq/rabbitmq-server/main/LICENSE | 2026-11-24 |
| Erlang/OTP (runtime da imagem do RabbitMQ) | `rabbitmq:4.4-management` | Apache License 2.0 | permissiva | https://raw.githubusercontent.com/erlang/otp/master/LICENSE.txt | 2026-11-24 |
| OpenSSL 3.5.9 (imagem do RabbitMQ) | `rabbitmq:4.4-management` | "For the 3.0 release, and later ... the Apache License v2 applies" | permissiva | https://openssl-library.org/source/license/ | 2026-11-24 |
| Python 3.13 (runtime da API) | `python:3.13-slim-trixie` | Python Software Foundation License Version 2 | permissiva | https://docs.python.org/3/license.html | 2026-11-24 |
| artigo2md (código da API base) | `artigo2md-api:local` | MIT | permissiva | `pyproject.toml` (campo `license`) | 2026-11-24 |
| uv 0.13.9 (só ferramenta de build) | `ghcr.io/astral-sh/uv:0.13.9` | dupla MIT / Apache-2.0 ("licensed under either of") | permissiva | https://raw.githubusercontent.com/astral-sh/uv/main/README.md | 2026-11-24 |

## Sistema operacional base das imagens (apenas documentado, dec-013)

| Componente | Imagem | Licença (como na fonte) | Princípio II | Fonte oficial | Consultado em |
|---|---|---|---|---|---|
| Debian trixie (SO base) | `postgres:18-trixie` e `python:3.13-slim-trixie` | "the Debian system and all its components will be free"; seção main só com software livre | documentado (SO base, dec-013) | https://www.debian.org/social_contract e https://www.debian.org/legal/licenses/ | 2026-11-24 |
| Ubuntu 26.04 (SO base) | `rabbitmq:4.4-management` | "an aggregate work of many works, each covered by their own licence(s)" | documentado (SO base, dec-013) | https://canonical.com/legal/intellectual-property-policy | 2026-11-24 |

## Dependências Python da imagem da API (grupo `api`, Python 3.13)

Lista derivada do `uv.lock` (`uv export --frozen --only-group api`), nas versões resolvidas para Python 3.13
(as variantes para Python anterior a 3.11 do lock não entram na imagem). Licença lida do campo
`license_expression` (ou `license`) da página oficial do pacote no PyPI; a do `pydantic` vem da página
https://pypi.org/project/pydantic/2.14.4/ (campo "License expression"), porque o JSON dele excede o limite de leitura.

| Componente | Imagem | Licença (como na fonte) | Princípio II | Fonte oficial | Consultado em |
|---|---|---|---|---|---|
| fastapi 0.165.0 | `artigo2md-api:local` | MIT | permissiva | https://pypi.org/pypi/fastapi/0.165.0/json | 2026-11-24 |
| uvicorn 0.54.0 | `artigo2md-api:local` | BSD-3-Clause | permissiva | https://pypi.org/pypi/uvicorn/0.54.0/json | 2026-11-24 |
| starlette 1.10.0 | `artigo2md-api:local` | BSD-3-Clause | permissiva | https://pypi.org/pypi/starlette/1.10.0/json | 2026-11-24 |
| pydantic 2.14.4 | `artigo2md-api:local` | MIT | permissiva | https://pypi.org/project/pydantic/2.14.4/ | 2026-11-24 |
| pydantic-core 2.50.5 | `artigo2md-api:local` | MIT | permissiva | https://pypi.org/pypi/pydantic-core/2.50.5/json | 2026-11-24 |
| annotated-types 0.8.0 | `artigo2md-api:local` | MIT | permissiva | https://pypi.org/pypi/annotated-types/0.8.0/json | 2026-11-24 |
| annotated-doc 0.0.7 | `artigo2md-api:local` | MIT | permissiva | https://pypi.org/pypi/annotated-doc/0.0.7/json | 2026-11-24 |
| anyio 4.16.0 | `artigo2md-api:local` | MIT | permissiva | https://pypi.org/pypi/anyio/4.16.0/json | 2026-11-24 |
| idna 3.20 | `artigo2md-api:local` | BSD-3-Clause | permissiva | https://pypi.org/pypi/idna/3.20/json | 2026-11-24 |
| click 8.5.1 | `artigo2md-api:local` | BSD-3-Clause | permissiva | https://pypi.org/pypi/click/8.5.1/json | 2026-11-24 |
| h11 0.16.0 | `artigo2md-api:local` | MIT | permissiva | https://pypi.org/pypi/h11/0.16.0/json | 2026-11-24 |
| typing-extensions 4.16.0 | `artigo2md-api:local` | PSF-2.0 | permissiva | https://pypi.org/pypi/typing-extensions/4.16.0/json | 2026-11-24 |
| typing-inspection 0.4.5 | `artigo2md-api:local` | MIT | permissiva | https://pypi.org/pypi/typing-inspection/0.4.5/json | 2026-11-24 |
| opentelemetry-api 1.45.2 | `artigo2md-api:local` | Apache-2.0 | permissiva | https://pypi.org/pypi/opentelemetry-api/1.45.2/json | 2026-11-24 |

## Dependências Python do worker nativo (extra `queue`)

Dependências do extra opcional `queue` do `pyproject.toml` (feature `conversion-queue`, FR-020): só são
instaladas por quem usa `artigo2md worker` (`uv sync --extra queue`) e nunca são importadas por `convert`,
`doctor` ou `serve`. O `pika` é fixado em `1.5.0` para casar com a licença verificada; qualquer upgrade exige
nova verificação e novo registro. A licença do `pika` 1.5.0 (BSD-3-Clause) foi lida pelo operador no campo
`license` da página oficial do PyPI e no arquivo LICENSE do repositório oficial `https://github.com/pika/pika`
(resposta ao bloqueio humano `block-001`, consulta dele em 2026-11-24); o agente não conseguiu consultar a rede
(guard de comandos) e conferiu apenas, localmente, que os metadados do pacote instalado do `uv.lock` declaram
`License: BSD-3-Clause`, `Requires-Python: >=3.7` e nenhuma dependência de runtime (só extras opcionais
`gevent`/`tornado`/`twisted`, não instalados). A do `psutil` 7.2.2 foi lida nos metadados do pacote resolvido
pelo `uv.lock`.

| Componente | Imagem | Licença (como na fonte) | Princípio II | Fonte oficial | Consultado em |
|---|---|---|---|---|---|
| pika 1.5.0 | worker nativo (extra `queue`) | BSD-3-Clause | permissiva | https://pypi.org/pypi/pika/json | 2026-11-24 |
| psutil 7.2.2 | worker nativo (extra `queue`) | BSD-3-Clause | permissiva | `psutil-7.2.2.dist-info/METADATA` do pacote resolvido pelo `uv.lock` | 2026-11-25 |
