# Implementation Plan: Controle da infraestrutura pelo app desktop

**Feature**: `desktop-stack-control` | **Date**: 2026-11-26 | **Spec**: [spec.md](./spec.md)

## Summary

O app Tauri ganha a entrada "Infraestrutura": diagnostica Docker Desktop, OrbStack e colima, guia a
instalação offline, sobe e derruba a stack da `container-stack` executando `stack/up.sh` e
`stack/down.sh` da pasta do repositório indicada pela pessoa, inicia e para o pool nativo
`artigo2md worker` pelo ambiente `uv` desse repositório e mostra a saúde dos serviços por sondagens
de loopback iguais às do `artigo2md doctor`. A primeira execução tem duas etapas confirmadas:
imagens (pull/build do Compose com `--progress json`) e modelos (subcomando novo
`artigo2md models`, que baixa os dois modelos do Docling no cache do Hugging Face com progresso em
bytes). Todo processo externo e toda conexão saem do núcleo Rust; a webview só chama comandos Tauri e
lê um retrato do estado por sondagem, sem mudar CSP nem capabilities. Por decisão do operador
(block-004, dec-023), o painel de gestão do RabbitMQ deixa de ser publicado por padrão: a porta 15672
só é publicada (em `127.0.0.1`) por opt-in explícito no terminal, e o app nunca liga esse opt-in
(seção "Painel de gestão do RabbitMQ fora da publicação padrão"). Detalhes e fontes em
[research.md](./research.md).

## Technical Context

**Language/Version**: Rust 1.90, edition 2021 (`desktop/src-tauri/Cargo.toml:5-6`); TypeScript 7.0.2 +
Svelte 5.59.1 (`desktop/package.json`); Python ≥ 3.10 (`pyproject.toml:7`) para o subcomando
`models`.
**Primary Dependencies**: tauri 2.12.7, tauri-plugin-dialog 2.8.4, serde 1.0.229, serde_json 1.0.154,
thiserror 2.0.21 (`desktop/src-tauri/Cargo.toml:13-24`); nova direta `libc` 0.2.191 (research,
Decision 17); Python: `huggingface_hub` 1.33.0 (hoje transitiva via docling 2.151.1, `uv.lock`;
passa a direta no `pyproject.toml` como `huggingface-hub>=1.33,<2`, Apache-2.0).
**Storage**: arquivos — `stack-settings.json` (`app_config_dir`), `worker-pool.json` e
`lifecycle.lock` (`app_data_dir`), `<repo>/stack/.env` (existente, `stack/up.sh:19`, `ENV_FILE=`). Sem banco no app.
**Testing**: `uv run pytest`; `cargo test` (com `tempfile` 3.27.0); `npm test` = `vitest run`
(`desktop/package.json`); integração com compose atrás de `ARTIGO2MD_STACK=1`.
**Target Platform**: macOS 11+ (`desktop/src-tauri/tauri.conf.json:29-40`), Apple Silicon e Intel; o
runtime Docker é pré-requisito (`docs/briefing.md:86`).
**Project Type**: desktop-app (núcleo Rust + webview) + subcomando de CLI Python.
**Performance Goals**: mudança de estado de serviço visível em ≤ 10 s (SC-003); progresso de download
atualizado em ≤ 5 s (SC-008); "Iniciar infraestrutura" + "Iniciar workers" ≤ 3 min com imagens e
modelos presentes (SC-001); parada total ≤ 60 s (SC-005), medida a partir da última das duas ações:
"Parar workers" usa até 45 s; o `down.sh` (`docker compose down`) não tem prazo declarado no script, e o
tempo real é medido no teste de integração do cenário 3/4 do quickstart, com falha se passar de 60 s
somados.
**Constraints**: só `127.0.0.1` (FR-037); credenciais fora de tela, registro e linha de comando
(FR-011); nada de `docling` no processo do app nem no ramo principal da CLI (CLAUDE.md, `worker.py`);
`convert`/`doctor`/`serve` inalterados (FR-039); utilitários POSIX do macOS apenas.
**Scale/Scope**: app monousuário; 3 serviços, 1 pool, 2 etapas de primeira execução, 3 runtimes.

## Constitution Check

*GATE: Deve passar antes do Phase 0. Re-checar após Phase 1.*

| Princípio | Status | Notas |
|-----------|--------|-------|
| I. Processamento 100% Local | PASS | O app só conecta em `127.0.0.1` (sondagens no Rust, CSP inalterada). Downloads de imagens e modelos só após confirmação e feitos pelo Docker e pelo processo `artigo2md models`, nunca pelo app. `HF_HUB_DISABLE_TELEMETRY=1`; sem telemetria nem checagem de atualização. Portas publicadas seguem em loopback (`stack/compose.yaml`; endereço não configurável); o painel de gestão do RabbitMQ sai da publicação padrão (dec-025). |
| II. Licenças permissivas | PASS | Novas diretas `libc` (`MIT OR Apache-2.0`) e `huggingface-hub` (Apache-2.0, já transitiva). `tqdm` (`MPL-2.0 AND MIT`, transitiva, sem modificação) registradas em research, Decision 17. Nenhuma dependência npm nova; nada GPL/AGPL. |
| III. Português | PASS | Textos novos em `desktop/src/lib/messages.ts` e nos módulos do guia; mensagens do `models` em pt-BR; códigos de saída traduzidos (contracts). |
| IV. Testes antes do commit | PASS | `uv run pytest`, `cargo test`, `npm test` sem Docker (executáveis falsos no `PATH`, canal AMQP falso já existente); integração atrás de `ARTIGO2MD_STACK=1`. |
| V. Veracidade de dados | PASS | Contratos existentes com fonte (`contracts/external-processes.md`); contratos novos rotulados PROPOSTA; pontos não observados marcados "a validar na implementação" (bytes no `--progress json`; Compose para colima). |

