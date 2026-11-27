# Data Model: desktop-stack-control

Entidades do núcleo Rust (`desktop/src-tauri/src/`) e da interface (`desktop/src/lib/types.ts`).
Nomes de campo em camelCase na borda IPC e nos arquivos JSON (convenção do desktop-shell,
`docs/specs/desktop-shell/plan.md:127-139`). Todos os tipos e arquivos novos desta feature são
**[PROPOSTA — a validar na implementação]**; as chaves do `.env` e os comandos externos citados são
existentes e têm fonte. Nenhuma entidade é gravada em banco: o app não acessa o Postgres.

## Entity: StackSettings (arquivo `stack-settings.json`)

Configurações desta feature, em `app_config_dir` ao lado do `settings.json` do desktop-shell
(research, Decision 16). Escrita atômica, `deny_unknown_fields`.

| Field | Type | Required | Validation / default |
|-------|------|----------|----------------------|
| schemaVersion | integer | yes | = 1 |
| repoPath | string \| null | yes | caminho absoluto; `null` = não indicado |
| workers | integer | yes | ≥ 1; padrão 4 (`src/artigo2md/jobqueue/settings.py:28-34`) |
| minFreeMemoryMb | integer | yes | ≥ 0; padrão 4822 (mesma fonte) |
| dataDir | string | yes | caminho absoluto canônico; padrão `~/artigo2md-dados` expandido (research, Decision 6). Proposta até o `.env` existir; depois, espelho de leitura de `ARTIGO2MD_DATA_DIR` (research, Decision 5). Só muda por `pick_data_dir` |
| guide | GuideProgress \| null | yes | `null` = guia nunca aberto |

Arquivo ausente → padrões sem aviso; inválido → padrões e aviso `reset` (mesmo comportamento de
`desktop/src-tauri/src/settings.rs:48-73`).

## Entity: GuideProgress

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| runtime | `"dockerDesktop"` \| `"orbstack"` \| `"colima"` \| null | yes | `null` = ainda na comparação |
| step | integer | yes | 0 ≤ step < número de passos do runtime |

## Entity: GuideStep (conteúdo estático em `desktop/src/lib/guide.ts`)

| Field | Type | Description |
|-------|------|-------------|
| runtime | RuntimeKind | runtime do passo |
| index | integer | posição (0..n-1); a interface mostra "passo i de n" |
| text | string | instrução curta em pt-BR |
| copyable | string[] | endereços ou comandos mostrados como texto copiável |
| sourceUrl | string | página oficial de onde o passo veio (research, Decision 15) |
| minMacos | string \| null | versão mínima do macOS do runtime, quando a fonte a declara |

Regra: nenhum passo tem ação de abrir navegador, baixar ou executar (FR-005); teste npm confere que
todo passo tem `sourceUrl`.

## Entity: StackParams (arquivo `<repo>/stack/.env`, existente)

Chaves e padrões de `stack/.env.example:22-39`; regras de `stack/up.sh:49-154` (passos 1 a 3b). O app lê todas as
chaves, mas só escreve as quatro marcadas como editáveis, e só cria o arquivo quando ele não existe
(research, Decision 5).

| Key | Editável pelo app | Default ao criar | Regra |
|-----|-------------------|------------------|-------|
| ARTIGO2MD_DATA_DIR | sim | `StackSettings.dataDir` | absoluto, existente e canônico (`up.sh:143-154`, `DATA_DIR=`); gravável é checagem só do app (research, Decision 6) |
| ARTIGO2MD_POSTGRES_PORT | sim | 5432 | 1..65535 (`up.sh:130-138`, `PORT_VARS`) |
| ARTIGO2MD_RABBITMQ_PORT | sim | 5672 | idem |
| ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT | não | 15672 | idem; usada só pelo opt-in do painel no terminal, nunca pelo app (`plan.md`, dec-025) |
| ARTIGO2MD_API_PORT | sim | 8778 | idem |
| POSTGRES_USER, POSTGRES_DB, RABBITMQ_DEFAULT_USER | não | `artigo2md` | não vazio (`up.sh:118-120`) |
| POSTGRES_PASSWORD, RABBITMQ_DEFAULT_PASS | não (só na criação) | 32 hex de `/dev/urandom` | ≥ 12, sem `$`, ≠ `troque-esta-senha` (`up.sh:22,89-91,124-125`: `EXAMPLE_PASSWORD`, teste de `$`, exemplo e tamanho) |

Permissão do arquivo criado: `0600`. Em memória, as credenciais ficam em `Secret` (sem
`Debug`/`Serialize` do valor).

## Entity: RuntimeReport

