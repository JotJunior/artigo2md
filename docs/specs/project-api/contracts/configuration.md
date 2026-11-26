# Contract: configuração da API e mudanças na stack

> **Contrato implementado e verificado** (`src/artigo2md/api/settings.py`, `stack/compose.yaml`, `stack/.env.example`,
> `stack/up.sh`; ciclo real com a stack em `tests/test_api_integration.py`, onda-014). Variáveis novas marcadas "nova";
> as existentes citam a origem.

## Variáveis lidas pela API (`src/artigo2md/api/settings.py`)

| Variável | Status | Valor no compose | Validação | Falha |
|----------|--------|------------------|-----------|-------|
| `ARTIGO2MD_DATA_DIR_HOST` | nova | `${ARTIGO2MD_DATA_DIR}` | absoluto, sem `..`, sem `\0`, sem `/` final | rotas de arquivo → 503 `data_dir_unavailable` |
| `ARTIGO2MD_DATA_DIR_MOUNT` | nova | `/data` | absoluto, diretório existente no container | idem |
| `ARTIGO2MD_API_ALLOW_DEV_ORIGIN` | nova | `${ARTIGO2MD_API_ALLOW_DEV_ORIGIN:-}` (vazia = desligada) | `1` ou `true` (sem diferenciar maiúsculas) acrescenta `http://localhost:1420` à lista efetiva; qualquer outro valor mantém desligada | valor inválido → permanece desligada, aviso em português |
| `ARTIGO2MD_RABBITMQ_HOST` | nova | `rabbitmq` | `127.0.0.1`, `::1`, `localhost` ou `rabbitmq`; `rabbitmq` só se todos os endereços resolvidos forem loopback ou privados (mesma regra de `ARTIGO2MD_POSTGRES_HOST`, `job-store/contracts/repository.md`) | rotas que publicam → 503 `broker_unavailable` |
| `ARTIGO2MD_RABBITMQ_PORT` | existente (`src/artigo2md/infra.py:31`) | `5672` (porta do container) | 1..65535 | idem |
| `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS` | existentes (`jobqueue/settings.py:18-19`) | do `stack/.env` | não vazias | idem; mensagem cita só o nome da variável |
| `ARTIGO2MD_POSTGRES_HOST`, `ARTIGO2MD_POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | existentes (`store/config.py`, `DbSettings.from_env`) | `postgres`, `5432`, demais do `stack/.env` | regras do job-store | rotas de banco → 503 `database_unavailable` |

**Lista de origens fixa** (FR-028): a API aceita `Origin: tauri://localhost` (origem de produção do app, confirmada
pelo operador em block-001/dec-023) e, só com `ARTIGO2MD_API_ALLOW_DEV_ORIGIN`, também `http://localhost:1420`. Não
há variável que troque a lista: `ARTIGO2MD_API_ALLOWED_ORIGINS` existiu até a tarefa 13.9 e foi **removida por
decisão do operador** (dec-052, que substitui a decisão provisória dec-047); se estiver definida no ambiente, a API a
ignora.

Nenhuma falha de configuração derruba o processo: `/health` responde sempre (FR-032). Valores de senha nunca
aparecem em log nem em resposta.

## `stack/.env.example` (acréscimos)

```sh
# Pasta de dados do app (obrigatória). Caminho ABSOLUTO de uma pasta que já existe, sem link simbólico no
# caminho (o `up.sh` confere contra `pwd -P`). Os workers gravam o Markdown aqui e a API a monta em /data.
# Use exatamente o mesmo caminho em `artigo2md worker --data-dir`. Exemplo: /Users/voce/artigo2md-dados
ARTIGO2MD_DATA_DIR=

# API: só a origem do app (tauri://localhost) pode chamar a API pelo navegador; a lista é fixa.
# Só para desenvolver o app (acrescenta http://localhost:1420); deixe vazia ou ausente no uso normal.
# ARTIGO2MD_API_ALLOW_DEV_ORIGIN=
```

`stack/up.sh` recusa subir, com mensagem em português, se `ARTIGO2MD_DATA_DIR` estiver ausente, vazio, relativo,
inexistente, não for diretório ou diferir de `$(cd "$ARTIGO2MD_DATA_DIR" && pwd -P)`.

## `stack/compose.yaml`, serviço `api` (acréscimos)

| Chave | Valor |
|-------|-------|
| `volumes` | `- "${ARTIGO2MD_DATA_DIR:?defina ARTIGO2MD_DATA_DIR em stack/.env}:/data"` (único volume do serviço) |
| `environment` | `ARTIGO2MD_DATA_DIR_HOST`, `ARTIGO2MD_DATA_DIR_MOUNT=/data`, `ARTIGO2MD_API_ALLOW_DEV_ORIGIN`, `ARTIGO2MD_POSTGRES_HOST=postgres`, `ARTIGO2MD_POSTGRES_PORT=5432`, `POSTGRES_USER`/`_PASSWORD`/`_DB`, `ARTIGO2MD_RABBITMQ_HOST=rabbitmq`, `ARTIGO2MD_RABBITMQ_PORT=5672`, `RABBITMQ_DEFAULT_USER`/`_PASS` (as obrigatórias com `${VAR:?defina ... em stack/.env}`) |

Muda (único ajuste fora do volume): `tmpfs: - /tmp:size=768m` — o zip de exportação é gravado em `/tmp` e a
soma de Markdown tem teto de 512 MiB (decisão do operador, block-001/dec-023); o zip nunca passa do tamanho da soma e 768 MiB deixa folga
para o restante de `/tmp`. O tmpfs ocupa RAM só enquanto o arquivo existe.

Mantidos sem mudança: `user: "10001:10001"`, `read_only: true`, `cap_drop: [ALL]`,
`no-new-privileges:true`, porta `127.0.0.1:${ARTIGO2MD_API_PORT}:8000`, healthcheck e `depends_on`
(`stack/compose.yaml`).

## `uv.lock` (entregável)

A imagem usa `uv sync --frozen --only-group api`; sem o `uv.lock` regenerado a imagem não instala `pika`. Critério de
aceite: `git diff uv.lock` mostra só a entrada de `pika` e a referência no grupo `api`; nenhuma outra versão muda
(decisão do operador, block-001/dec-023); `uv sync --frozen --only-group api` resolve e `import pika` funciona.

## `pyproject.toml`

`[dependency-groups] api` ganha `"pika==1.5.0"` (licença BSD-3-Clause já verificada:
`docs/specs/conversion-queue/research.md`, L15/L16). Nenhuma outra dependência nova. A regeneração do `uv.lock` MUST só adicionar `pika` ao grupo `api`: nenhuma outra versão pode mudar (decisão do operador, block-001/dec-023).