## Project Structure

### Documentation (this feature)

```
docs/specs/desktop-stack-control/
├── spec.md
├── plan.md                         # este arquivo
├── research.md                     # Phase 0
├── data-model.md                   # Phase 1
├── quickstart.md                   # Phase 1
└── contracts/
    ├── tauri-commands.md           # comandos novos [PROPOSTA]
    ├── models-cli.md               # artigo2md models [PROPOSTA]
    └── external-processes.md       # contratos existentes consumidos (com fonte)
```

### Source Code (repository root)

Arquivos existentes alterados e novos (novos marcados com `+`):

```
desktop/
├── src-tauri/
│   ├── Cargo.toml                  # + dependência libc
│   └── src/
│       ├── lib.rs                  # registra comandos novos, estado InfraState, CloseRequested/ExitRequested
│       ├── commands.rs             # comandos de contracts/tauri-commands.md
│       ├── error.rs                # códigos novos
│       ├── log.rs                  # recebe só linhas já mascaradas
│       └── stack/                  # + módulo novo
│           ├── mod.rs              # InfraState, retrato, Mutex de operação
│           ├── exec.rs             # Command com args isolados, PATH, timeout, leitura de linhas
│           ├── locate.rs           # resolução de docker/uv/colima (Decision 3)
│           ├── runtime.rs          # detecção de runtimes (Decision 2)
│           ├── repo.rs             # verificação da pasta do repositório (Decision 4)
│           ├── env_file.rs         # leitura/criação/edição parcial do .env (Decision 5)
│           ├── secret.rs           # tipo Secret + mascaramento
│           ├── data_dir.rs         # pasta de dados (Decision 6)
│           ├── lifecycle.rs        # up.sh/down.sh e tradução de códigos (Decision 7)
│           ├── images.rs           # etapa imagens (Decision 8)
│           ├── models.rs           # etapa modelos via artigo2md models (Decision 9)
│           ├── health.rs           # sondagens Postgres/AMQP/HTTP (Decision 10)
│           ├── pool.rs             # pool de workers, parada calma, órfão (Decision 12)
│           ├── instance_lock.rs    # flock em lifecycle.lock (Decision 14)
│           └── stack_settings.rs   # stack-settings.json (Decision 16)
├── src/
│   ├── App.svelte                  # nova view "infra"
│   ├── components/
│   │   ├── Sidebar.svelte          # entrada "Infraestrutura" + indicador resumido
│   │   ├── InfraPanel.svelte       # + painel de saúde e ações
│   │   ├── FirstRunList.svelte     # + etapas da primeira execução
│   │   ├── InstallGuide.svelte     # + guia passo a passo
│   │   └── StackSettings.svelte    # + seção nas Configurações (repositório, workers, memória, avançadas)
│   └── lib/
│       ├── api.ts                  # funções dos comandos novos
│       ├── types.ts                # tipos de data-model.md; View += "infra"
│       ├── messages.ts             # textos pt-BR e tradução de códigos
│       ├── infra.svelte.ts         # + sondagem do retrato (1 s / 3 s)
│       └── guide.ts                # + conteúdo do guia com URL de fonte por passo
└── tests/
    ├── infra.test.ts               # + estados, resumo, mensagens
    ├── guide.test.ts               # + conteúdo, passos, sem ação de abrir navegador
    └── security-config.test.ts     # inalterado (deve continuar passando)
pyproject.toml                      # + huggingface-hub>=1.33,<2 (direta)
.gitignore                          # + stack/.env.*.tmp (M9)
README.md                           # painel fora da publicação padrão; risco de DNS rebinding atualizado
stack/                              # artefatos da container-stack (dec-025; autorizado em block-004)
├── compose.yaml                    # rabbitmq publica só a 5672 (sai a linha 36 da versão anterior)
├── compose.management.yaml         # + opt-in: publica 127.0.0.1:<porta>:15672 [PROPOSTA]
├── .env.example                    # ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT comentada como "só com o opt-in"
└── up.sh                           # + --management-panel [PROPOSTA]; porta/URL do painel só com o opt-in
src/artigo2md/jobqueue/topology.py  # mensagem da linha 91 aponta o opt-in do painel
src/artigo2md/
├── cli.py                          # ramo "models" importado sob demanda
└── modelcache.py                   # + status/download dos modelos (contracts/models-cli.md)
tests/
├── test_models_cli.py              # + contrato JSON, códigos de saída, sem rede
└── test_desktop_stack_integration.py # + [ARTIGO2MD_STACK=1] roundtrip /v1/status, ciclo completo
```

**Structure Decision**: toda lógica de sistema num módulo Rust novo `stack/`, seguindo o padrão do
desktop-shell (núcleo Rust faz E/S, webview só apresenta); o mecanismo de modelos fica no pacote
Python, como pede FR-036, num módulo separado que não importa `docling`.

## Convenções de Borda