| Field | Type | Description |
|-------|------|-------------|
| runtimes | RuntimeEntry[3] | um por runtime, sempre na ordem dockerDesktop, orbstack, colima |
| inUse | RuntimeKind \| null | runtime do contexto corrente |
| overall | `"ready"` \| `"noRuntime"` \| `"stopped"` \| `"composeMissing"` \| `"ambiguous"` | classificação de FR-002 |
| dockerPath | string \| null | executável resolvido (research, Decision 3) |
| checkedAt | string (ISO 8601) | horário da verificação |

`RuntimeEntry`: `kind` (`"dockerDesktop"` \| `"orbstack"` \| `"colima"`), `state`
(`"notInstalled"` \| `"stopped"` \| `"running"`), `context` (string \| null, nome do contexto
Docker: `desktop-linux`, `orbstack`, `colima`; research, Decision 2).

Regras: `ready` exige um runtime `running`, `inUse` definido e Compose disponível; `ambiguous` quando o
contexto corrente é `default` e há mais de um `running`; ações de stack só liberadas em `ready`.

## Entity: RepoCheck

| Field | Type | Description |
|-------|------|-------------|
| status | `"ok"` \| `"notSet"` \| `"missingFolder"` \| `"missingStackFiles"` \| `"missingProjectFiles"` \| `"uvNotFound"` \| `"uvEnvBroken"` \| `"modelsUnsupported"` | primeiro problema encontrado (research, Decision 4). `modelsUnsupported` (FR-016A): `artigo2md models status --json` saiu com 2 (subcomando ausente ou uso inválido), ou o evento não traz `schema_version` ou a versão é desconhecida (`contracts/models-cli.md`); é detectado na verificação dos modelos, não na inspeção de arquivos. A ausência de `--management-panel` no `up.sh` não gera estado: o app nunca usa essa opção |
| uvPath | string \| null | executável resolvido |
| missing | string[] | arquivos ausentes (caminhos relativos ao repositório) |

## Entity: ServiceStatus

| Field | Type | Description |
|-------|------|-------------|
| service | `"postgres"` \| `"rabbitmq"` \| `"api"` | serviço monitorado |
| state | `"stopped"` \| `"starting"` \| `"healthy"` \| `"unhealthy"` | FR-026; textos "parado", "iniciando", "saudável", "com problema" |
| address | string | `127.0.0.1:<porta do .env>` |
| checkedAt | string (ISO 8601) | última verificação |

`consumerConnected` fica só em `InfraSnapshot` (abaixo); o endereço do painel de gestão não existe no
modelo, porque o app não publica o painel (FR-029, dec-025). Mapeamento de
runtime e pool para os quatro estados e regra do indicador resumido: research, Decision 10.

### State transitions (por serviço)

```
stopped --(Iniciar infraestrutura)--> starting --(sondagem ok)--> healthy
starting --(up.sh termina != 0)--> unhealthy | stopped
healthy --(sondagem falha)--> unhealthy --(sondagem ok)--> healthy
healthy|unhealthy --(Parar infraestrutura ok)--> stopped
```

## Entity: WorkerPool

| Field | Type | Description |
|-------|------|-------------|
| state | `"stopped"` \| `"starting"` \| `"running"` \| `"stopping"` \| `"crashed"` \| `"orphanFound"` | estado do pool |
| workers | integer | número em uso na execução corrente |
| minFreeMemoryMb | integer | idem |
| exitCode | integer \| null | último código de saída, em `crashed` |
| reason | WorkerExitReason \| null | código traduzido (ver `contracts/external-processes.md`), mais `killed` (término por sinal) e `unknown` (pool adotado que sumiu) |
| lines | string[] | últimas mensagens (máximo 200, credenciais mascaradas) |
| memoryWarning | boolean | o worker emitiu o aviso de pool acima da RAM |
| adopted | boolean | pool de sessão anterior adotado (sem mensagens) |

Pré-condições de `stopped|crashed → starting` (FR-018, US3-2): stack no ar (RabbitMQ em execução e
saudável) **e** etapa `models` de `FirstRunStep` em `done`; sem a segunda, o comando é recusado com
`modelsMissing` e nenhum processo é iniciado (a conversão pedida por quem consome, `desktop-processing`,
vê o mesmo estado pendente; US6-9). Ao fechar o app: parada calma até o teto de 60 s (FR-024, plan M21);
se o teto for atingido, o app sai, registra os PIDs e a próxima abertura entra em `orphanFound`.

### State transitions

```
stopped --(Iniciar workers, RabbitMQ saudável)--> starting --(processo vivo)--> running
running --(Parar workers / fechar app)--> stopping --(grupo vazio)--> stopped
running --(sai sozinho)--> crashed --(Reiniciar)--> starting
[abertura do app com registro válido] --> orphanFound --(Parar)--> stopping
orphanFound --(Continuar usando)--> running (adopted = true)
```

## Entity: WorkerPoolRecord (arquivo `worker-pool.json`)

