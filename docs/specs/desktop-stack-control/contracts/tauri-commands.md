# Contracts: comandos Tauri de desktop-stack-control

**[PROPOSTA — a validar na implementação]** — todos os comandos abaixo são novos. Seguem a convenção
do desktop-shell (`docs/specs/desktop-shell/contracts/commands.md`; `docs/specs/desktop-shell/plan.md:127-139`):
nome do comando em snake_case, payload em camelCase via serde, `async` com trabalho bloqueante em
`spawn_blocking`, erro serializado como `{code, detail: null}` com o detalhe só no registro do app
(`desktop/src-tauri/src/error.rs:1-2,148-156`, `impl Serialize for AppError`). A interface chama tudo por `desktop/src/lib/api.ts`,
o único ponto de `invoke()` (`desktop/src/lib/api.ts:1-5,41-47`, `call<T>`). Tipos em `data-model.md`.

Nenhum comando recebe nem devolve credencial. Nenhum comando abre conexão para fora de `127.0.0.1`.

## get_infra_snapshot

**Request**: sem argumentos. **Response**: `InfraSnapshot`. Só lê o último retrato produzido pelo
temporizador do núcleo (sondagens a cada 2 s, research Decision 10); nunca executa verificação nem
pega o Mutex de operação. Chamado pela interface a cada 1 s com operação/download em andamento e a
cada 3 s fora disso.

## refresh_infra

**Request**: sem argumentos. **Response**: `InfraSnapshot` após nova verificação completa de runtime,
repositório, pasta de dados, serviços e etapas da primeira execução ("Verificar novamente", FR-003).

## start_stack / stop_stack

**Request**: sem argumentos. **Response (ok)**: `{ "alreadyInState": boolean }` — `true` quando a stack
já estava no ar (start) ou parada (stop) (FR-009). A operação roda em segundo plano e o progresso
aparece em `InfraSnapshot.operation`; o comando retorna ao terminar.

`start_stack` só executa com `runtime.overall == "ready"`, `repo.status == "ok"` e
`dataDir.status == "ok"`. Ordem: garante o `.env` (cria se ausente, research Decisions 5 e 8) → valida
o `.env` com as regras do `up.sh` (`invalidParams` com o nome da variável) → confere imagens (sem as
três, `imagesMissing`, sem chamar o `up.sh`) → `up.sh`.

## start_workers / stop_workers

**Request**: sem argumentos (usa `StackSettings`). **Response (ok)**: `{ "alreadyInState": boolean }`.
`start_workers` exige RabbitMQ em execução no Compose e `healthy` (`plan.md` M2) e a etapa `models`
em `done` (`modelsMissing`); é aceito nos estados `stopped` e `crashed` ("Reiniciar", FR-022).
`stop_workers` faz a parada calma de até 45 s (research, Decision 12).

## resolve_previous_pool

**Request**: `{ "action": "stop" | "keep" }`. **Response (ok)**: `WorkerPool`. Válido só com
`pool.state == "orphanFound"`.
`action` chega como texto e é conferido à mão (valor desconhecido: `invalidParams`); sem pool órfão pendente
o comando recusa com `operationInProgress`. Só a instância com o `lifecycle.lock` o executa
(`anotherInstance` na outra) e o app reconfere o registro (M8) imediatamente antes de qualquer sinal.

## prepare_images / prepare_models

**Request**: `{ "confirmed": boolean }`, desserializado como `serde_json::Value` e conferido à mão:
campo ausente, não booleano ou `false` → `confirmationRequired` (FR-031). O controle real é o diálogo
nativo do Rust (`plan.md` M16); `confirmed` é só UX. `prepare_images` garante o `.env` antes do
Compose. **Response (ok)**: `FirstRunStep` final. Antes do primeiro byte, checa espaço em disco
(FR-033). Progresso em `InfraSnapshot.firstRun`. Saídas de `artigo2md models download`
(`contracts/models-cli.md`) → `FirstRunStep.failure`: 1 → `reason` do evento `result`; 7 →
`insufficientDisk`; 2 e 6 → `other`; término sem `result` → `interrupted`.

## get_stack_settings / save_stack_settings

**get Response**: `{ "settings": StackSettings, "ports": { "postgres": int, "rabbitmq": int,
"api": int } | null }` (sem a porta do painel de gestão, que o app não publica; `plan.md` dec-025) — `ports` vem do `.env`; `null` se ele não existe
(mostra os padrões de `stack/.env.example:25-26,30`).

**save Request**: `{ "settings": StackSettings, "ports": {...} | null }`. `repoPath` e `dataDir` têm
de ser iguais aos valores gravados (só mudam por `pick_repo_folder`/`pick_data_dir`; `plan.md` M1),
senão `invalidStackSettings`. Valida tudo antes de gravar;
grava `stack-settings.json` e, se `ports` ou `dataDir` mudaram e o `.env` existe, reescreve só as
quatro chaves editáveis. Recusado durante operação em andamento.

## pick_repo_folder / pick_data_dir