| Camada | Case style | Validação | Fonte da verdade |
|--------|------------|-----------|------------------|
| Nomes de comando Tauri | snake_case | `generate_handler!` | `desktop/src-tauri/src/lib.rs`; `contracts/tauri-commands.md` |
| Payload IPC (Rust ↔ TS) | camelCase | serde `rename_all = "camelCase"`, `deny_unknown_fields` na entrada | `data-model.md`; `desktop/src/lib/types.ts` |
| Enums no IPC | camelCase (valores como `dockerDesktop`, `notInstalled`) | serde | `data-model.md` |
| Códigos de erro | camelCase | `ErrorCode` ↔ `ERROR_CODES` | `desktop/src-tauri/src/error.rs`; `types.ts` |
| `stack-settings.json`, `worker-pool.json` | camelCase | serde + `is_valid` | `data-model.md` |
| `<repo>/stack/.env` | UPPER_SNAKE (existente) | regras do `up.sh` | `stack/.env.example`; `stack/up.sh:49-154` |
| JSON Lines de `artigo2md models` | snake_case | teste de contrato em pytest | `contracts/models-cli.md` |
| JSON da API (`/v1/status`) | snake_case (existente) | serde com `rename` por campo | `docs/specs/project-api/contracts/http-api.md:170-176` |
| `--progress json` do Compose | camelCase/minúsculas do Compose (existente) | parse tolerante (campos ausentes ignorados) | observado (research, Decision 8) |
| Textos ao usuário | pt-BR | revisão + testes de mensagens | `desktop/src/lib/messages.ts` |

**Mapper layer**: serde no Rust (sem ORM). Tipos de entrada de processos externos (`ModelsEvent`,
`ComposeProgressLine`, `ApiStatus`) ficam separados dos tipos do IPC, e a conversão é explícita em
`stack/`. Sem Zod: a interface confia no tipo do comando, como no desktop-shell (`desktop/src/lib/api.ts:41-47`, `call<T>` com `invoke<T>`).

## Segurança (gate owasp-security, 2026-11-26)

Mitigações incorporadas ao design a partir do gate (0 crítico, 1 alto, 11 médios, 9 baixos). Elas
prevalecem sobre qualquer detalhe divergente de `research.md`.

| # | Origem | Mitigação |
|---|--------|-----------|
| M1 | ALTO — webview poderia apontar `repoPath`/`dataDir` para pasta própria e obter execução de código via `start_stack`/`uv run` | `repoPath` e `dataDir` só mudam por comandos Rust que abrem o seletor nativo de pasta (`pick_repo_folder`, `pick_data_dir`, com `tauri-plugin-dialog` chamado do Rust, como o `open_folder` do desktop-shell, `desktop/src-tauri/src/commands.rs:112`, `pub async fn open_folder`). `save_stack_settings` recusa `repoPath`/`dataDir` diferentes dos valores gravados (`invalidStackSettings`). Teste Rust e teste npm cobrem a recusa |
| M2 | Médio — processo local escutando na porta do RabbitMQ com a stack parada receberia as credenciais | `start_workers` exige que `compose … ps --status running --services` liste `rabbitmq` **e** que a sondagem AMQP passe, verificados imediatamente antes de iniciar o processo. Checagem do dono do socket em escuta: viável e implementada para Docker Desktop (11.5); OrbStack e colima ficam "indeterminado" até serem observados |
| M3 | Médio — scripts do repositório executados sem checagem | `RepoCheck` exige que a pasta, `stack/`, `up.sh`, `down.sh`, `compose.yaml` pertençam ao uid corrente, sejam arquivos regulares (sem link simbólico) e não tenham escrita para grupo/outros; o caminho canônico aparece na tela |
| M4 | Médio — `uv` poderia baixar Python ou pacotes | Toda chamada ao `uv` com `UV_OFFLINE=1` e `UV_PYTHON_DOWNLOADS=never` (observado em `uv run --help`: `--offline [env: UV_OFFLINE=]`, `--no-python-downloads [env: "UV_PYTHON_DOWNLOADS=never"]`). `UV_OFFLINE` não afeta o download dos modelos, feito pelo `huggingface_hub` |
| M5 | Médio — ambiente herdado (`PYTHONPATH`, `HF_ENDPOINT`, proxies, `UV_INDEX_URL`, `DOCKER_*`) | `env_clear()` em todo filho e lista permitida: `HOME`, `USER`, `LOGNAME`, `TMPDIR`, `LANG`/`LC_*`, o `PATH` construído e valores explícitos (`HF_HUB_DISABLE_TELEMETRY=1`, M4). Credenciais só no `Command::env` do worker; nunca em `std::env::set_var`, nunca em docker, `up.sh` ou `models` |
| M6 | Médio — sequestro de `PATH` | `PATH` dos filhos com `/usr/bin:/bin:/usr/sbin:/sbin` primeiro. Executável resolvido aceito só se for caminho absoluto de arquivo regular executável, do uid corrente ou de root, sem escrita para grupo/outros |
| M7 | Médio — `zsh -l` roda arquivos do usuário e `command -v` devolve alias | Usar `whence -p`, stdin nulo, tempo máximo de 5 s com encerramento do grupo, só a última linha, validada por M6; senão a lista fixa |
| M8 | Médio — PID/PGID reciclado | Antes de qualquer sinal: listar membros do grupo (`proc_listpids` com `PROC_PGRP_ONLY`) e conferir horário de início ≥ `startedAt`; para órfão, também `proc_pidpath(pid)` igual ao `uv` resolvido e argumentos contendo `artigo2md worker`. Qualquer falha = nenhum sinal enviado. Disponibilidade exata dessas funções no `libc` 0.2.191: a validar na implementação |
| M9 | Médio — `.env` com link simbólico, corrida na criação, temporário com segredo | `lstat` recusa link, não regular ou de outro uid. Temporário `.env.<aleatório>.tmp` com `create_new`, modo `0600` e `O_NOFOLLOW`, `fsync`, publicação sem sobrescrever (`link` + `unlink`; se o destino surgir no meio, desiste e usa o existente). Reescrita parcial mantém `0600` e avisa se o modo atual for mais amplo. `.gitignore` ganha `stack/.env.*.tmp` |
| M10 | Médio — `Debug` de `Command` mostra o ambiente | Nunca formatar `Command` nem erro de spawn com `{:?}`; `Secret` até o `.env()`; só `compose config --images` é permitido (nunca `config` sem filtro, que resolve credenciais); mascaramento antes de qualquer buffer, `technicalLog`, `log.rs` ou detalhe de `error.rs`; teste procura a senha gerada em toda saída capturada |
| M11 | Médio — tags de imagem mutáveis | Risco aceito nesta feature (fixar por digest é escopo da `container-stack`, que define `stack/compose.yaml` e o Dockerfile); registrado na tabela de riscos |
| M12 | Médio — modelo na revisão `main` | Mantida a revisão que o próprio Docling usa (`main` para heron, `v2.3.0` para TableFormer): fixar um SHA no prefetch faria a conversão, que pede `main`, não achar `refs/main` no cache em modo offline. Risco aceito e registrado; `HF_ENDPOINT` removido do ambiente (M5) |
| M13 | Baixo — cliente HTTP | `TcpStream` direto para `SocketAddr` `127.0.0.1:<porta>`, sem DNS, sem proxy, sem seguir redirecionamento, resposta limitada a 64 KiB, 1 s/1 s |
| M14 | Baixo — `dataDir` amplo demais | Recusar `/`, `$HOME`, `~/Library`, a pasta do repositório e seus ancestrais; criar a pasta padrão com modo `0700` |
| M15 | Baixo — argumento vindo de saída externa | `--context=<c>` com nome validado (`[A-Za-z0-9._-]`), `--` antes de nomes de imagem |
| M16 | Baixo — `confirmed` forjável | Diálogo nativo de confirmação no Rust antes de `prepare_images`/`prepare_models`; `confirmed` no IPC é só UX |
| M17 | Baixo — descritores herdados | `lifecycle.lock` aberto com `O_CLOEXEC`, `O_NOFOLLOW`, `0600`; stdin nulo em todo filho; `app_data_dir` e `app_config_dir` com `0700` |
| M18 | Baixo — registros editados entre sessões | `worker-pool.json` e `stack-settings.json` atômicos, `0600`, `deny_unknown_fields`; órfão só é adotado/parado se `repoPath` do registro = configuração atual e M8 passar |
| M19 | Baixo — ambiente visível ao mesmo uid | Aceito: as credenciais ficam no ambiente do processo `uv` (visível ao mesmo usuário e a root); FR-011 exige o ambiente; `Secret` zerado ao descartar |
| M20 | Baixo — travamento da interface | Todo comando externo com tempo máximo e encerramento do grupo; uma verificação por vez; leitura do retrato nunca pega o Mutex de operação; linhas limitadas a 4 KiB |
| M21 | Baixo — fechar o app travado | Teto de 60 s (SC-005) para o encerramento ao fechar; depois o app sai e registra os PIDs remanescentes; só a instância com o lock resolve órfão |