Na pasta de dados do app do Tauri (`app_data_dir`); gravado ao iniciar o pool e apagado quando ele
termina (research, Decision 12).

| Field | Type | Description |
|-------|------|-------------|
| schemaVersion | integer | = 1 |
| pid | integer | PID do processo `uv` iniciado |
| pgid | integer | grupo de processos (= pid, por `process_group(0)`) |
| startedAt | integer | início do processo em segundos Unix, de `proc_pidinfo` |
| repoPath | string | repositório usado |

## Entity: FirstRunStep

| Field | Type | Description |
|-------|------|-------------|
| step | `"images"` \| `"models"` | "imagens da infraestrutura", "modelos de conversão" |
| state | `"pending"` \| `"downloading"` \| `"done"` \| `"failed"` | FR-030 |
| downloadedBytes | integer \| null | volume baixado, quando conhecido |
| totalBytes | integer \| null | total, quando conhecido |
| itemsDone / itemsTotal | integer | imagens ou modelos concluídos |
| updatedAt | string (ISO 8601) \| null | hora da última atualização de progresso recebida; `null` antes do primeiro evento. Com `totalBytes` nulo é o sinal de atividade de FR-032 |
| failure | DownloadFailure \| null | `"noNetwork"`, `"diskFull"`, `"interrupted"`, `"other"` |
| approxSize | string | texto com fonte: imagens "cerca de 1,5 GB" (medição, research Decision 8); modelos "cerca de 3 GB livres" (`README.md:34`) |
| location | string | onde os arquivos ficam: "disco do runtime Docker" ou o cache do Hugging Face |

**Atividade com `totalBytes` nulo (FR-032, US6-3, ux CHK006)**: a interface mostra `downloadedBytes`
em unidade legível, `itemsDone/itemsTotal` e `updatedAt`. Teste de atividade (usado em 10.3 e no núcleo
Rust): com um download simulado em curso e `totalBytes` nulo, duas leituras do retrato separadas por
até 5 s diferem em `downloadedBytes` ou `updatedAt`; sem mudança por mais de 5 s, a interface mostra o
estado "sem resposta" em vez de um valor parado.

O estado não é persistido: é recalculado a cada verificação (`docker image inspect` para imagens;
`artigo2md models status --json` para modelos). Um download interrompido aparece como `pending`
ou `failed`, nunca `done` (FR-030, FR-034).

## Entity: Operation

| Field | Type | Description |
|-------|------|-------------|
| kind | `"startStack"` \| `"stopStack"` \| `"startWorkers"` \| `"stopWorkers"` \| `"prepareImages"` \| `"prepareModels"` | operação de ciclo de vida |
| stage | `"checkingRepo"` \| `"checkingDataDir"` \| `"ensuringParams"` \| `"validatingParams"` \| `"checkingImages"` \| `"pullingImages"` \| `"buildingApi"` \| `"runningUp"` \| `"runningDown"` \| `"startingWorkers"` \| `"stoppingWorkers"` \| `"forcingStop"` \| `"checkingDisk"` \| `"downloadingModels"` | etapa atual, traduzida no frontend |
| startedAt | string (ISO 8601) | início |
| result | OperationResult \| null | ao terminar: `ok` ou um código de erro de `contracts/tauri-commands.md` |
| technicalLog | string[] | saída técnica (máximo 500 linhas, mascarada), mostrada só sob demanda; fica no núcleo e não vai no `InfraSnapshot` (a interface a pede por `get_operation_log`) |

Regra: no máximo uma `Operation` de ciclo de vida ativa por instância (Mutex) e por máquina (`flock`
em `lifecycle.lock`); `prepareModels` tem exclusão própria e pode correr junto de uma operação de
stack (research, Decision 14). `InfraSnapshot.operation` passa a ser uma lista de 0 a 2 operações.

## Entity: InfraSnapshot (retrato devolvido à interface)

| Field | Type |
|-------|------|
| readOnly | boolean (outra instância detém `lifecycle.lock`) |
| runtime | RuntimeReport |
| repo | RepoCheck |
| dataDir | DataDirCheck (`path`, `status`: `"ok"` \| `"missing"` (a criação automática falhou) \| `"notDirectory"` \| `"notWritable"` \| `"notCanonical"` \| `"forbidden"` (pasta ampla demais, `plan.md` M14), `canonicalPath`) |
| services | ServiceStatus[3] |
| pool | WorkerPool |
| firstRun | FirstRunStep[2] |
| operations | Operation[] (0 a 2) |
| summary | `"healthy"` \| `"partial"` \| `"stopped"` (indicador da barra lateral, FR-025; regra em research, Decision 10) |
| consumerConnected | boolean \| null (`GET /v1/status` → `consumer.connected`, `src/artigo2md/api/routes/status.py:17-26`; `null` com a API fora do ar) |
