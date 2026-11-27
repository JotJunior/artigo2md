# Contracts: `artigo2md models` (mecanismo novo de FR-036)

**[PROPOSTA — a validar na implementação]** — subcomando novo do pacote Python, iniciado pelo app como
processo próprio: `<uv> run --no-sync --project <repo> artigo2md models <verbo> --json`
(research, Decisions 4 e 9). Não muda `convert`, `doctor`, `serve` nem `worker` (FR-039). Ramo
importado sob demanda em `src/artigo2md/cli.py`, como o ramo `worker`; não importa `docling` (só
`huggingface_hub`).

Modelos necessários (constante do módulo novo, conferida por teste contra o Docling instalado):

| id | repo_id | revision | Fonte |
|----|---------|----------|-------|
| layout | `docling-project/docling-layout-heron` | `main` | `docling/datamodel/stage_model_specs.py:1019-1025` (docling 2.151.1) |
| tableformer | `docling-project/docling-models` | `v2.3.0` | `docling/models/stages/table_structure/table_structure_model.py:79,149-150` |

Destino: o cache padrão do Hugging Face, `huggingface_hub.constants.HF_HUB_CACHE` (resolvido em
`huggingface_hub/constants.py:159-184` a partir de `HF_HUB_CACHE`, `HUGGINGFACE_HUB_CACHE`, `HF_HOME`
e `XDG_CACHE_HOME`; padrão `~/.cache/huggingface/hub`), o mesmo que a conversão usa hoje
(`huggingface_hub/_snapshot_download.py:233-235`). O campo `cache_dir` do status é esse valor.

## Saída

Uma linha JSON por evento em **stdout**, chaves em snake_case (mesmo estilo do JSON da API, ex.
`last_event_at`, `docs/specs/project-api/contracts/http-api.md:170-176`). Mensagens humanas em
português no campo `message`. Nenhuma linha contém token ou credencial. stderr: livre, só diagnóstico.

**Versão do contrato** `[PROPOSTA]` (api CHK011; análogo ao `schema_version` do worker,
`docs/specs/conversion-queue/contracts/worker-cli.md`): todo evento traz `"schema_version": 1` (inteiro).
O app compara com a versão que conhece e, com evento sem o campo ou com versão desconhecida, não
interpreta o restante da linha: trata como repositório incompatível (`RepoCheck.status =
"modelsUnsupported"`, FR-016A) e não executa nenhuma operação dependente de `models`. Subcomando ausente
no repositório = saída 2 (uso inválido) em `models status --json`, com o mesmo tratamento. Exemplos
abaixo omitem o campo por brevidade, mas ele está presente em todos os eventos.

## models status --json

Sem rede (`local_files_only=True`; `HF_HUB_DISABLE_TELEMETRY=1`).

```json
{"event": "status", "cache_dir": "<caminho>", "models": [{"id": "layout", "present": true}, {"id": "tableformer", "present": false}]}
```

| Exit | Significado |
|------|-------------|
| 0 | status emitido (presentes ou não) |
| 2 | uso inválido |
| 6 | plataforma não suportada para preparação (fora do macOS) |

## models download --json

Baixa só os modelos ausentes (`present == false`). Sequência de eventos:

| event | Campos | Quando |
|-------|--------|--------|
| `plan` | `total_bytes` (int \| null), `missing` (lista de `id`) | após `dry_run=True` |
| `disk` | `free_bytes`, `required_bytes`, `ok` (bool) | antes do primeiro byte; `ok=false` encerra com exit 7 |
| `progress` | `id`, `downloaded_bytes`, `total_bytes` (int \| null) | no máximo 1 por segundo por modelo |
| `model_done` | `id` | modelo concluído |
| `result` | `status` (`"done"` \| `"failed"`), `reason` (`"no_network"` \| `"disk_full"` \| `"interrupted"` \| `"other"` \| null), `message` (pt-BR) | último evento |

| Exit | Significado |
|------|-------------|
| 0 | todos presentes ao final |
| 1 | falha de download (`result.status == "failed"`) |
| 2 | uso inválido |
| 6 | plataforma não suportada |
| 7 | espaço em disco insuficiente |
| 130 / sinal | interrompido; o próximo `status` mostra o estado real (snapshot incompleto não conta como presente, `huggingface_hub/_snapshot_download.py:329-341`) |

Os códigos 6 e 7 não colidem com os do worker (0–5, `src/artigo2md/jobqueue/settings.py:40-45`).
O app trata qualquer término sem `result` como `interrupted`.