### Contrato de falha fechada de M8 e resultado aceitável de M2 (security CHK013)

Tornam M2 e M8 especificáveis mesmo com as sondas 11.3 e 11.5 pendentes: o requisito vale como escrito e
a sonda só decide *como* é satisfeito, nunca *se* é.

- **M8, falha fechada**: sem `proc_listpids` e `proc_pidpath` utilizáveis (símbolo ausente no `libc` 0.2.191
  sem `extern` documentado, chamada que falha ou retorna erro), ou com qualquer conferência falhando
  (grupo vazio inesperado, horário de início < `startedAt`, `proc_pidpath` diferente do `uv` resolvido,
  argumentos sem `artigo2md worker`), o app MUST NOT enviar nenhum sinal (nem `SIGTERM` nem `SIGKILL`, nem a
  PID nem ao grupo), MUST descartar o registro `worker-pool.json` e MUST mostrar o aviso em português "Não foi
  possível confirmar com segurança que o processo anterior é um worker deste app; nada foi encerrado. Se ele
  ainda estiver ativo, encerre-o pelo Monitor de Atividade." O pool passa a `stopped` sem `orphanFound`.
  Teste Rust cobre cada ramo de falha (11.3.3).
- **M2, resultado aceitável da checagem do dono do socket em escuta, por runtime**: (a) *conferido* — o
  processo em escuta na porta do RabbitMQ pertence ao runtime Docker em uso (processo de publicação de
  porta do próprio runtime) ou a checagem confirma o container: `start_workers` prossegue; (b) *divergente* —
  outro processo detém a porta: `start_workers` MUST recusar com `stackNotRunning` e nenhuma credencial é
  passada a nenhum processo; (c) *indeterminado* — o runtime não permite a checagem: o app segue só com as
  duas conferências já exigidas (RabbitMQ listado em execução pelo Compose **e** sondagem AMQP saudável,
  imediatamente antes de iniciar), sem relaxá-las, e registra no `technicalLog` que o dono do socket não pôde
  ser conferido. Os critérios por runtime (Docker Desktop, OrbStack, colima) são fixados por 11.5 a partir de
  observação; nenhum é presumido aqui.
- **Sonda inviável**: se 11.3 ou 11.5 não oferecer mecanismo viável, o resultado é registrado nesta seção como
  **risco aceito explícito** e é aberto um bloqueio humano; a aceitação pertence ao dono do produto, não ao
  agente (tarefa 1.10.3).