**Request**: sem argumentos. Abre o seletor nativo de pasta pelo Rust (`tauri-plugin-dialog`, como
`open_folder`, `desktop/src-tauri/src/commands.rs:112`). **Response**: `StackSettings` com o novo
caminho canônico gravado, ou o valor anterior se a pessoa cancelar. Únicos caminhos para mudar
`repoPath` e `dataDir` (`plan.md` M1, M3, M14). Recusado durante operação em andamento.

## save_guide_progress

**Request**: `{ "guide": GuideProgress | null }`. **Response**: `StackSettings`.

## get_operation_log

**Request**: sem argumentos. **Response**: `{ "lines": string[] }` — saída técnica mascarada da última
operação (FR-014: disponível sob demanda, nunca a mensagem principal).

## set_guide_open

**Request**: `{ "open": boolean }`. **Response**: vazio. Avisa o núcleo que o guia de instalação abriu ou
fechou, para o runtime ser verificado a cada 5 s enquanto ele está aberto (FR-003, FR-006; research,
Decision 10). Não executa verificação nem pega o Mutex de operação. Acrescentado na implementação de 10.4.4:
a tarefa pedia o diagnóstico a cada 5 s e o núcleo (`InfraState::set_guide_open`) não tinha comando que o
acionasse.

## get_macos_version

**Request**: sem argumentos. **Response**: `string | null` — `ProductVersion` de
`/System/Library/CoreServices/SystemVersion.plist` (formato: `<maior>.<menor>[.<correção>]`; "14.5.1" é só o valor de exemplo da fixture do teste
`product_version_is_read_from_the_plist_text`, `desktop/src-tauri/src/commands.rs:277`, e não foi lido de uma
máquina; observado em 2026-11-27 nesta máquina: `plutil -extract ProductVersion raw
/System/Library/CoreServices/SystemVersion.plist` → `27.2.1`), ou `null` se o arquivo não pôde ser lido ou o valor não é só dígitos e pontos. Só alimenta o guia
(research, Decision 15: "o guia lê a versão do sistema"): com `null` nenhum runtime é marcado como
indisponível. Acrescentado na implementação de 10.4.3 porque nenhum comando anterior expunha a versão.

## Error Responses

Códigos novos, camelCase, somados a `ErrorCode` (`desktop/src-tauri/src/error.rs:15-47`) e a
`ERROR_CODES` (`desktop/src/lib/types.ts:4-35`); o texto em português fica em
`desktop/src/lib/messages.ts`.

| Code | Quando |
|------|--------|
| operationInProgress | outra operação de ciclo de vida em andamento (FR-015) |
| anotherInstance | outra instância detém `lifecycle.lock` (FR-015) |
| dockerNotReady | `runtime.overall != "ready"` |
| repoInvalid | `repo.status != "ok"` (detalhe no `RepoCheck`) |
| dataDirInvalid | pasta de dados ausente, sem escrita ou não canônica |
| imagesMissing | imagens ausentes sem a etapa `images` concluída |
| confirmationRequired | `confirmed` ausente ou falso |
| insufficientDisk | espaço livre menor que o necessário (a interface mostra quanto falta) |
| invalidParams | `up.sh` saiu com 2 (`stack/up.sh:13-14`) |
| dockerUnavailable | `up.sh`/`down.sh` saíram com 3 (`stack/up.sh:13-14`; `stack/down.sh:14`) |
| portInUse | `up.sh` saiu com 4; a interface cita serviço e porta |
| serviceUnhealthy | `up.sh` saiu com 5; a interface cita o serviço |
| stopFailed | `down.sh` saiu com 1 (`stack/down.sh:76,82`) |
| scriptFailed | `up.sh` saiu com 1 (falha no `cd`, `stack/up.sh:18`) ou com código não documentado; `down.sh` saiu com 2 (opções inválidas, que o app não passa) ou com qualquer código não documentado |
| modelsMissing | iniciar workers com a etapa `models` diferente de `done` (FR-036) |
| stackNotRunning | iniciar workers sem RabbitMQ saudável (FR-018) |
| workerExited | o pool terminou sozinho; o motivo vem em `WorkerPool.reason` |
| downloadFailed | download falhou; o motivo vem em `FirstRunStep.failure` |
| invalidStackSettings | valor fora das regras de `data-model.md` |

**Mapeamento de saída do `down.sh`** (completo; api CHK003, FR-014): 0 → ok; 1 → `stopFailed`; 3 →
`dockerUnavailable`; 2 e qualquer código não documentado → `scriptFailed` (com a saída técnica mascarada
disponível sob demanda). Mensagens principais em português a implementar em 9.1.3 para esses dois códigos:
`stopFailed` = "Não foi possível parar a infraestrutura. Veja os detalhes técnicos e tente de novo.";
`scriptFailed` = "O script de iniciar ou parar a infraestrutura terminou de forma inesperada. Veja os
detalhes técnicos; se o problema continuar, confira se a pasta do repositório está íntegra e atualizada.".

**Incompatibilidade de versão do repositório** (FR-016A, api CHK011): o `models status --json` com saída 2
(subcomando ausente ou uso inválido), evento sem `schema_version` ou com versão desconhecida define
`RepoCheck.status = "modelsUnsupported"` e, portanto, `repoInvalid` nas operações que dependem de `models`
(`prepare_models`, `start_workers`); nenhuma operação é executada e a mensagem em português é a de FR-016A.
