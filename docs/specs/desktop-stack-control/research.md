# Research: desktop-stack-control

Documento do Phase 0 do `/plan` (2026-11-26). Resolve os pontos que a spec deixou para o plano
(FR-005, FR-012, FR-016, FR-036 e os mecanismos de detecção, ciclo de vida e saúde). Toda afirmação
sobre contrato existente cita a fonte: arquivo do repositório (`caminho:linha`), página oficial
consultada (URL) ou comando de fato executado ("observado: `comando`", nesta máquina, em
2026-11-26). Contrato novo desta feature está marcado **[PROPOSTA — a validar na implementação]**.

Nenhum eixo estrutural novo foi decidido aqui: linguagem, frameworks e arquitetura do app vêm de
`desktop-shell` (Tauri 2, Rust, Svelte 5; fonte: `docs/briefing.md:74,92`), e a localização via pasta
do repositório e o ambiente `uv` vêm da decisão do operador (block-001, dec-012; `spec.md`,
Clarifications).

## Decision 1: Quem executa os comandos de infraestrutura

**Decision**: o núcleo Rust do app (`desktop/src-tauri`) inicia todos os processos externos
(`docker`, `sh stack/up.sh`, `sh stack/down.sh`, `uv run … artigo2md worker`, `uv run … artigo2md
models`) com `std::process::Command`, argumentos isolados e sem shell intermediário para valores do
usuário (FR-017). O frontend Svelte só chama comandos Tauri e consulta um retrato do estado por
sondagem (Decision 11).

**Rationale**: o desktop-shell já concentra no Rust todo acesso ao sistema (comandos em
`desktop/src-tauri/src/commands.rs:106-197`, bloqueio via `spawn_blocking`, `commands.rs:96-104`, `run_blocking`) e
proíbe permissões `shell`/`process` na webview (`desktop/tests/security-config.test.ts:80-93`).
`Command::arg` não passa por `/bin/sh`, logo espaços e caracteres especiais não alteram o comando.

**Alternatives considered**: `tauri-plugin-shell` (dependência nova e permissão `shell` na webview,
contrariando o teste de segurança acima); `fetch` da webview para os serviços (exigiria abrir a CSP
`connect-src`, hoje `ipc: http://ipc.localhost`, `desktop/src-tauri/tauri.conf.json:25`).

## Decision 2: Detecção do runtime Docker (FR-001, FR-002)

**Decision**: três sinais independentes, sem conexão de rede para fora:

1. **Instalado**: Docker Desktop = existe `/Applications/Docker.app`
   (https://docs.docker.com/desktop/setup/install/mac-install/); OrbStack = existe
   `/Applications/OrbStack.app` (https://formulae.brew.sh/cask/orbstack) ou `~/.orbstack/bin`
   (https://docs.orbstack.dev/install); colima = executável `colima` encontrado (Decision 3; binário em
   `/usr/local/bin/colima` segundo https://github.com/abiosoft/colima/blob/main/docs/INSTALL.md, ou no
   prefixo do Homebrew).
2. **Contexto Docker de cada runtime**: `desktop-linux` (Docker Desktop;
   https://docs.docker.com/desktop/settings-and-maintenance/settings/), `orbstack`
   (https://docs.orbstack.dev/docker/), `colima` para o perfil padrão (fonte:
   https://github.com/abiosoft/colima/blob/main/environment/container/docker/context.go e
   `config/config.go`). A lista vem de `docker context ls --format '{{json .}}'`, que devolve uma linha
   JSON por contexto com `Current`, `DockerEndpoint` e `Name` (observado: comando em 2026-11-26).
3. **Em execução**: `docker --context <nome> info --format '{{json .}}'` com saída 0; com o daemon
   inacessível o comando sai com 1 e a mensagem "failed to connect to the docker API" (observado:
   `DOCKER_HOST=unix:///nonexistent.sock docker info`).

"Docker em uso" = o contexto com `Current: true` (o asterisco de `docker context ls`, fonte:
https://docs.docker.com/engine/manage-resources/contexts/). Se o contexto corrente for `default`
(socket `/var/run/docker.sock`, que pode ser link de qualquer um dos runtimes; fontes: mesma página e
https://docs.docker.com/desktop/setup/install/mac-permission-requirements/) e mais de um runtime
estiver em execução, o app informa a ambiguidade (FR-002, caso de borda). Se `DOCKER_HOST` ou
`DOCKER_CONTEXT` estiver no ambiente do app, eles prevalecem sobre `docker context use`
(https://docs.docker.com/reference/cli/docker/); o app os remove do ambiente dos processos filhos e
passa `--context <em uso>` explicitamente, para que o diagnóstico e as operações usem o mesmo daemon.

"Docker Compose disponível" = `docker --context <em uso> compose version` com saída 0 (observado:
"Docker Compose version v5.6.1"; o `up.sh` usa a mesma verificação, `stack/up.sh:161`, `docker compose version`).

**Rationale**: só usa comportamento observado ou documentado. `docker desktop status`, `orb status` e
`colima status` não têm códigos de saída documentados (não encontrados nas páginas consultadas),
por isso não são a fonte do estado; o estado "instalado e parado" é inferido de "instalado" e
"contexto sem resposta".

**Alternatives considered**: testar apenas os sockets (`~/.docker/run/docker.sock`,
`~/.colima/default/docker.sock`; o caminho completo do socket do OrbStack não foi encontrado na
documentação) — insuficiente para OrbStack; usar `colima status --json` (código de saída só inferido
do código-fonte).

## Decision 3: Localização de `docker`, `uv` e `colima` num app aberto pelo Finder

**Decision**: o app resolve cada executável, nesta ordem, e guarda o caminho absoluto resolvido no
retrato do estado: (1) `/bin/zsh -l -c 'whence -p <nome>'` (observado: `whence -p uv` →
`/opt/homebrew/bin/uv`), com o nome fixo no código (nenhum valor do usuário entra nesse comando),
stdin nulo, tempo máximo de 5 s e só a última linha da saída, validada como na seção Segurança do
`plan.md` (M6); (2) lista fixa de candidatos: `docker` em `~/.docker/bin/docker`
(Docker Desktop 4.89+, https://docs.docker.com/desktop/settings-and-maintenance/settings/),
`/usr/local/bin/docker` (mesma página e https://docs.orbstack.dev/install), `~/.orbstack/bin/docker`
(https://docs.orbstack.dev/install) e `/opt/homebrew/bin/docker`; `uv` em `/opt/homebrew/bin/uv`
(observado: `which -a uv`); `colima` em `/usr/local/bin/colima` e `/opt/homebrew/bin/colima`. O
`PATH` dos processos filhos é `/usr/bin:/bin:/usr/sbin:/sbin` **primeiro**, seguido dos diretórios do
`docker` e do `uv` resolvidos, para que utilitários do sistema usados pelo `up.sh` (`nc` em
`/usr/bin/nc`, observado: `which nc`; `stack/up.sh:217,232`, `nc -z` e `command -v nc`) não sejam substituídos por versões de
diretórios graváveis pelo usuário (gate de segurança, `plan.md` M6).

**Rationale**: o PATH de um app aberto pelo Finder não inclui o PATH do shell do usuário; o `up.sh`
precisa de `docker` e `nc` no PATH (`stack/up.sh:158,232`, `command -v docker` e `command -v nc`). O shell de login resolve o caso comum, e a
lista fixa cobre quem não usa zsh.

**Alternatives considered**: exigir caminho manual nas configurações (ruim para a pessoa não técnica);
herdar o PATH do app (não encontra o `docker` do Docker Desktop em `~/.docker/bin`).

## Decision 4: Pasta do repositório e verificação antes de cada operação (FR-016)

**Decision**: configuração `repoPath` (caminho absoluto). Antes de qualquer operação o app verifica,
nesta ordem, e para no primeiro problema com mensagem própria: a pasta existe; contém
`stack/compose.yaml`, `stack/up.sh` e `stack/down.sh` (fonte: os próprios arquivos); contém
`pyproject.toml` e `uv.lock` (necessários ao build da imagem da API, `stack/api/Dockerfile:18-22`, e
ao `uv run`); o `uv` foi resolvido (Decision 3); `uv run --no-sync --project <repo> artigo2md
--version` sai com 0 (o parser tem `--version`, `src/artigo2md/cli.py:227`, `add_argument("--version"`). A falta do extra
`queue` só aparece ao iniciar o worker, como saída 3 (`src/artigo2md/jobqueue/settings.py:40-45`;
`docs/specs/conversion-queue/contracts/worker-cli.md:40-49`), e vira mensagem própria.

**Rationale**: `--no-sync` evita que o `uv run` sincronize o ambiente ("When using `run`, uv will
ensure that the project environment is up-to-date", https://docs.astral.sh/uv/concepts/projects/run/)
e, com isso, baixe pacotes sem pedido (FR-016: o app não instala dependências; FR-035). `--no-sync`
"Implies `--frozen`" (https://docs.astral.sh/uv/reference/cli/#uv-run; observado em `uv run --help`,
uv 0.13.9).

**Alternatives considered**: chamar `<repo>/.venv/bin/artigo2md` direto (contraria FR-016, que manda
usar o ambiente `uv`); `uv run` sem `--no-sync` (sincroniza e pode baixar).

## Decision 5: Arquivo de parâmetros da stack (FR-010, FR-011, FR-013)

**Decision**: o arquivo de parâmetros é `<repo>/stack/.env`, o único que o `up.sh` lê (caminho fixo,
`stack/up.sh:19`, `ENV_FILE=`, sem variável que o troque). Na primeira inicialização, se ele não existir, o app o
cria com permissão `0600`, escrita atômica (temporário + `rename`, mesmo padrão de
`desktop/src-tauri/src/settings.rs:129-141`), com as chaves de `stack/.env.example:22-39`: portas
padrão (5432, 5672, 15672, 8778), `POSTGRES_USER`/`POSTGRES_DB`/`RABBITMQ_DEFAULT_USER` = `artigo2md`
(valores de exemplo do próprio `.env.example`), `ARTIGO2MD_DATA_DIR` = pasta de dados (Decision 6) e
duas senhas geradas. Senha: 32 caracteres hexadecimais a partir de 16 bytes de `/dev/urandom` —
cumpre "12 ou mais caracteres", "sem `$`" e "diferente de `troque-esta-senha`"
(`stack/up.sh:22,89-91,124-125`: `EXAMPLE_PASSWORD`, teste de `$`, exemplo e tamanho).

Arquivo existente: o app nunca o recria nem toca em credenciais. Trocar portas ou a pasta de dados
nas configurações reescreve **somente** as linhas das quatro chaves `ARTIGO2MD_POSTGRES_PORT`,
`ARTIGO2MD_RABBITMQ_PORT`, `ARTIGO2MD_API_PORT` e `ARTIGO2MD_DATA_DIR` (a
`ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT` é copiada na criação e nunca editada: o app não publica o painel,
`plan.md` dec-025), preservando byte a byte as demais linhas, inclusive comentários e
credenciais. O `.env` é a fonte de verdade das portas e da pasta de dados. Regra única para a pasta de
dados: até o `.env` existir, `StackSettings.dataDir` é a proposta usada para criá-lo; depois, é só um
espelho de leitura de `ARTIGO2MD_DATA_DIR`, atualizado a partir do `.env` a cada verificação, e
`pick_data_dir` grava primeiro no `.env` e depois no espelho. Portas não têm cópia. Validação de porta antes de gravar: inteiro de 1 a 65535 (mesma regra de `stack/up.sh:130-138`, `PORT_VARS`).
O endereço de publicação não é configurável (`stack/.env.example:17`).

Leitura do `.env` pelo app segue as regras do parser do `up.sh` (`stack/up.sh:52-92`: sem `eval`,
remove `export `, aspas externas, ignora chaves fora de `[A-Za-z0-9_]`). As credenciais lidas ficam
só em memória, em tipo que não implementa `Debug`/`Serialize` com o valor (Decision 13).

**Rationale**: reaproveita o script como executor (FR-007 exige o mesmo comportamento) e mantém uma
fonte só para stack e worker; o worker também lê `./stack/.env` relativo ao diretório de trabalho
(`src/artigo2md/jobqueue/settings.py:89-116`).

**Alternatives considered**: arquivo de parâmetros na pasta de configuração do app com
`docker compose` chamado direto (duplicaria a validação do `up.sh` e divergiria do script); guardar as
portas também no `settings` do app (duas fontes de verdade).

## Decision 6: Pasta de dados padrão (FR-012)

**Decision**: padrão proposto `~/artigo2md-dados` (resolvido para caminho absoluto real). O app cria
a pasta se não existir, mostra o caminho e valida antes de iniciar stack ou workers: existe, é
diretório, é gravável (criação e remoção de um arquivo temporário) e é igual ao seu caminho canônico
(`std::fs::canonicalize`), a mesma exigência do `up.sh`, que compara com `pwd -P`
(`stack/up.sh:140-154`, `ARTIGO2MD_DATA_DIR`; `stack/.env.example:19-21`). O mesmo valor vai ao `.env` e ao
`--data-dir` do worker.

**Rationale**: fica na pasta pessoal, fora de `~/Documents` e `~/Desktop` (pastas que o macOS protege
com pedido de permissão), e não colide com um clone do repositório chamado `artigo2md`. O README usa
uma pasta na raiz da pasta pessoal como exemplo (`README.md:235-236`, `~/artigo2md-fila`).

**Alternatives considered**: `~/Library/Application Support/<identificador>` (escondida para a pessoa
não técnica, que vai abrir os Markdown gerados ali); `~/artigo2md` (colide com o nome do repositório).

## Decision 7: Subir e parar a stack (FR-007 a FR-009, FR-014)

**Decision**: "Iniciar infraestrutura" executa `/bin/sh <repo>/stack/up.sh` sem opções e "Parar
infraestrutura" executa `/bin/sh <repo>/stack/down.sh` sem opções, com `current_dir` = `<repo>`,
saída padrão e de erro lidas linha a linha num registro técnico limitado. O resultado vem só do código
de saída: `up.sh` 0/2/3/4/5 (`stack/up.sh:13-14`), `down.sh` 0/1/2/3 (`stack/down.sh:14,76,82`); 1 do
`up.sh` só ocorre se o `cd` falhar (`stack/up.sh:18`). "Já estava no ar" é detectado antes, pelo
estado dos serviços (Decision 10), e também é o que o `up.sh` faz (`stack/up.sh:200-213`, bloco `ALL_RUNNING`). O nome do
serviço com problema no código 5 e da porta no código 4 vem de consulta própria do app depois da falha
(`docker compose … ps --all --format '{{.Service}} {{.State}} {{.Health}}'`, o mesmo formato de
`stack/up.sh:267`, `dc ps --all --format`; e a porta é a do `.env` cuja checagem de loopback falhou), nunca da análise do
texto em português do script. Para o código 2, o app valida o `.env` **antes** de chamar o script, com
as mesmas regras de `stack/up.sh:49-154` (chave duplicada, `$`, `POSTGRES_HOST_AUTH_METHOD`, campos
vazios, senha de exemplo ou curta, portas, pasta de dados), e nomeia a variável pela própria checagem;
se o script ainda assim sair com 2, a mensagem é genérica ("parâmetros recusados pelo script; veja os
detalhes técnicos"). Operação na pasta da stack que sai com 1 do `up.sh` vira `scriptFailed`. O app não oferece `--apagar-dados` nem `--atualizar-imagens`
(FR-008).

Toda chamada direta ao Compose replica a do script: `docker --context <em uso> compose
--project-directory <repo>/stack --env-file <repo>/stack/.env -f <repo>/stack/compose.yaml …`
(`stack/up.sh:31-37`, função `dc`; projeto `artigo2md`, `stack/compose.yaml:7`).

**Rationale**: comportamento idêntico ao script por construção; o texto do script é para humanos.

**Alternatives considered**: reimplementar o `up.sh` em Rust (duplicação, risco de divergência).

## Decision 8: Etapa "imagens da infraestrutura" (FR-030 a FR-034)

**Ordem do primeiro uso**: o Compose exige as variáveis do `.env` (`${VAR:?}` em
`stack/compose.yaml:12-81`), então o app garante o `.env` (cria se ausente, Decision 5) **antes** de
qualquer chamada ao Compose — no primeiro `refresh_infra` com repositório e pasta de dados válidos, em
`prepare_images` e em `start_stack`. A criação do `.env` não baixa nada.

**Decision**: imagens da stack = saída de `docker compose … config --images` (observado:
`artigo2md-api:local`, `postgres:18-trixie`, `rabbitmq:4.4-management`). Estado: "concluída" se
`docker image inspect` encontra as três; senão "pendente". Sem confirmação (FR-031), o app não chama o
`up.sh` com imagem ausente, porque ele baixaria (`stack/up.sh:248-257`, `docker image inspect` em 251, `--pull missing`). Com
confirmação: `docker compose … --progress json pull --ignore-buildable` e depois
`docker compose … --progress json build api` (o `--progress json` existe no Compose v5.6.1,
observado em `docker compose --help`; `--ignore-buildable` em `docker compose pull --help`). Cada
linha JSON tem ao menos `id`, `status` e `text` (observado: `--dry-run pull`, ex.
`{"id":"Image postgres:18-trixie","status":"Working","text":"Pulling"}` e `"status":"Done"`). Depois
disso o `up.sh` encontra a imagem da API e usa `--pull missing --no-build` (`stack/up.sh:251-253`).

Progresso: estado por imagem (aguardando, baixando, concluída) e volume baixado com indicação de
atividade. **[A validar na implementação — resolvido em 2026-11-27, ver a nota abaixo]**: os campos de bytes das linhas JSON de um `pull` real não
foram observados (o `--dry-run` não baixa); a tarefa de implementação captura a saída real de um pull
e, se houver campos de bytes, mostra percentual; se não houver, mostra a contagem de imagens
concluídas com indicação de atividade, o que cumpre o ramo "tamanho total desconhecido" de FR-032.

**Nota de 2026-11-27 (resultado de 11.1; o texto acima não foi reescrito)**: um `pull` real foi observado (Docker
Compose v5.6.1, Docker Desktop 29.9.2, imagem pública `alpine:3.20`, projeto descartável; as imagens da stack não
foram baixadas), e as linhas saem em stderr. Há bytes **só por camada** (linhas `Downloading` com `current`, `total`
e `percent`), `totalBytes` geral **nunca** é conhecido de antemão, e por isso o app soma os `current` observados, não
calcula percentual geral e usa o ramo "tamanho total desconhecido" de FR-032. Detalhes: `plan.md`, "Resultado de
11.1", `contracts/external-processes.md` e a fixture `desktop/src-tauri/src/stack/fixtures/compose-pull-progress.jsonl`
(saída capturada); implementação em `desktop/src-tauri/src/stack/images.rs`. Não observado: a cadência das linhas em
imagens de centenas de MB.

Tamanho aproximado mostrado antes da confirmação: cerca de 1,5 GB, soma das imagens locais medidas
nesta máquina (observado: `docker image ls`, 2026-11-26, arm64: `postgres:18-trixie` 701MB,
`rabbitmq:4.4-management` 456MB, `artigo2md-api:local` 339MB). A construção da imagem da API baixa a
imagem base e as dependências Python (`stack/api/Dockerfile:4,6,19`). Espaço em disco (FR-033): o
armazenamento das imagens fica no disco virtual do runtime, cujo espaço livre o app não consegue ler
por loopback sem credenciais; o app verifica o espaço livre do volume da pasta pessoal (`statvfs`)
contra esse tamanho e avisa quanto falta, e a falha de disco cheio durante o pull é traduzida
(caso de borda da spec).

**Alternatives considered**: deixar o `up.sh` baixar (sem confirmação nem progresso, viola FR-031);
`docker manifest inspect` para tamanho exato (consulta a internet antes da confirmação).

## Decision 9: Mecanismo novo de preparação dos modelos (FR-036)

**Decision**: novo subcomando `artigo2md models` no pacote Python, com dois verbos
**[PROPOSTA — a validar na implementação]** (contrato em `contracts/models-cli.md`):

- `artigo2md models status --json`: verifica, sem rede, se cada modelo necessário já está no cache.
- `artigo2md models download --json`: baixa os modelos que faltam, sem converter documento,
  emitindo uma linha JSON por evento de progresso e um resultado final.

Fatos da fonte (cópia instalada de docling 2.151.1, mesma versão do `uv.lock`):

- Com `artifacts_path` vazio — o caso do projeto, que não o define (busca em `src/`: não encontrado) —
  a pipeline baixa por `huggingface_hub.snapshot_download` no cache padrão do Hugging Face
  (`docling/models/utils/hf_model_download.py:116-121`; `huggingface_hub/_snapshot_download.py:233-235`,
  `cache_dir = constants.HF_HUB_CACHE`). `HF_HUB_CACHE` é resolvido em `huggingface_hub/constants.py:159-184`
  a partir de `HF_HUB_CACHE`, do legado `HUGGINGFACE_HUB_CACHE` e de `HF_HOME` (que por padrão é
  `$XDG_CACHE_HOME/huggingface` ou `~/.cache/huggingface`); o subcomando não reimplementa essa ordem:
  lê `huggingface_hub.constants.HF_HUB_CACHE` e informa o caminho resultante.
- `docling.utils.model_downloader.download_models` e `docling-tools models download` gravam em outro
  lugar, `~/.cache/docling/models` (`docling/utils/model_downloader.py:235-236`), que a pipeline só usa
  com `artifacts_path` definido. Por isso o subcomando **não** usa `download_models`: baixa para o
  mesmo cache que a conversão já lê, sem mudar `convert`, `doctor` nem `serve` (FR-039).
- Modelos necessários no macOS com as opções de `src/artigo2md/engines.py:55-79,123-131`
  (tabela ACCURATE, OCR `ocrmac`, enriquecimentos desligados por padrão):
  `docling-project/docling-layout-heron` na revisão `main` (`docling/datamodel/stage_model_specs.py:1019-1025`)
  e `docling-project/docling-models` na revisão `v2.3.0`
  (`docling/models/stages/table_structure/table_structure_model.py:79,149-150`). O `ocrmac` não baixa
  modelo (`docling/models/stages/ocr/ocr_mac_model.py:82-96`). Fora do macOS a lista não foi
  investigada; o subcomando informa "plataforma não suportada para preparação" (o app é só macOS,
  `docs/briefing.md:86`).
- A lista (repositório + revisão) fica numa constante do módulo novo, com um teste que a confere contra
  as specs do próprio Docling instalado (`stage_model_specs`, `TableStructureModel`), para detectar
  deriva quando o Docling for atualizado.
- Progresso em bytes: `snapshot_download(..., tqdm_class=<classe>)` cria barras agregadas de bytes
  (`desc="Downloading bytes"`, `unit="B"`) a partir dessa classe (`huggingface_hub/_snapshot_download.py:202-207,437-501`).
  O subcomando passa uma subclasse de `huggingface_hub.utils.tqdm` que, em vez de desenhar barra,
  emite no máximo uma linha JSON por segundo com bytes baixados e total.
- Total antes de baixar: `snapshot_download(..., dry_run=True)` devolve `DryRunFileInfo` sem baixar,
  com rede (`_snapshot_download.py:208-215`; `DryRunError` quando o repositório não é alcançável,
  `:311-314`); serve à checagem de espaço (FR-033) feita
  depois da confirmação e antes do primeiro byte.
- Presença sem rede: `snapshot_download(repo_id, revision=..., local_files_only=True)` levanta
  `LocalEntryNotFoundError` se faltar e rejeita snapshot incompleto
  (`_snapshot_download.py:329-341,362-367`).
- O subcomando define `HF_HUB_DISABLE_TELEMETRY=1`, como `engines.py:24-27`, e não importa `docling`
  no caminho de download nem de status (só `huggingface_hub`); a conferência com as specs do Docling
  fica no teste.

Tamanho mostrado antes da confirmação: "cerca de 3 GB livres", fonte `README.md:34`. O total exato
vem do `dry_run` depois da confirmação.

**Rationale**: atende (a), (b) e (c) de FR-036 e baixa no lugar em que a conversão procura. Roda como
processo próprio, iniciado pelo app via `uv run --no-sync` (o app não faz conexão para fora, FR-037).
Licenças: `huggingface_hub` Apache-2.0 (METADATA:8,13), já dependência transitiva; `tqdm` é
`MPL-2.0 AND MIT` (`tqdm-4.70.1.dist-info/METADATA:6`), transitiva de `docling-slim` e de
`huggingface_hub`, entra sem modificação e não vira dependência direta no `pyproject.toml`
(Princípio II, emenda 1.2.0).

**Alternatives considered**: `docling-tools models download` (cache diferente e saída em barra Rich,
não legível pelo app); converter um PDF de amostra para forçar o download (FR-036 proíbe); download
pelo próprio app em Rust (FR-036 e FR-037 proíbem).

## Decision 10: Saúde dos serviços (FR-026 a FR-029)

**Decision**: o núcleo Rust reimplementa as três sondagens do `artigo2md doctor`, sem credenciais e só
em `127.0.0.1`: Postgres = envia SSLRequest `!II (8, 80877103)` e aceita primeiro byte `S`, `N` ou `E`
(`src/artigo2md/infra.py:38,165-177`); RabbitMQ = envia `AMQP\x00\x00\x09\x01` e aceita quadro tipo 1
no canal 0 (`infra.py:40,180-193`); API = `GET /health` com 200 e JSON `status == "ok"`
(`infra.py:196-210`; `src/artigo2md/api/app.py:75-78`). Tempo de conexão e de leitura de 1 s cada,
como `infra.py:22-26`. Aviso "consumidor de eventos parado" = `GET /v1/status` com
`consumer.connected == false` (`src/artigo2md/api/routes/status.py:17-26`;
`docs/specs/project-api/contracts/http-api.md:170-176`). Portas vêm do `.env` (Decision 5).

Estado do serviço: "parado" quando o Compose não lista o serviço em execução
(`docker compose … ps --status running --services`, mesmo uso de `stack/up.sh:165`) e não há operação
de subir em andamento; "iniciando" durante a operação de subir enquanto a sondagem falha; "saudável"
quando a sondagem passa; "com problema" quando o serviço está em execução para o Compose (ou fora de
operação) e a sondagem falha. Com o Docker indisponível, Postgres, RabbitMQ e API aparecem "parado" e o
runtime mostra o problema.

Sondagem por um temporizador do núcleo Rust a cada 2 s, independente da interface (o indicador da
barra lateral é sempre visível, FR-025), mais "Verificar novamente". Uma rodada: as três sondagens em
paralelo (até 2 s cada) e `compose … ps --status running --services` com tempo máximo de 3 s. A
interface lê o retrato a cada 3 s (1 s com operação em andamento). Pior caso entre a mudança e a tela:
2 s (espera da próxima rodada) + 3 s (rodada) + 3 s (leitura da interface) = 8 s < 10 s (SC-003).
O runtime é verificado a cada 10 s fora do guia e a cada 5 s com o guia aberto; imagens
(`docker image inspect`) e modelos (`artigo2md models status --json`) só na abertura do app, em
"Verificar novamente", antes de `start_stack`/`start_workers` e ao fim de cada preparação, porque cada
`models status` inicia um processo Python.

Mapeamento para os quatro estados de FR-026: runtime — `running` em uso = "saudável", `stopped` ou
nenhum = "parado", `composeMissing`/`ambiguous` = "com problema"; pool — `running` = "saudável",
`starting`/`stopping` = "iniciando", `stopped` = "parado", `crashed` = "com problema". Indicador
resumido (FR-025): "saudável" quando runtime, os três serviços e o pool estão saudáveis; "parada" quando
os três serviços estão parados; "parcial" em qualquer outro caso.

**Rationale**: mesmo critério do `doctor` (FR-026) sem importar Python; as conexões saem do processo
Rust, então a CSP da webview não muda.

**Alternatives considered**: chamar `uv run artigo2md doctor` a cada sondagem (lento, carrega Python a
cada 3 s); usar o estado de saúde do Compose (exige o Docker para cada leitura e não é o critério de
FR-026).

## Decision 11: Comunicação núcleo ↔ interface

**Decision**: comandos Tauri e sondagem: o frontend chama `get_infra_snapshot` a cada 1 s enquanto há
operação ou download em andamento e a cada 3 s fora disso. Nenhum evento Tauri novo.

**Rationale**: a capability `default.json` tem `"permissions": []`
(`desktop/src-tauri/capabilities/default.json`) e a sondagem por comando mantém isso sem mudança. Se
eventos Tauri exigem ou não uma permissão `core:event:*` na capability não foi verificado em fonte
oficial nesta etapa (o teste `desktop/tests/security-config.test.ts:86-93` só barra os prefixos
`fs|dialog|shell|http|opener|process` e `:default`); a escolha por sondagem dispensa essa verificação.
1 s cumpre FR-032 (≤ 5 s).

**Alternatives considered**: eventos Tauri (`emit`/`listen`), que pediriam mudança de capability.

## Decision 12: Pool de workers: início, parada calma e órfãos (FR-018 a FR-024)

**Decision**:

- Início: `<uv> run --no-sync --project <repo> artigo2md worker --data-dir <pasta> --workers <N>
  --min-free-memory <MB> --shutdown-timeout 30` (flags em `src/artigo2md/cli.py:251-274`, `sub.add_parser("worker"`, e
  `worker-cli.md:11-20`; o prazo é passado explicitamente porque a espera do app depende dele), com
  `current_dir` = `<repo>`, novo grupo de processos (`CommandExt::process_group(0)`, Rust estável) e as
  credenciais `RABBITMQ_DEFAULT_USER`/`RABBITMQ_DEFAULT_PASS` e a porta `ARTIGO2MD_RABBITMQ_PORT` no
  **ambiente** do processo (FR-011; o worker as lê do ambiente primeiro, `jobqueue/settings.py:89-132`,
  e as remove antes de criar filhos, `jobqueue/settings.py:119-123,198-202`, `scrub_environment`). O app remove
  `ARTIGO2MD_STACK_ENV` do ambiente herdado. Pré-condições: RabbitMQ saudável (ver `plan.md` M2) e
  etapa "modelos de conversão" concluída (código `modelsMissing` caso contrário). O ambiente do worker
  inclui `HF_HUB_OFFLINE=1`: com essa variável definida, `engines.py` não tenta baixar e levanta
  `EngineUnavailable` (`src/artigo2md/engines.py:134-194`), de modo que nenhuma conversão iniciada
  pelo app dispara download escondido de modelo (FR-036); se os modelos sumirem do cache, o job falha
  com mensagem e o app volta a mostrar "Preparar modelos".
- Parada calma: `SIGTERM` só ao processo `uv`; o uv repassa `SIGTERM` ao filho ("On Unix systems, uv
  will forward most signals", https://docs.astral.sh/uv/concepts/projects/run/) e sai com o código do
  filho (observado: `uv run --no-sync python …` com handler de SIGTERM saiu com 0 após `kill -TERM`; com
  `sys.exit(4)` saiu com 4). O worker espera `--shutdown-timeout` (padrão 30 s), devolve o job à fila
  sem ack e dá mais 10 s (`src/artigo2md/jobqueue/service.py:24,111-125`). O app espera 30 + 10 + 5 =
  45 s e então envia `SIGKILL` ao grupo inteiro (`libc::killpg`), o que alcança os filhos `spawn` do
  worker (`src/artigo2md/worker.py:73,85-91`; não há `setsid` no worker). Fim confirmado quando
  `killpg(pgid, 0)` falha com `ESRCH` (nenhum processo no grupo). SC-005 (≤ 60 s) cumprido.
- Registro do pool: `worker-pool.json` na pasta de dados do app do Tauri com `pid`, `pgid`,
  `startedAt` (início do processo lido por `proc_pidinfo(PROC_PIDTBSDINFO)`, disponível em `libc`
  0.2.191, `src/unix/bsd/apple/mod.rs:669,3983,5480`) e `repoPath`. Ao abrir, se o `pid` existe e o
  horário de início confere, é o pool da sessão anterior: o app oferece "Parar" (mesma parada calma) ou
  "Continuar usando" (adota o registro; as mensagens anteriores não estão disponíveis). Processo com
  horário diferente = PID reutilizado: registro descartado. Processos `artigo2md worker` sem registro
  não são tocados (FR-021).
- Término inesperado: thread que aguarda o filho registra o código; tradução por código
  (`worker-cli.md:40-49`); término por sinal = motivo `killed`; sem reinício automático; ação
  "Reiniciar" (`start_workers` aceito no estado `crashed`). Pool adotado não é filho do app: o núcleo
  confere a cada 2 s se o grupo ainda tem membros válidos (`plan.md` M8); se não tiver, estado
  `crashed`, `exitCode` nulo e motivo `unknown`.
- Mensagens: as últimas 200 linhas da saída de erro do worker (o log vai para stderr em português,
  `src/artigo2md/jobqueue/slot.py:76-77`), com mascaramento das credenciais conhecidas antes de
  guardar. O aviso de pool acima da RAM vem do próprio worker (`src/artigo2md/jobqueue/memory.py:74-81`,
  emitido em `service.py:74-76`) e é destacado quando a linha começa com "Aviso:".
- Fechar o app: `CloseRequested`/`ExitRequested` do Tauri são interceptados enquanto houver pool ou
  operação em andamento; a interface mostra "Encerrando workers…", roda a parada calma e só então
  encerra (teto de 60 s, `plan.md` M21). Operação em andamento (`up.sh`, `down.sh`, pull/build,
  `models download`) recebe `SIGTERM` no grupo de processos dela (todo processo de operação também é
  iniciado com `process_group(0)`), espera de até 10 s e `SIGKILL` no grupo; nada fica rodando depois do
  app, e na próxima abertura o estado real é recalculado (FR-030, FR-034). A stack não é derrubada
  (FR-024).

**Rationale**: usa só comportamento documentado ou observado do worker e do uv; `libc` já está no
`Cargo.lock` (0.2.191) e passa a dependência direta, licença `MIT OR Apache-2.0` (observado:
`libc-0.2.191/Cargo.toml` no registro local do cargo).

**Alternatives considered**: `SIGTERM` ao grupo inteiro (mataria os filhos de conversão na hora — eles
só ignoram SIGINT, `worker.py:45-48` — e quebraria a parada calma); crate `sysinfo` ou `nix`
(dependências novas sem necessidade).

## Decision 13: Credenciais fora de tela, registros e linha de comando (FR-011, SC-007)

**Decision**: credenciais só existem no `.env` (0600) e na memória do núcleo Rust, num tipo `Secret`
sem `Debug`, `Display` nem `Serialize` do valor. Nenhum comando Tauri devolve credencial. Toda linha de
saída técnica guardada (scripts, Compose, worker, models) passa por mascaramento dos valores conhecidos
antes de ir para o registro do app ou para a tela. Credenciais nunca entram em `arg()`; vão ao worker
pelo ambiente (Decision 12). Mensagens sobre credencial citam só o nome da variável (padrão do
`up.sh:10-11`).

**Rationale**: FR-011 manda o ambiente para o worker; a lista de processos (`ps`) mostra argumentos, e
o app não os usa para segredos. O `.env.example` registra que credenciais aparecem no
`docker inspect` (`stack/.env.example:15-16`), risco aceito pela `container-stack` e fora desta feature.

## Decision 14: Uma operação por vez e segunda instância (FR-015)

**Decision**: um `Mutex` de ciclo de vida no núcleo recusa nova operação (iniciar/parar stack,
iniciar/parar workers — as quatro de FR-015 — e preparar imagens, que também usa o Compose) com o
código `operationInProgress`. "Preparar modelos" tem `Mutex` próprio: não usa a stack, então não
bloqueia iniciar/parar a stack durante um download de GB; `start_workers` continua recusado enquanto
os modelos não estiverem concluídos (Decision 12). Delta registrado em relação a FR-015: preparar
imagens entra na exclusão mútua; preparar modelos é exclusivo só consigo mesmo.
Entre instâncias: o app obtém `flock(LOCK_EX | LOCK_NB)` em `lifecycle.lock` na pasta de dados do app
do Tauri ao abrir; quem não obtém fica em modo somente leitura (mostra o retrato, recusa ações com
`anotherInstance`). `flock` vem de `libc` (`src/unix/mod.rs:2273`). O lock é do sistema operacional e
some com o processo.

**Alternatives considered**: `tauri-plugin-single-instance` (dependência nova; a spec pede que a segunda
instância mostre o estado, não que seja impedida).

## Decision 15: Guia de instalação (FR-004 a FR-006)

**Decision**: conteúdo estático em português no frontend, um módulo por runtime, cada passo com a URL
da fonte oficial registrada no código e no `data-model.md`. Endereços aparecem como texto copiável;
nenhuma ação abre navegador nem executa instalador (FR-005). Progresso do guia (runtime escolhido e
passo) persistido em `stack-settings.json`. Com o guia aberto, o diagnóstico roda a cada 5 s e o guia se
encerra ao detectar "Docker pronto".

Conteúdo-base com fonte:

| Runtime | Instalação | Primeira abertura / iniciar | Comparação (licença) |
|---|---|---|---|
| Docker Desktop | baixar `Docker.dmg` (Apple silicon: `https://desktop.docker.com/mac/main/arm64/Docker.dmg`; Intel: `https://desktop.docker.com/mac/main/amd64/Docker.dmg`), abrir, arrastar para Aplicativos (https://docs.docker.com/desktop/setup/install/mac-install/); ou `brew install --cask docker-desktop` (https://formulae.brew.sh/cask/docker-desktop) | abrir o Docker.app e aceitar o contrato de assinatura, sem o qual ele não roda (mesma página) | grátis para uso pessoal, educação, código aberto não comercial e empresas com menos de 250 pessoas e menos de US$ 10 milhões de receita; acima disso, assinatura paga (mesma página) |
| OrbStack | baixar em `https://orbstack.dev/download` (versões separadas para Apple Silicon e Intel; macOS 14 ou mais novo) ou `brew install orbstack` (mesma página; https://docs.orbstack.dev/faq) | abrir o OrbStack; "ready to use out of the box" (https://docs.orbstack.dev/quick-start) | grátis para uso pessoal; uso comercial exige licença por pessoa (https://docs.orbstack.dev/licensing) |
| colima | `brew install colima` e `brew install docker` (https://github.com/abiosoft/colima; macOS 13 ou mais novo, https://github.com/abiosoft/colima/blob/main/docs/FAQ.md); Compose: `brew install docker-compose` e seguir a instrução que o Homebrew mostra ao final para registrar o plugin (https://formulae.brew.sh/formula/docker-compose) | `colima start` (https://github.com/abiosoft/colima) | MIT, gratuito (mesma página) |

A instrução exata do Homebrew para o plugin do Compose (`cliPluginsExtraDirs`) não foi capturada; o
guia manda seguir a mensagem do próprio Homebrew em vez de presumir o texto.

**Nota de 2026-11-27 (resultado do converge, tarefa 13.13; a tabela acima não foi reescrita)**: o guia
(`desktop/src/lib/guide.ts`) oferece para Docker Desktop e OrbStack só o caminho do instalador (download do
`Docker.dmg` e de `orbstack.dev/download`); as alternativas `brew install --cask docker-desktop` e
`brew install orbstack` da tabela não foram incluídas, e Homebrew aparece só no caminho do colima, cuja instalação na
tabela é só por Homebrew. Essa omissão não estava registrada em nenhum artefato e o motivo dela não foi anotado na
época da implementação; sabe-se que FR-004 não exige a alternativa. Incluí-la, e quanto detalhe técnico o guia mostra,
é decisão do dono do produto, ligada a ux CHK018, que **segue aberto** (`{humano}`). Nenhum passo foi acrescentado ao
guia por esta nota.

Versão do macOS: o app aceita macOS 11+ (`tauri.conf.json:29-40`), mas OrbStack exige 14+ e colima
13+ (fontes acima). O guia lê a versão do sistema e marca como indisponível, com o motivo, o runtime
cuja versão mínima não é atendida; o Docker Desktop suporta a versão atual e as duas anteriores
(https://docs.docker.com/desktop/setup/install/mac-install/), aviso mostrado na comparação.

**Alternatives considered**: buscar o conteúdo on-line (viola FR-005, que exige funcionar sem
internet).

## Decision 16: Configurações do app desta feature (FR-019)

**Decision**: arquivo próprio `stack-settings.json` em `app_config_dir`, ao lado do `settings.json` do
desktop-shell, com o mesmo padrão: `serde(rename_all = "camelCase", deny_unknown_fields)`,
`schemaVersion`, escrita atômica e `Mutex` (`desktop/src-tauri/src/settings.rs:25-141`;
`desktop/src-tauri/src/model.rs:38-46`). Campos: `repoPath`, `workers` (≥ 1, padrão 4),
`minFreeMemoryMb` (≥ 0, padrão 4822; `src/artigo2md/jobqueue/settings.py:28-34`), `dataDir`
(proposta inicial da Decision 6, até existir `.env`), `guide`. Portas não ficam aqui (Decision 5).

**Rationale**: não muda o esquema do `settings.json` do desktop-shell (`deny_unknown_fields` e
`schema_version` 1, `model.rs:6,38-46`), e a tela "Configurações" mostra os dois arquivos.

**Alternatives considered**: acrescentar campos ao `AppSettings` (exige bump de esquema e mexe no
contrato de outra feature).

## Decision 17: Dependências novas e licenças (FR-040)

| Dependência | Onde | Tipo | Licença (fonte) |
|---|---|---|---|
| `libc` 0.2.191 | `desktop/src-tauri/Cargo.toml` | direta (já transitiva no `Cargo.lock`) | `MIT OR Apache-2.0` (observado: `libc-0.2.191/Cargo.toml`, campo `license`) |
| `huggingface_hub` 1.33.0 | Python | passa a direta no `pyproject.toml` (`huggingface-hub>=1.33,<2`; o teto `<2` é o mesmo que `docling-ibm-models` exige, `docling_ibm_models-4.0.4.dist-info/METADATA:31`), já que `modelcache.py` e `engines.py` a importam | Apache-2.0 (`huggingface_hub-1.33.0.dist-info/METADATA:8,13`) |
| `tqdm` 4.70.1 | Python | transitiva existente (subclasse via `huggingface_hub.utils.tqdm`) | `MPL-2.0 AND MIT` (`tqdm-4.70.1.dist-info/METADATA:6`); copyleft fraco no nível de arquivo, transitiva, sem modificação — registrada aqui conforme o Princípio II |

Conferência na implementação (tarefa 4.4.2): `importlib.metadata` do ambiente instalado devolveu `huggingface_hub 1.33.0` com `License: Apache-2.0` e `tqdm 4.70.1` com `License: MPL-2.0 AND MIT`; `~/.cargo/registry/src/*/libc-0.2.191/Cargo.toml` traz `license = "MIT OR Apache-2.0"`. `huggingface-hub>=1.33,<2` entrou em `pyproject.toml` e `uv.lock` ganhou só as duas linhas da dependência direta (nenhuma versão trocada).

Nenhuma dependência npm nova.