## Riscos e pontos a validar na implementação

| Risco | Mitigação |
|-------|-----------|
| Campos de bytes no `--progress json` de um pull real | Observado em 11.1 (ver "Resultado de 11.1"): `current`/`total` por camada nas linhas "Downloading"; a tela mostra o volume baixado, contagem de imagens e hora (ramo "tamanho desconhecido" de FR-032), sem percentual geral |
| Espaço livre do disco virtual do runtime não legível | Checar o volume da pasta pessoal contra ~1,5 GB medido e traduzir "disco cheio" do pull (research, Decision 8) |
| Compose para colima exige configurar plugin pelo Homebrew | Guia manda seguir a instrução exibida pelo Homebrew; diagnóstico "Docker Compose ausente" cobre o caso |
| Lista de modelos muda com upgrade do Docling | Teste confere a constante contra as specs do Docling instalado |
| Painel do RabbitMQ sem proteção contra DNS rebinding (`README.md:184-185` aponta esta feature como responsável) | Tratado nesta feature (block-004, dec-023; desenho dec-025): o painel não é publicado por padrão; só o opt-in explícito no terminal o publica em `127.0.0.1`, e aí o risco volta, dito no README e na saída do `up.sh`. Ver seção abaixo |
| `up.sh` sem o opt-in sobre uma stack que está com o painel publicado | O atalho "já estava no ar" (hoje `stack/up.sh:200-213`) deixaria o painel aberto: sem o opt-in, o `up.sh` só toma o atalho se `docker compose port rabbitmq 15672` não devolver endereço; senão chama `up`. Que o `up` recria o container ao perder a porta: observado em 11.4 e coberto pelo teste de integração |
| Credenciais visíveis via `docker inspect` (`stack/.env.example:15-16`) | Fora do escopo; aceito na `container-stack` |
| Tags de imagem e revisão `main` do modelo mutáveis | Risco aceito (M11, M12) |

### Dono verificável de cada ponto "a validar na implementação" (api CHK014)

| Ponto a validar | Tarefa dona | Critério de aceitação | Resultado alternativo se a sonda falhar |
|-----------------|-------------|-----------------------|-----------------------------------------|
| Bytes no `--progress json` do pull | 11.1 | amostra real de pull guardada como fixture e teste Rust sobre ela; campos de bytes presentes ou ausentes registrados com data e versão do Compose | sem bytes: contagem de imagens com atividade (ramo "tamanho desconhecido" de FR-032) |
| Compose para colima | 11.2 | `docker compose version` funciona com colima após a instalação descrita no guia | sem ambiente: "não verificado" no relatório, sem presumir; texto do guia ajustado com fonte se exigir passo extra |
| `proc_listpids`/`proc_pidpath`/`proc_pidinfo` e constantes no `libc` 0.2.191 | 11.3 | assinaturas conferidas no código-fonte do `libc` e teste Rust sobre o próprio processo e um grupo de teste | declarar via `extern` com fonte, ou falha fechada de M8 (seção acima) |
| Saída de `docker compose port rabbitmq 15672` sem publicação | 11.4 (11.4.1) | forma real observada e atalho de `up.sh` (2.2.4) ajustado a ela | se a forma for ambígua, o atalho cai para chamar `up` sempre (mais lento, mais seguro) |
| `up` recria o container ao perder a porta | 11.4 (11.4.2) | observado e virado asserção do teste de 2.6 (`ARTIGO2MD_STACK=1`) | se não recriar, o `up.sh` sem a opção passa a derrubar o serviço antes de subir (a decidir com o operador, pois muda o comportamento da `container-stack`) |
| `down` remove o container iniciado com o arquivo adicional | 11.4 (11.4.2) | observado e virado asserção do teste de 2.6 | se não remover, `down.sh` passa a receber o arquivo adicional (mudança pequena em `stack/down.sh`, registrada como Decisão) |
| Dono do socket em escuta (M2) | 11.5 | decisão por runtime registrada em "Contrato de falha fechada de M8 e resultado aceitável de M2" e teste Rust | risco aceito explícito + bloqueio humano (1.10.3) |

**Resultado de 11.3 (2026-11-27, `libc` 0.2.191, macOS arm64, SDK 27.2)**: `proc_listpids`, `proc_pidinfo`,
`proc_pidpath`, `proc_bsdinfo` e `PROC_PIDTBSDINFO` existem no `libc` 0.2.191 (`src/unix/bsd/apple/mod.rs`, só
`target_os = "macos"`). `PROC_PGRP_ONLY` **não** existe no `libc`: o app declara a constante (valor 2, de
`sys/proc_info.h` do SDK, `#define PROC_PGRP_ONLY 2`) em `desktop/src-tauri/src/stack/procinfo.rs`. Os argumentos
de um processo vêm de `sysctl({CTL_KERN, KERN_PROCARGS2, pid})` (constantes no `libc`). Testes Rust consultam o
próprio processo e um grupo criado pelo teste (`procinfo::tests`); fora do macOS todas as respostas são `None`
(falha fechada de M8). Mecanismo viável: não há risco aceito a registrar para M8.

**Resultado de 11.1 (2026-11-27, Docker Compose v5.6.1, Docker Desktop 29.9.2)**: observado com `docker compose -p
<projeto temporário> -f <arquivo temporário> --progress json pull` sobre `alpine:3.20` (imagem pública de ~4 MB, projeto
Compose descartável; as imagens da stack NÃO foram baixadas e a imagem de teste foi removida depois). As linhas saem em
**stderr**. Uma camada em download traz bytes: `{"id":"3f26bc2dec0b","parent_id":"Image alpine:3.20","status":"Working",
"text":"Downloading","details":"2.097MB","current":2097152,"total":4092319,"percent":51}`; camadas que concluem rápido
vêm só como `Download complete` (sem `current`/`total`). Logo há bytes **por camada, parciais**: o total geral nunca é
conhecido de antemão. Decisão: `images.rs` soma os `current` observados em `FirstRunStep.downloadedBytes` (valor
mínimo, monotônico) e mantém `totalBytes` ausente, de modo que a tela mostra volume + contagem de imagens + hora, nunca
um percentual inventado. Fixture: `desktop/src-tauri/src/stack/fixtures/compose-pull-progress.jsonl` (saída capturada,
sem credenciais); teste `captured_pull_output_reports_downloaded_bytes_and_one_image_done`. Limite da observação: uma
imagem com várias camadas grandes (as da stack, ~1,5 GB) não foi baixada, então a cadência das linhas "Downloading" em
imagens grandes não foi observada.

**Resultado de 11.2 (2026-11-27)**: **não verificado**. Esta máquina não tem colima (`colima`: command not found) nem
OrbStack; nada foi presumido. Falta a verificação de `docker compose version` após a instalação descrita no guia para
colima. A falha de Compose ausente continua coberta por teste (`RuntimeOverall::ComposeMissing`,
`desktop/src-tauri/src/stack/runtime.rs`, teste da tarefa 7.1).

**Resultado de 11.4 (2026-11-27, Docker Compose v5.6.1, Docker Desktop)**, observado num projeto Compose de teste:
- `docker compose port rabbitmq 15672` com a porta **publicada**: exit 0, stdout `127.0.0.1:<porta>`.
- Com a porta **não publicada**: exit 1, stdout vazio e stderr `no port 15672/tcp for container <nome>: 5672/tcp`.
  O atalho `panel_published` do `up.sh` trata exit != 0 ou saída sem número como "não publicado": já estava correto,
  nenhum ajuste foi necessário.
- `up.sh` sem a opção sobre uma stack com o painel publicado **recria só o RabbitMQ** (id do container muda; a 15672
  deixa de estar publicada); chamado de novo, devolve "A infraestrutura já estava no ar.".
- `down.sh` (que não recebe o arquivo adicional) **remove** o container iniciado com ele e mantém os volumes.
Viraram asserções em `tests/test_stack_integration.py` (`test_management_panel_is_not_published_by_default` e
`test_panel_opt_out_recreates_rabbitmq_and_down_removes_it`). Nenhum resultado alternativo foi acionado.

**Resultado de 11.5 (2026-11-27)**: **Docker Desktop: mecanismo viável e observado.** Com uma stack de teste no ar,
`lsof -nP -iTCP:<porta> -sTCP:LISTEN -Fpcn` (sem credenciais e sem rede) lista o processo `com.docker.backend`
(`/Applications/Docker.app/Contents/MacOS/com.docker.backend`, confirmado por `ps` e por `proc_pidpath`) como dono
de uma porta publicada pelo projeto de teste. A saída capturada guardada em `socket_owner.rs:127` (`OBSERVED`) tem uma
só porta (`n127.0.0.1:54245`); que 5672 e 5432 tenham o mesmo dono não está nessa captura e fica como não verificado
por ela. Implementado em `desktop/src-tauri/src/stack/socket_owner.rs` e ligado a
`check_worker_preconditions`: resultado *conferido* segue; *divergente* (outro executável escutando) recusa com
`stackNotRunning` antes de qualquer credencial; *indeterminado* (lsof ausente, saída vazia, executável ilegível) segue
só com as duas conferências já exigidas e registra o motivo no `technicalLog`. **OrbStack e colima: dono do socket não
observado** (runtimes não instalados aqui), logo o resultado para eles é *indeterminado* (alternativa c, já aceita em
"Contrato de falha fechada de M8 e resultado aceitável de M2"), sem critério presumido. Pendência: observar o dono
do socket com OrbStack e com colima e, se viável, acrescentar o critério por observação. Testes Rust:
`socket_owner::tests` (usa a saída real observada) e o roundtrip contra a stack real, que exige `Confirmed` com Docker
Desktop.

## Painel de gestão do RabbitMQ fora da publicação padrão (block-004, dec-023, dec-025)

Decisão do operador: tratar o DNS rebinding do painel nesta feature não publicando o painel por
padrão; alterar `stack/compose.yaml` está autorizado. Desenho escolhido (dec-025): opt-in explícito por
arquivo Compose adicional, porque uma variável no `compose.yaml` só troca o valor de uma entrada de
`ports`, não a omite. Sonda local (Docker Compose v5.6.1, `docker compose config`, sem rede): com só o
arquivo base, `rabbitmq` publica apenas `127.0.0.1:5672`; com base + adicional, as listas `ports` são
concatenadas (`5672` e `15672`, ambas `host_ip: 127.0.0.1`); a variável da porta do painel só é exigida
quando o arquivo adicional entra. O plugin de gestão continua na imagem `rabbitmq:4.4-management`
(`stack/compose.yaml:30`); só a publicação muda.

**Desenho** (nomes novos `[PROPOSTA — a validar na implementação]`):

- `stack/compose.yaml`: o serviço `rabbitmq` publica só `127.0.0.1:${ARTIGO2MD_RABBITMQ_PORT}:5672`
  (sai a linha 36 da versão anterior, a que publicava a 15672).
- `stack/compose.management.yaml` (novo): só `services.rabbitmq.ports` com
  `"127.0.0.1:${ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT:?…}:15672"`, mesmo IP literal fixo (FR-006 da
  container-stack continua valendo para toda porta publicada).
- `stack/up.sh --management-panel`: acrescenta `-f stack/compose.management.yaml` à chamada `dc`
  (`stack/up.sh:31-37`, função `dc`); valida e testa como ocupada a `ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT` (hoje
  `stack/up.sh:129-131,236-239`) e mostra a URL (hoje `stack/up.sh:179,191-198,285`: `PANEL_URL`, `print_panel_status`) só nesse modo, com aviso em português de que o painel
  fica sem proteção contra DNS rebinding enquanto publicado. Sem a opção: nenhuma linha de URL; uma linha
  "Painel do RabbitMQ: não publicado (use --management-panel)". Atalho "já estava no ar"
  (hoje `stack/up.sh:200-213`): só quando a publicação atual do painel bate com o modo pedido (consulta
  `docker compose port rabbitmq 15672`). Resolvido em 11.4 (ver "Resultado de 11.4" acima): com a porta não
  publicada a saída é exit 1 e stdout vazio, e o atalho já estava correto.
- `stack/down.sh`: inalterado (`docker compose down` usa o projeto `artigo2md`). Resolvido em 11.4: ele remove o
  container iniciado com o arquivo adicional, e mantém os volumes (asserção em
  `tests/test_stack_integration.py`, `test_panel_opt_out_recreates_rabbitmq_and_down_removes_it`).
- `stack/.env.example:29`: a chave fica, com comentário "usada só com `--management-panel`"; o app a
  copia ao criar o `.env` (research, Decision 5), sem expô-la para edição.
- `src/artigo2md/jobqueue/topology.py:91`: "Remova-a pelo painel" passa a dizer como abrir o painel
  (`sh stack/up.sh --management-panel`, avisando que ele fica sem proteção contra DNS rebinding até um
  `sh stack/up.sh` sem a opção) ou remover pela linha de comando do broker; mesmo texto em
  `docs/specs/conversion-queue/contracts/worker-cli.md:64`.
- Gate owasp-security desta revisão (2026-11-26): 0 crítico, 0 alto. Baixos aceitos: (1) com o opt-in
  ligado o risco de DNS rebinding volta, por escolha explícita da pessoa no terminal, com aviso; (2) um
  painel aberto pelo terminal com a stack já no ar não aparece no app até o próximo "Iniciar
  infraestrutura", que o fecha (atalho acima), e também se fecha com `sh stack/down.sh` ou "Parar
  infraestrutura" (observado em 11.4); o README diz isso; (3) os testes de integração que usam a
  API do painel o publicam em `127.0.0.1` só durante a execução atrás de `ARTIGO2MD_STACK=1`.
- O `doctor` não muda: não sonda o painel (`src/artigo2md/infra.py:28-32` lista só `postgres`,
  `rabbitmq` 5672 e `api`). Nenhuma sondagem de saúde desta feature usa o painel (research, Decision 10).

**Efeito nesta feature**: o app chama `up.sh` sempre sem `--management-panel`; `InfraSnapshot` perde
`managementAddress`; a porta do painel sai das configurações avançadas e dos `ports` de
`get_stack_settings`/`save_stack_settings` (o app reescreve quatro chaves do `.env`, não cinco); FR-013,
FR-029 e o cenário 8 da US de saúde foram ajustados na spec. Quickstart ganha a verificação de que,
depois de "Iniciar infraestrutura", `docker compose -p artigo2md port rabbitmq 15672` não devolve
endereço.

**Impacto nos artefatos e testes da `container-stack`** (vira tarefa no `create-tasks`; nada é editado
nesta etapa; os números de linha da tabela são os da época do plano, antes das edições, e servem só para achar o trecho):

| Artefato | Trecho | Ajuste |
|----------|--------|--------|
| `docs/specs/container-stack/spec.md` | US1 cenário 1 (linha 21), US1 cenário 4 (linha 24), US2 cenário 1 (linha 40), FR-001 (113), FR-006 (118), entidade (143) | painel habilitado na imagem, mas publicado só por opt-in; FR-006 vale para a porta quando publicada; o atalho "já estava no ar" só com o modo do painel igual ao pedido (tarefa 13.6) |
| `docs/specs/container-stack/contracts/stack-scripts.md` | linhas 22 e 45; atalho, `down.sh` e tabela de etapas | saída do `up.sh` e tabela de variáveis com o opt-in; linha do painel no atalho, efeito do `down.sh` sobre o painel e mensagens conforme o script (tarefa 13.7) |
| `docs/specs/container-stack/quickstart.md` | linhas 9 e 27 | URL do painel e `docker compose port … 15672` só com o opt-in |
| `docs/specs/container-stack/research.md` | Decision 5 (54), Decision 9 (94), Decision 11 riscos (129), resultados (143, 144, 146); chamada do Compose e atalho (84) | publicação padrão sem 15672; risco de DNS rebinding passa a "tratado por não publicação"; nota datada com o arquivo adicional do opt-in e a condição do atalho (tarefa 13.8) |
| `docs/specs/container-stack/data-model.md` | linhas 18, 29 e 42 | transição "já estava no ar" com a condição do painel; `container_ports` do rabbitmq: 15672 só com o opt-in; validação da porta do painel só com o opt-in (tarefa 13.9) |
| `docs/specs/container-stack/checklists/security.md` | CHK006 (linha 15) | anotar a resolução por dec-023/dec-025 |
| `docs/specs/project-api/quickstart.md:56`, `docs/specs/conversion-queue/plan.md:36` e `docs/specs/conversion-queue/tasks.md:28` | uso manual do painel | dizer que exige `--management-panel` |
| `tests/test_stack_files.py` | linha 99 (`{"5672", "15672"}`), 208, 222-223 | base publica só `5672`; arquivo adicional publica só `15672` com `127.0.0.1` |
| `tests/test_stack_scripts.py` | linhas 101, 179, 270, 345, 407 | validação e porta ocupada do painel só com `--management-panel`; sem a opção, nenhuma URL |
| `tests/test_stack_integration.py` | 48, 134-152, 246, 263, 325-326, 367-388 | sobe com `--management-panel` onde usa a API do painel; teste novo: sem a opção, 15672 não publicada |
| `tests/test_jobqueue_integration.py` (48, 78, 161, 372-411) e `tests/test_api_integration.py` (213-214, 590, 630, 737, 791) | API HTTP do painel | sobem a stack com o opt-in |
| `README.md` | 123, 164-165, 184-185 | painel fora da publicação padrão, como abrir pelo terminal e o risco quando aberto |
| `desktop/README.md` | linha 34 | o painel não é publicado e só o terminal (`sh stack/up.sh --management-panel`) o abre; remete ao `README.md` da raiz para o fechamento |

**Rastreio da tabela de impacto até as tarefas** (cada linha tem exatamente uma tarefa dona em
[tasks.md](./tasks.md); api CHK013):

| Artefato (linha da tabela de impacto) | Tarefa |
|---------------------------------------|--------|
| `docs/specs/container-stack/spec.md` | 3.1 |
| `docs/specs/container-stack/contracts/stack-scripts.md` | 3.2 |
| `docs/specs/container-stack/quickstart.md` | 3.3 |
| `docs/specs/container-stack/research.md` | 3.4 |
| `docs/specs/container-stack/data-model.md` | 3.5 |
| `docs/specs/container-stack/checklists/security.md` | 3.6 |
| `docs/specs/project-api/quickstart.md`, `docs/specs/conversion-queue/plan.md` e `tasks.md` | 3.7 |
| `tests/test_stack_files.py` | 2.4 |
| `tests/test_stack_scripts.py` | 2.5 |
| `tests/test_stack_integration.py` | 2.6 |
| `tests/test_jobqueue_integration.py` e `tests/test_api_integration.py` | 2.7 |
| `README.md` | 3.8 |

Itens de desenho fora da tabela: `stack/compose.yaml` e `stack/compose.management.yaml` → 2.1;
`stack/up.sh` → 2.2; `stack/.env.example`, `src/artigo2md/jobqueue/topology.py` (mensagem da linha 91) e
`docs/specs/conversion-queue/contracts/worker-cli.md` → 2.3.

## Rastreabilidade FR → design

| FR | Onde |
|----|------|
| FR-001, FR-002 | research D2, D3; data-model RuntimeReport |
| FR-003 | research D10 (cadência), D11; tauri-commands `refresh_infra` |
| FR-004 a FR-006 | research D15; data-model GuideStep, GuideProgress |
| FR-007 a FR-009 | research D7, D8; external-processes `up.sh`/`down.sh` |
| FR-010, FR-011 | research D5, D13; plan M5, M9, M10, M19 |
| FR-012 | research D6; data-model DataDirCheck; plan M14 |
| FR-013 | research D5; tauri-commands `save_stack_settings`; plan "Painel de gestão do RabbitMQ" |
| FR-014 | research D7; tauri-commands Error Responses; external-processes |
| FR-015 | research D14; data-model Operation |
| FR-016 | research D4; data-model RepoCheck; plan M1, M3, M4 |
| FR-017 | research D1; plan M15 |
| FR-018 a FR-024 | research D12; data-model WorkerPool, WorkerPoolRecord; plan M2, M8, M18, M21 |
| FR-025 a FR-029 | research D10; data-model ServiceStatus, InfraSnapshot; plan M13; FR-029 também plan "Painel de gestão do RabbitMQ" |
| FR-030 a FR-035 | research D8, D9; data-model FirstRunStep; contracts/models-cli.md |
| FR-036 | research D9, D12 (`HF_HUB_OFFLINE=1` no worker); contracts/models-cli.md |
| FR-037 | research D1, D10, D11; plan M5, M13; quickstart cenário 9 |
| FR-038 | `desktop/src/lib/messages.ts`, `guide.ts`, mensagens do `models` em pt-BR; Constitution Check III |
| FR-039 | research D9 (subcomando separado, sem mudar `convert`/`doctor`/`serve`); quickstart cenário 9 |
| FR-040 | research D17 |
| FR-041 | quickstart (gates `ARTIGO2MD_STACK=1`); Project Structure (testes novos) |

## Complexity Tracking

Sem violações de constitution.

## Re-check pós-design

Revalidado após Phase 1: o design não cria serviço, porta nem dependência obrigatória além de `libc`;
não muda a CSP nem a capability; a única conexão de rede para fora (downloads) sai de processos que já
fariam esse download (Docker e Hugging Face via Python) e só após confirmação. Nota de 2026-11-27: no ambiente instalado, o `huggingface_hub` 1.33.0 declara `hf-xet` como dependência em arm64/x86_64 (`huggingface_hub-1.33.0.dist-info/METADATA:29`), e o download dos modelos pode ir, além do Hub, a um endpoint de armazenamento Xet que o próprio Hub indica na resposta (`huggingface_hub/utils/_xet.py:95`; `huggingface_hub/file_download.py:575-590`); o nome desse host não é afirmado aqui por falta de fonte. Todos os princípios
permanecem PASS.
