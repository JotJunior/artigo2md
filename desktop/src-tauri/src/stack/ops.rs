//! Operações de ciclo de vida expostas como comandos (tarefas 8.4 e 8.5; contracts/tauri-commands.md;
//! plan.md M2, M16, M21; research Decision 12).
//!
//! Os comandos Tauri (`commands.rs`) só criam o `Confirmer` nativo e chamam estas funções, que ficam
//! testáveis sem janela. Toda operação: reserva a exclusão da instância (`InstanceGate`, uma por vez e
//! só com o `lifecycle.lock`), aparece em `InfraSnapshot.operations` e registra os grupos de
//! processos que inicia (para fechar o app sem deixar nada rodando).

use std::path::Path;
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::error::{AppError, AppResult, ErrorCode};
use crate::stack::config::{self, StackContext};
use crate::stack::data_dir::{self, DataDirCheck};
use crate::stack::env_file::{self, KEY_RABBITMQ_PASS, KEY_RABBITMQ_PORT, KEY_RABBITMQ_USER};
use crate::stack::exec;
use crate::stack::first_run::{self, Confirmer, DownloadFailure, FirstRunStep, PrepareError};
use crate::stack::health;
use crate::stack::images;
use crate::stack::instance_lock::OperationKind;
use crate::stack::lifecycle::{self, LifecycleError, LifecycleOutcome, OperationStage, Reporter};
use crate::stack::models::{self, ModelsCheck, ModelsEnv};
use crate::stack::pool::{DetectOutcome, StartParams, WorkerPool};
use crate::stack::repo::{self, RepoCheck};
use crate::stack::runtime::{self, DetectEnv, DockerEnv, RuntimeReport};
use crate::stack::secret::Masked;
use crate::stack::socket_owner::{self, SocketOwner};
use crate::stack::stack_settings::StackSettings;
use crate::stack::{InfraState, OperationResult};

/// Teto do fechamento do app (SC-005; plan.md M21).
pub const CLOSE_CEILING: Duration = Duration::from_secs(60);
/// Espera de uma operação em andamento pelo `SIGTERM` antes do `SIGKILL` (Decision 12).
pub const OPERATION_TERM_GRACE: Duration = Duration::from_secs(10);

/// `confirmed` chega como `serde_json::Value` e é conferido à mão (FR-031): campo ausente, não
/// booleano ou `false` é `confirmationRequired`. É só UX: o controle real é o diálogo nativo do Rust
/// (`Confirmer`, plan.md M16), que roda MESMO com `confirmed: true`.
pub fn require_confirmed(confirmed: Option<&Value>) -> AppResult<()> {
    match confirmed {
        Some(Value::Bool(true)) => Ok(()),
        _ => Err(AppError::new(
            ErrorCode::ConfirmationRequired,
            "a confirmação da pessoa é necessária",
        )),
    }
}

/// Falha de uma operação: o erro do IPC e o que o retrato mostra além do código.
struct OpFailure {
    error: AppError,
    service: Option<String>,
    port: Option<u16>,
    failure: Option<DownloadFailure>,
    shortfall_bytes: Option<u64>,
}

impl From<AppError> for OpFailure {
    fn from(error: AppError) -> Self {
        Self {
            error,
            service: None,
            port: None,
            failure: None,
            shortfall_bytes: None,
        }
    }
}

impl From<LifecycleError> for OpFailure {
    fn from(e: LifecycleError) -> Self {
        Self {
            error: e.error,
            service: e.service,
            port: e.port,
            failure: None,
            shortfall_bytes: None,
        }
    }
}

impl From<PrepareError> for OpFailure {
    fn from(e: PrepareError) -> Self {
        Self {
            error: e.error,
            service: None,
            port: None,
            failure: e.failure,
            shortfall_bytes: e.shortfall_bytes,
        }
    }
}

impl OpFailure {
    fn result(&self) -> OperationResult {
        OperationResult {
            ok: false,
            code: Some(self.error.code),
            service: self.service.clone(),
            port: self.port,
            failure: self.failure,
            shortfall_bytes: self.shortfall_bytes,
            already_in_state: None,
        }
    }
}

/// Executa uma operação: reserva a exclusão, a mostra no retrato, instala o registro de grupos de
/// processos e grava o resultado. `body` devolve o valor e `alreadyInState`.
fn run_op<T>(
    ctx: &StackContext,
    infra: &InfraState,
    kind: OperationKind,
    first_stage: OperationStage,
    body: impl FnOnce(&mut dyn Reporter) -> Result<(T, bool), OpFailure>,
) -> AppResult<T> {
    let _guard = ctx.gate.begin(kind)?;
    infra.begin_operation(kind, first_stage);
    let mut tracker = infra.tracker(kind);
    let outcome = exec::with_registry(&infra.groups, || body(&mut tracker));
    match outcome {
        Ok((value, already)) => {
            infra.finish_operation(kind, OperationResult::ok(already));
            Ok(value)
        }
        Err(failure) => {
            infra.finish_operation(kind, failure.result());
            Err(failure.error)
        }
    }
}

/// Verificações frescas feitas dentro da operação (não confia no último retrato).
struct Fresh {
    settings: StackSettings,
    runtime: RuntimeReport,
    repo_check: RepoCheck,
    data_dir: DataDirCheck,
    docker: Option<DockerEnv>,
}

fn fresh(ctx: &StackContext) -> Fresh {
    // `get_stack_settings` também espelha `ARTIGO2MD_DATA_DIR` do `.env` (Decision 5).
    let settings = config::get_stack_settings(ctx)
        .map(|r| r.settings)
        .unwrap_or_else(|_| ctx.settings.load().settings);
    let located = ctx.located();
    let repo_path = settings.repo_path.as_deref().map(Path::new);
    let runtime = runtime::detect(&DetectEnv::from_located(located, &ctx.home));
    let repo_check = repo::check(repo_path, located);
    let data_dir = data_dir::check(Path::new(&settings.data_dir), &ctx.home, repo_path, false);
    let docker = match (repo_path, repo_check.is_ok()) {
        (Some(repo), true) => DockerEnv::from_report(&runtime, located, repo),
        _ => None,
    };
    Fresh {
        settings,
        runtime,
        repo_check,
        data_dir,
        docker,
    }
}

fn docker_not_ready() -> AppError {
    AppError::new(ErrorCode::DockerNotReady, "o Docker não está pronto")
}

// ---------------------------------------------------------------------------------------------
// Stack e etapas da primeira execução
// ---------------------------------------------------------------------------------------------

/// `start_stack` (contracts/tauri-commands.md).
pub fn start_stack(ctx: &StackContext, infra: &InfraState) -> AppResult<LifecycleOutcome> {
    run_op(
        ctx,
        infra,
        OperationKind::StartStack,
        OperationStage::CheckingRepo,
        |reporter| {
            let f = fresh(ctx);
            lifecycle::check_start_preconditions(&f.runtime, &f.repo_check, &f.data_dir)?;
            let env = f.docker.as_ref().ok_or_else(docker_not_ready)?;
            let outcome = lifecycle::start_stack(env, &f.settings.data_dir, reporter)?;
            Ok((outcome, outcome.already_in_state))
        },
    )
}

/// `stop_stack`.
pub fn stop_stack(ctx: &StackContext, infra: &InfraState) -> AppResult<LifecycleOutcome> {
    run_op(
        ctx,
        infra,
        OperationKind::StopStack,
        OperationStage::CheckingRepo,
        |reporter| {
            let f = fresh(ctx);
            lifecycle::check_stop_preconditions(&f.runtime, &f.repo_check)?;
            let env = f.docker.as_ref().ok_or_else(docker_not_ready)?;
            let outcome = lifecycle::stop_stack(env, reporter)?;
            Ok((outcome, outcome.already_in_state))
        },
    )
}

/// `prepare_images`: o diálogo nativo (`confirmer`) é o controle real; `confirmed` já foi conferido
/// por `require_confirmed` no comando.
pub fn prepare_images(
    ctx: &StackContext,
    infra: &InfraState,
    confirmer: &dyn Confirmer,
) -> AppResult<FirstRunStep> {
    run_op(
        ctx,
        infra,
        OperationKind::PrepareImages,
        OperationStage::CheckingRepo,
        |reporter| {
            let f = fresh(ctx);
            lifecycle::check_start_preconditions(&f.runtime, &f.repo_check, &f.data_dir)?;
            let env = f.docker.as_ref().ok_or_else(docker_not_ready)?;
            let result = images::prepare_images(
                env,
                &f.settings.data_dir,
                &|| first_run::free_bytes(&ctx.home),
                confirmer,
                reporter,
            );
            finish_step(infra, result)
        },
    )
}

/// `prepare_models`.
pub fn prepare_models(
    ctx: &StackContext,
    infra: &InfraState,
    confirmer: &dyn Confirmer,
) -> AppResult<FirstRunStep> {
    run_op(
        ctx,
        infra,
        OperationKind::PrepareModels,
        OperationStage::CheckingRepo,
        |reporter| {
            let f = fresh(ctx);
            if !f.repo_check.is_ok() {
                return Err(AppError::new(
                    ErrorCode::RepoInvalid,
                    "a pasta do repositório não está pronta",
                )
                .into());
            }
            let repo = f.settings.repo_path.as_deref().map(Path::new);
            let env = repo
                .and_then(|r| ModelsEnv::from_located(ctx.located(), r))
                .ok_or_else(|| {
                    AppError::new(ErrorCode::RepoInvalid, "uv ou repositório ausente")
                })?;
            // Subcomando ausente ou de outra versão: `modelsUnsupported` (FR-016A) antes do diálogo.
            if models::check_models(&env) == ModelsCheck::Unsupported {
                return Err(AppError::new(
                    ErrorCode::RepoInvalid,
                    "o repositório não oferece o subcomando models esperado (modelsUnsupported)",
                )
                .into());
            }
            let result = models::prepare_models(&env, confirmer, reporter);
            finish_step(infra, result)
        },
    )
}

/// Guarda a etapa final (ou a etapa com falha) no retrato e converte o erro.
fn finish_step(
    infra: &InfraState,
    result: Result<FirstRunStep, PrepareError>,
) -> Result<(FirstRunStep, bool), OpFailure> {
    match result {
        Ok(step) => {
            infra.set_step(&step);
            Ok((step, false))
        }
        Err(e) => {
            if let Some(step) = &e.step {
                infra.set_step(step);
            }
            Err(e.into())
        }
    }
}

// ---------------------------------------------------------------------------------------------
// Pool de workers
// ---------------------------------------------------------------------------------------------

/// As três conferências de `start_workers` que dependem do mundo externo, como funções: a ordem e a
/// recusa ficam testáveis sem Docker, `uv` nem rede.
pub struct WorkerChecks<'a> {
    /// `compose ps --status running --services` lista `rabbitmq` (M2).
    pub rabbitmq_running: &'a dyn Fn() -> AppResult<bool>,
    /// A sondagem AMQP em `127.0.0.1` passa (M2).
    pub amqp_healthy: &'a dyn Fn() -> bool,
    /// `models status --json`.
    pub models: &'a dyn Fn() -> ModelsCheck,
    /// Dono do socket em escuta na porta do RabbitMQ (M2, tarefa 11.5).
    pub socket_owner: &'a dyn Fn() -> SocketOwner,
}

/// Pré-condições de `start_workers` (FR-018; plan.md M2): stack no ar (listada pelo Compose E
/// sondagem AMQP), modelos prontos e, de novo, a conferência da stack imediatamente antes de iniciar
/// (a checagem de modelos pode demorar).
pub fn check_worker_preconditions(
    checks: &WorkerChecks<'_>,
    reporter: &mut dyn Reporter,
) -> AppResult<()> {
    let stack_up = || -> AppResult<()> {
        let up = (checks.rabbitmq_running)()? && (checks.amqp_healthy)();
        if up {
            Ok(())
        } else {
            Err(AppError::new(
                ErrorCode::StackNotRunning,
                "o RabbitMQ não está em execução e saudável",
            ))
        }
    };
    stack_up()?;
    match (checks.models)() {
        ModelsCheck::Checked(status) if status.all_present() => {}
        ModelsCheck::Unsupported => {
            return Err(AppError::new(
                ErrorCode::RepoInvalid,
                "o repositório não oferece o subcomando models esperado (modelsUnsupported)",
            ))
        }
        _ => {
            return Err(AppError::new(
                ErrorCode::ModelsMissing,
                "os modelos de conversão ainda não foram preparados",
            ))
        }
    }
    stack_up()?;
    // M2: o dono do socket em escuta, por último e imediatamente antes de qualquer credencial sair.
    match (checks.socket_owner)() {
        SocketOwner::Confirmed => reporter.line(Masked::non_secret(
            "dono do socket da porta do RabbitMQ conferido: processo do runtime Docker",
        )),
        SocketOwner::Divergent(command) => {
            return Err(AppError::new(
                ErrorCode::StackNotRunning,
                format!("outro processo ({command}) escuta na porta do RabbitMQ"),
            ))
        }
        SocketOwner::Indeterminate(reason) => reporter.line(Masked::non_secret(&format!(
            "dono do socket da porta do RabbitMQ não pôde ser conferido: {reason}"
        ))),
    }
    Ok(())
}

/// `start_workers`. `alreadyInState` quando o pool já está em execução (FR-021).
pub fn start_workers(ctx: &StackContext, infra: &InfraState) -> AppResult<LifecycleOutcome> {
    run_op(
        ctx,
        infra,
        OperationKind::StartWorkers,
        OperationStage::StartingWorkers,
        |reporter| {
            if infra.pool.is_active() {
                return Ok((
                    LifecycleOutcome {
                        already_in_state: true,
                    },
                    true,
                ));
            }
            let f = fresh(ctx);
            lifecycle::check_stop_preconditions(&f.runtime, &f.repo_check)?;
            if !f.data_dir.is_ok() {
                return Err(AppError::new(
                    ErrorCode::DataDirInvalid,
                    "a pasta de dados não está pronta",
                )
                .into());
            }
            let docker = f.docker.as_ref().ok_or_else(docker_not_ready)?;
            let in_use = f.runtime.in_use;
            let repo = docker.repo.clone();
            let located = ctx.located();
            let uv = located
                .uv
                .clone()
                .ok_or_else(|| AppError::new(ErrorCode::RepoInvalid, "uv não encontrado"))?;
            let models_env = ModelsEnv::from_located(located, &repo)
                .ok_or_else(|| AppError::new(ErrorCode::RepoInvalid, "uv não encontrado"))?;

            let env_file = env_file::load(&env_file::env_path(&repo))?.ok_or_else(|| {
                AppError::new(ErrorCode::StackNotRunning, "stack/.env ainda não existe")
            })?;
            let port = env_file.port(KEY_RABBITMQ_PORT).ok_or_else(|| {
                AppError::new(ErrorCode::InvalidParams, KEY_RABBITMQ_PORT.to_string())
            })?;
            let user = env_file
                .plain(KEY_RABBITMQ_USER)
                .filter(|u| !u.is_empty())
                .map(str::to_string)
                .ok_or_else(|| {
                    AppError::new(ErrorCode::InvalidParams, KEY_RABBITMQ_USER.to_string())
                })?;
            let pass = env_file
                .secret(KEY_RABBITMQ_PASS)
                .filter(|p| !p.is_empty())
                .cloned()
                .ok_or_else(|| {
                    AppError::new(ErrorCode::InvalidParams, KEY_RABBITMQ_PASS.to_string())
                })?;

            check_worker_preconditions(
                &WorkerChecks {
                    rabbitmq_running: &|| {
                        lifecycle::running_services(docker)
                            .map(|s| s.iter().any(|name| name == "rabbitmq"))
                    },
                    amqp_healthy: &|| health::probe_rabbitmq(port),
                    models: &|| models::check_models(&models_env),
                    socket_owner: &|| socket_owner::probe(port, in_use),
                },
                reporter,
            )?;

            let already = infra.pool.start(StartParams {
                uv,
                repo,
                path_dirs: located.path_dirs(),
                data_dir: f.settings.data_dir.clone().into(),
                workers: f.settings.workers,
                min_free_memory_mb: f.settings.min_free_memory_mb,
                rabbitmq_port: port,
                rabbitmq_user: user,
                rabbitmq_pass: pass,
            })?;
            Ok((
                LifecycleOutcome {
                    already_in_state: already,
                },
                already,
            ))
        },
    )
}

/// `stop_workers`: parada calma de até 45 s (`SIGTERM` ao `uv`, depois `SIGKILL` ao grupo).
pub fn stop_workers(ctx: &StackContext, infra: &InfraState) -> AppResult<LifecycleOutcome> {
    run_op(
        ctx,
        infra,
        OperationKind::StopWorkers,
        OperationStage::StoppingWorkers,
        |reporter| {
            let already = {
                let mut on_force = || reporter.stage(OperationStage::ForcingStop);
                infra.pool.stop(&mut on_force)?
            };
            Ok((
                LifecycleOutcome {
                    already_in_state: already,
                },
                already,
            ))
        },
    )
}

/// Ação de `resolve_previous_pool`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PreviousPoolAction {
    Stop,
    Keep,
}

impl PreviousPoolAction {
    pub fn parse(text: &str) -> AppResult<Self> {
        match text {
            "stop" => Ok(Self::Stop),
            "keep" => Ok(Self::Keep),
            _ => Err(AppError::new(
                ErrorCode::InvalidParams,
                "ação desconhecida para o pool anterior",
            )),
        }
    }
}

/// `resolve_previous_pool`: válido só com `pool.state == "orphanFound"`; só a instância com o lock
/// chega aqui (a exclusão da operação recusa a segunda instância com `anotherInstance`).
pub fn resolve_previous_pool(
    ctx: &StackContext,
    infra: &InfraState,
    action: PreviousPoolAction,
) -> AppResult<WorkerPool> {
    run_op(
        ctx,
        infra,
        OperationKind::StopWorkers,
        OperationStage::StoppingWorkers,
        |reporter| {
            let pool = match action {
                PreviousPoolAction::Keep => infra.pool.keep_previous()?,
                PreviousPoolAction::Stop => {
                    let mut on_force = || reporter.stage(OperationStage::ForcingStop);
                    infra.pool.stop_previous(&mut on_force)?
                }
            };
            Ok((pool, false))
        },
    )
}

/// Ao abrir o app: procura o pool deixado pela sessão anterior (FR-021). Bloqueia (resolver o `uv`
/// pode levar segundos): chame fora da thread de interface. Só a instância com o lock o faz.
pub fn detect_previous_pool(ctx: &StackContext, infra: &InfraState) -> DetectOutcome {
    if ctx.gate.read_only() {
        return DetectOutcome::Nothing;
    }
    let settings = ctx.settings.load().settings;
    let uv = ctx.located().uv.clone();
    infra
        .pool
        .detect_previous(settings.repo_path.as_deref(), uv.as_deref())
}

// ---------------------------------------------------------------------------------------------
// Fechamento do app (FR-024; plan.md M21)
// ---------------------------------------------------------------------------------------------

/// O fechamento precisa esperar por algo? Há pool nosso ou operação em andamento. Com a stack no ar
/// e o pool parado, não há nada a fazer e o app sai direto.
pub fn close_work_pending(ctx: &StackContext, infra: &InfraState) -> bool {
    !ctx.gate.read_only()
        && (infra.pool.is_active()
            || ctx.gate.stack_operation_active()
            || ctx.gate.models_operation_active()
            || !infra.groups.is_empty())
}

/// O que sobrou quando o fechamento terminou.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CloseReport {
    /// O teto estourou antes de tudo terminar.
    pub timed_out: bool,
    /// PIDs (líder de grupo) que continuam vivos ao sair; ficam em `worker-pool.json` para a próxima
    /// abertura aplicar FR-021.
    pub remaining: Vec<i32>,
}

/// Para o pool com a parada calma e encerra as operações em andamento (`SIGTERM` no grupo, espera de
/// até `op_grace` e `SIGKILL`), tudo no máximo até `ceiling`. NUNCA derruba a stack.
pub fn shutdown_for_close(
    infra: &InfraState,
    ceiling: Duration,
    op_grace: Duration,
) -> CloseReport {
    let deadline = Instant::now() + ceiling;
    let (tx, rx) = mpsc::channel::<&'static str>();
    let mut waiting = 0;

    if infra.pool.is_active() {
        let pool = infra.pool.clone();
        let tx = tx.clone();
        waiting += 1;
        thread::spawn(move || {
            // Erro (por exemplo, pool ainda iniciando) não impede o fechamento: o registro fica.
            let _ = pool.stop(&mut || {});
            let _ = tx.send("pool");
        });
    }
    if !infra.groups.is_empty() {
        let groups = std::sync::Arc::clone(&infra.groups);
        let tx = tx.clone();
        waiting += 1;
        thread::spawn(move || {
            groups.terminate_all(op_grace);
            let _ = tx.send("operations");
        });
    }
    drop(tx);

    let mut timed_out = false;
    while waiting > 0 {
        let left = deadline.saturating_duration_since(Instant::now());
        match rx.recv_timeout(left) {
            Ok(_) => waiting -= 1,
            Err(_) => {
                timed_out = true;
                break;
            }
        }
    }

    let mut remaining = Vec::new();
    if timed_out {
        if infra.pool.is_active() {
            if let Some((pid, _)) = infra.pool.live_ids() {
                remaining.push(pid);
            }
        }
        remaining.extend(infra.groups.ids());
    }
    CloseReport {
        timed_out,
        remaining,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stack::first_run::{StepKind, StepState};
    use crate::stack::instance_lock::{self, InstanceGate, LockOutcome};
    use crate::stack::models::{ModelPresence, ModelsStatus};
    use crate::stack::pool::{PoolTimings, WorkerPoolState};
    use crate::stack::procinfo::SystemProc;
    use std::cell::Cell;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
    use std::sync::Arc;

    struct Fx {
        _tmp: tempfile::TempDir,
        root: PathBuf,
        ctx: StackContext,
        infra: InfraState,
    }

    impl Fx {
        fn new() -> Fx {
            Fx::with_lock(false)
        }

        fn with_lock(busy: bool) -> Fx {
            let tmp = tempfile::tempdir().unwrap();
            let root = fs::canonicalize(tmp.path()).unwrap();
            let outcome = if busy {
                LockOutcome::Busy
            } else {
                instance_lock::acquire(&root.join("appdata")).unwrap()
            };
            // Sem `docker` nem `uv` resolvidos: nenhum teste toca o Docker real do operador.
            let ctx = StackContext::with_located(
                root.join("config"),
                root.join("home"),
                InstanceGate::new(outcome),
                crate::stack::locate::Located::default(),
            );
            Fx {
                _tmp: tmp,
                root,
                ctx,
                infra: InfraState::new(),
            }
        }
    }

    struct Fixed(bool, Cell<u32>);

    impl Confirmer for Fixed {
        fn confirm(&self, _title: &str, _message: &str) -> bool {
            self.1.set(self.1.get() + 1);
            self.0
        }
    }

    // ---- 8.5.4: `confirmed` ----

    #[test]
    fn confirmed_must_be_the_boolean_true() {
        assert!(require_confirmed(Some(&Value::Bool(true))).is_ok());
        let refused: [Option<Value>; 6] = [
            None,
            Some(Value::Null),
            Some(Value::Bool(false)),
            Some(serde_json::json!("true")),
            Some(serde_json::json!(1)),
            Some(serde_json::json!({"confirmed": true})),
        ];
        for value in refused {
            let err = require_confirmed(value.as_ref()).unwrap_err();
            assert_eq!(err.code, ErrorCode::ConfirmationRequired, "{value:?}");
        }
    }

    /// `docker` e `uv` falsos num repositório de teste: o suficiente para chegar ao diálogo.
    fn fx_with_models_repo(models_out: &str) -> (Fx, PathBuf) {
        let mut fx = Fx::new();
        let (bin, repo) = (fx.root.join("bin"), fx.root.join("repo"));
        fs::create_dir_all(&bin).unwrap();
        fs::create_dir_all(repo.join("stack")).unwrap();
        fs::create_dir_all(fx.root.join("home")).unwrap();
        let uv = "#!/bin/sh\nd=$(dirname \"$0\")\necho \"$@\" >> \"$d/uv-calls.log\"\n\
                  case \" $* \" in\n  *\" artigo2md --version \"*) echo artigo2md 0.1; exit 0;;\n  \
                  *\" models status --json \"*) cat \"$d/models.out\"; exit 0;;\nesac\nexit 99\n";
        fs::write(bin.join("uv"), uv).unwrap();
        fs::set_permissions(bin.join("uv"), fs::Permissions::from_mode(0o755)).unwrap();
        fs::write(bin.join("models.out"), models_out).unwrap();
        for file in [
            "stack/compose.yaml",
            "stack/up.sh",
            "stack/down.sh",
            "pyproject.toml",
            "uv.lock",
        ] {
            fs::write(repo.join(file), "").unwrap();
        }
        let located = crate::stack::locate::Located {
            docker: None,
            uv: Some(bin.join("uv")),
            colima: None,
        };
        let gate = InstanceGate::new(instance_lock::acquire(&fx.root.join("appdata2")).unwrap());
        fx.ctx =
            StackContext::with_located(fx.root.join("config"), fx.root.join("home"), gate, located);
        fx.ctx
            .settings
            .set_repo_path(Some(repo.to_str().unwrap().to_string()))
            .unwrap();
        (fx, bin)
    }

    const MODELS_MISSING: &str = "{\"schema_version\":1,\"event\":\"status\",\"cache_dir\":\"/c\",\"models\":[{\"id\":\"layout\",\"present\":false},{\"id\":\"tableformer\",\"present\":true}]}\n";

    #[test]
    fn forged_confirmed_does_not_skip_the_native_dialog() {
        // `confirmed: true` passou no comando, mas a pessoa recusa o diálogo nativo: nenhum download.
        let (fx, bin) = fx_with_models_repo(MODELS_MISSING);
        assert!(require_confirmed(Some(&Value::Bool(true))).is_ok());
        let decline = Fixed(false, Cell::new(0));
        let err = prepare_models(&fx.ctx, &fx.infra, &decline).unwrap_err();
        assert_eq!(err.code, ErrorCode::ConfirmationRequired);
        assert_eq!(decline.1.get(), 1, "o diálogo nativo sempre é mostrado");
        let calls = fs::read_to_string(bin.join("uv-calls.log")).unwrap();
        assert!(!calls.contains("models download"), "chamadas: {calls}");
        // O resultado da operação ficou no retrato.
        let ops = fx.infra.operation_active(OperationKind::PrepareModels);
        assert!(!ops, "a operação terminou");
    }

    #[test]
    fn unsupported_models_subcommand_is_repo_invalid_before_the_dialog() {
        let (fx, _bin) = fx_with_models_repo("");
        let accept = Fixed(true, Cell::new(0));
        let err = prepare_models(&fx.ctx, &fx.infra, &accept).unwrap_err();
        assert_eq!(err.code, ErrorCode::RepoInvalid);
        assert_eq!(
            accept.1.get(),
            0,
            "sem diálogo quando o repositório não serve"
        );
    }

    // ---- 8.5.4: exclusão de operações ----

    #[test]
    fn concurrent_operation_is_refused_with_operation_in_progress() {
        let fx = Fx::new();
        let held = fx.ctx.gate.begin(OperationKind::StartStack).unwrap();
        let accept = Fixed(true, Cell::new(0));
        assert_eq!(
            start_stack(&fx.ctx, &fx.infra).unwrap_err().code,
            ErrorCode::OperationInProgress
        );
        assert_eq!(
            stop_stack(&fx.ctx, &fx.infra).unwrap_err().code,
            ErrorCode::OperationInProgress
        );
        assert_eq!(
            start_workers(&fx.ctx, &fx.infra).unwrap_err().code,
            ErrorCode::OperationInProgress
        );
        assert_eq!(
            stop_workers(&fx.ctx, &fx.infra).unwrap_err().code,
            ErrorCode::OperationInProgress
        );
        assert_eq!(
            prepare_images(&fx.ctx, &fx.infra, &accept)
                .unwrap_err()
                .code,
            ErrorCode::OperationInProgress
        );
        assert_eq!(accept.1.get(), 0);
        drop(held);
        // Liberada a reserva, a próxima operação chega às conferências (sem Docker: dockerNotReady).
        assert_eq!(
            start_stack(&fx.ctx, &fx.infra).unwrap_err().code,
            ErrorCode::DockerNotReady
        );
    }

    #[test]
    fn second_instance_is_read_only_and_refuses_every_operation() {
        let fx = Fx::with_lock(true);
        let accept = Fixed(true, Cell::new(0));
        for code in [
            start_stack(&fx.ctx, &fx.infra).unwrap_err().code,
            stop_workers(&fx.ctx, &fx.infra).unwrap_err().code,
            prepare_models(&fx.ctx, &fx.infra, &accept)
                .unwrap_err()
                .code,
            resolve_previous_pool(&fx.ctx, &fx.infra, PreviousPoolAction::Stop)
                .unwrap_err()
                .code,
        ] {
            assert_eq!(code, ErrorCode::AnotherInstance);
        }
        assert_eq!(
            detect_previous_pool(&fx.ctx, &fx.infra),
            DetectOutcome::Nothing
        );
        assert!(!close_work_pending(&fx.ctx, &fx.infra));
    }

    #[test]
    fn previous_pool_action_is_parsed_strictly() {
        assert_eq!(
            PreviousPoolAction::parse("stop").unwrap(),
            PreviousPoolAction::Stop
        );
        assert_eq!(
            PreviousPoolAction::parse("keep").unwrap(),
            PreviousPoolAction::Keep
        );
        assert!(PreviousPoolAction::parse("Stop").is_err());
        assert!(PreviousPoolAction::parse("").is_err());
        let fx = Fx::new();
        // Sem pool órfão, resolver não vale.
        assert!(resolve_previous_pool(&fx.ctx, &fx.infra, PreviousPoolAction::Keep).is_err());
    }

    // ---- 8.1.2: pré-condições ----

    struct Counters {
        running: AtomicU32,
        amqp: AtomicU32,
        models: AtomicU32,
    }

    fn status(all: bool) -> ModelsCheck {
        ModelsCheck::Checked(ModelsStatus {
            cache_dir: "/c".to_string(),
            models: vec![
                ModelPresence {
                    id: "layout".to_string(),
                    present: true,
                },
                ModelPresence {
                    id: "tableformer".to_string(),
                    present: all,
                },
            ],
        })
    }

    fn run_checks(
        rabbit: bool,
        amqp: bool,
        models: ModelsCheck,
    ) -> (AppResult<()>, Counters, Vec<String>) {
        run_checks_with_owner(
            rabbit,
            amqp,
            models,
            SocketOwner::Indeterminate("teste: sem checagem"),
        )
    }

    fn run_checks_with_owner(
        rabbit: bool,
        amqp: bool,
        models: ModelsCheck,
        owner: SocketOwner,
    ) -> (AppResult<()>, Counters, Vec<String>) {
        struct Lines(Vec<String>);
        impl Reporter for Lines {
            fn stage(&mut self, _stage: OperationStage) {}
            fn line(&mut self, line: Masked) {
                self.0.push(line.into_string());
            }
        }
        let counters = Counters {
            running: AtomicU32::new(0),
            amqp: AtomicU32::new(0),
            models: AtomicU32::new(0),
        };
        let mut lines = Lines(Vec::new());
        let result = check_worker_preconditions(
            &WorkerChecks {
                rabbitmq_running: &|| {
                    counters.running.fetch_add(1, Ordering::SeqCst);
                    Ok(rabbit)
                },
                amqp_healthy: &|| {
                    counters.amqp.fetch_add(1, Ordering::SeqCst);
                    amqp
                },
                models: &|| {
                    counters.models.fetch_add(1, Ordering::SeqCst);
                    models.clone()
                },
                socket_owner: &|| owner.clone(),
            },
            &mut lines,
        );
        (result, counters, lines.0)
    }

    #[test]
    fn worker_preconditions_refuse_without_the_stack_and_never_look_at_models() {
        let (r, c, _) = run_checks(false, true, status(true));
        assert_eq!(r.unwrap_err().code, ErrorCode::StackNotRunning);
        assert_eq!(c.models.load(Ordering::SeqCst), 0);

        // RabbitMQ listado, mas a sondagem AMQP falha: continua recusado (M2 exige as duas).
        let (r, c, _) = run_checks(true, false, status(true));
        assert_eq!(r.unwrap_err().code, ErrorCode::StackNotRunning);
        assert_eq!(c.amqp.load(Ordering::SeqCst), 1);
        assert_eq!(c.models.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn worker_preconditions_require_the_models_step_done() {
        let (r, _, _) = run_checks(true, true, status(false));
        assert_eq!(r.unwrap_err().code, ErrorCode::ModelsMissing);
        for check in [ModelsCheck::Failed, ModelsCheck::PlatformUnsupported] {
            let (r, _, _) = run_checks(true, true, check);
            assert_eq!(r.unwrap_err().code, ErrorCode::ModelsMissing);
        }
        let (r, _, _) = run_checks(true, true, ModelsCheck::Unsupported);
        assert_eq!(r.unwrap_err().code, ErrorCode::RepoInvalid);
    }

    #[test]
    fn worker_preconditions_recheck_the_stack_right_before_starting() {
        let (r, c, lines) = run_checks(true, true, status(true));
        r.unwrap();
        assert_eq!(c.running.load(Ordering::SeqCst), 2);
        assert_eq!(c.amqp.load(Ordering::SeqCst), 2);
        assert_eq!(c.models.load(Ordering::SeqCst), 1);
        assert!(
            lines.iter().any(|l| l.contains("não pôde ser conferido")),
            "{lines:?}"
        );
    }

    #[test]
    fn a_foreign_listener_on_the_rabbitmq_port_refuses_the_start_without_credentials() {
        let (r, _, lines) = run_checks_with_owner(
            true,
            true,
            status(true),
            SocketOwner::Divergent("python3".to_string()),
        );
        let err = r.unwrap_err();
        assert_eq!(err.code, ErrorCode::StackNotRunning);
        assert!(err.to_string().contains("python3"));
        assert!(lines.iter().all(|l| !l.contains("conferido:")), "{lines:?}");
    }

    #[test]
    fn a_confirmed_socket_owner_is_logged_and_start_proceeds() {
        let (r, _, lines) = run_checks_with_owner(true, true, status(true), SocketOwner::Confirmed);
        r.unwrap();
        assert!(
            lines
                .iter()
                .any(|l| l.contains("conferido: processo do runtime")),
            "{lines:?}"
        );
    }

    #[test]
    fn start_workers_on_a_running_pool_is_already_in_state_without_any_check() {
        let fx = Fx::new();
        let mut pool = WorkerPool::stopped(4, 4822);
        pool.state = WorkerPoolState::Running;
        fx.infra.set_pool(pool);
        let outcome = start_workers(&fx.ctx, &fx.infra).unwrap();
        assert!(outcome.already_in_state);
    }

    #[test]
    fn start_workers_without_a_ready_runtime_is_refused() {
        let fx = Fx::new();
        assert_eq!(
            start_workers(&fx.ctx, &fx.infra).unwrap_err().code,
            ErrorCode::DockerNotReady
        );
        assert_eq!(fx.infra.pool.view().state, WorkerPoolState::Stopped);
    }

    #[test]
    fn stop_workers_on_a_stopped_pool_is_already_in_state() {
        let fx = Fx::new();
        assert!(stop_workers(&fx.ctx, &fx.infra).unwrap().already_in_state);
    }

    #[test]
    fn failed_preparation_step_is_kept_in_the_snapshot() {
        let fx = Fx::new();
        let mut step = FirstRunStep::new(StepKind::Models);
        step.finish_failed(DownloadFailure::NoNetwork);
        let err: Result<FirstRunStep, PrepareError> = Err(PrepareError {
            error: AppError::new(ErrorCode::DownloadFailed, "sem rede"),
            failure: Some(DownloadFailure::NoNetwork),
            shortfall_bytes: None,
            step: Some(Box::new(step)),
        });
        // A primeira rodada do temporizador verifica as etapas; a falha chega depois dela.
        crate::stack::tick(&fx.ctx, &fx.infra).unwrap();
        let failure = finish_step(&fx.infra, err).unwrap_err();
        assert_eq!(failure.result().failure, Some(DownloadFailure::NoNetwork));
        assert_eq!(failure.result().code, Some(ErrorCode::DownloadFailed));
        let snap = crate::stack::tick(&fx.ctx, &fx.infra).unwrap();
        let models = snap
            .first_run
            .iter()
            .find(|s| s.step == StepKind::Models)
            .unwrap();
        assert_eq!(models.state, StepState::Failed);
    }

    // ---- 8.4.4: fechamento ----

    const FAKE_WORKER: &str = "#!/bin/sh\nd=$(dirname \"$0\")\necho run >> \"$d/runs.txt\"\n\
                               mode=$(cat \"$d/mode\")\ncase \"$mode\" in\n  \
                               calm) trap 'exit 0' TERM; while :; do sleep 0.1; done;;\n  \
                               stubborn) trap '' TERM; while :; do sleep 1; done;;\nesac\n";

    struct Closing {
        _tmp: tempfile::TempDir,
        bin: PathBuf,
        app: PathBuf,
        infra: InfraState,
        params: Box<dyn Fn() -> StartParams>,
    }

    fn closing(mode: &str, term_wait: Duration) -> Closing {
        let tmp = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(tmp.path()).unwrap();
        let (bin, repo, data, app) = (
            root.join("bin"),
            root.join("repo"),
            root.join("dados"),
            root.join("app"),
        );
        for d in [&bin, &repo, &data] {
            fs::create_dir_all(d).unwrap();
        }
        fs::write(bin.join("uv"), FAKE_WORKER).unwrap();
        fs::set_permissions(bin.join("uv"), fs::Permissions::from_mode(0o755)).unwrap();
        fs::write(bin.join("mode"), mode).unwrap();
        let pool = crate::stack::pool::PoolManager::with_parts(
            Some(app.clone()),
            Arc::new(SystemProc),
            PoolTimings {
                term_wait,
                kill_wait: Duration::from_secs(5),
            },
            WorkerPool::stopped(2, 100),
        );
        let params = {
            let (uv, bin, repo, data) = (bin.join("uv"), bin.clone(), repo, data);
            Box::new(move || StartParams {
                uv: uv.clone(),
                repo: repo.clone(),
                path_dirs: vec![bin.clone()],
                data_dir: data.clone(),
                workers: 2,
                min_free_memory_mb: 100,
                rabbitmq_port: 5999,
                rabbitmq_user: "u".to_string(),
                rabbitmq_pass: crate::stack::secret::Secret::new("senha-de-teste-123"),
            }) as Box<dyn Fn() -> StartParams>
        };
        Closing {
            _tmp: tmp,
            bin,
            app,
            infra: InfraState::with_pool(pool),
            params,
        }
    }

    fn wait_runs(c: &Closing) {
        let deadline = Instant::now() + Duration::from_secs(10);
        while Instant::now() < deadline {
            if fs::read_to_string(c.bin.join("runs.txt")).is_ok_and(|t| t.lines().count() == 1) {
                // Dá tempo ao `sh` de instalar o `trap`.
                thread::sleep(Duration::from_millis(300));
                return;
            }
            thread::sleep(Duration::from_millis(20));
        }
        panic!("o worker falso não iniciou");
    }

    #[test]
    fn closing_stops_the_pool_before_exit_and_never_touches_the_stack() {
        let c = closing("calm", Duration::from_secs(10));
        c.infra.pool.start((c.params)()).unwrap();
        wait_runs(&c);
        let (_, pgid) = c.infra.pool.live_ids().unwrap();
        let fx = Fx::new();
        assert!(close_work_pending(&fx.ctx, &c.infra));

        let report = shutdown_for_close(&c.infra, Duration::from_secs(30), Duration::from_secs(5));
        assert!(!report.timed_out);
        assert!(report.remaining.is_empty());
        // Grupo vazio antes da saída.
        // SAFETY: só testa a existência do grupo.
        let rc = unsafe { libc::killpg(pgid, 0) };
        assert_eq!(rc, -1);
        assert_eq!(c.infra.pool.view().state, WorkerPoolState::Stopped);
        assert!(!c.app.join(crate::stack::pool::RECORD_FILE).exists());
        // O fechamento só mexe em processos do app: nenhuma chamada ao Docker nem aos scripts.
        assert!(!c.bin.join("docker-calls.log").exists());
    }

    #[test]
    fn closing_with_the_stack_up_and_the_pool_stopped_does_nothing() {
        let c = closing("calm", Duration::from_secs(10));
        let fx = Fx::new();
        assert!(!close_work_pending(&fx.ctx, &c.infra));
        let started = Instant::now();
        let report = shutdown_for_close(&c.infra, Duration::from_secs(30), Duration::from_secs(5));
        assert!(!report.timed_out && report.remaining.is_empty());
        assert!(started.elapsed() < Duration::from_secs(2));
        assert_eq!(c.infra.pool.view().state, WorkerPoolState::Stopped);
    }

    #[test]
    fn closing_respects_the_ceiling_and_reports_the_remaining_pids() {
        // O worker ignora SIGTERM e a espera calma (30 s) é maior que o teto do teste (1 s).
        let c = closing("stubborn", Duration::from_secs(30));
        c.infra.pool.start((c.params)()).unwrap();
        wait_runs(&c);
        let (pid, pgid) = c.infra.pool.live_ids().unwrap();

        let started = Instant::now();
        let report = shutdown_for_close(&c.infra, Duration::from_secs(1), Duration::from_secs(1));
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "o teto foi respeitado"
        );
        assert!(report.timed_out);
        assert_eq!(report.remaining, vec![pid]);
        // O registro continua: a próxima abertura aplica FR-021.
        assert!(c.app.join(crate::stack::pool::RECORD_FILE).exists());

        // Limpeza do teste.
        // SAFETY: sinal ao grupo criado por este teste.
        unsafe {
            libc::killpg(pgid, libc::SIGKILL);
        }
        let deadline = Instant::now() + Duration::from_secs(10);
        while c.infra.pool.is_active() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(50));
        }
    }

    #[test]
    fn closing_terminates_running_operations_then_kills_after_the_grace() {
        let tmp = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(tmp.path()).unwrap();
        let script = root.join("op.sh");
        fs::write(
            &script,
            "#!/bin/sh\ntrap '' TERM\necho pronto\nwhile :; do sleep 1; done\n",
        )
        .unwrap();
        fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();

        let infra = Arc::new(InfraState::new());
        let ready = Arc::new(AtomicBool::new(false));
        let runner = {
            let (infra, ready, script) = (Arc::clone(&infra), Arc::clone(&ready), script.clone());
            thread::spawn(move || {
                exec::with_registry(&infra.groups, || {
                    let spec =
                        exec::ExecSpec::new("/bin/sh", Duration::from_secs(120)).arg(&script);
                    exec::run(spec, &crate::stack::secret::Masker::new(), |line| {
                        if line.text.as_str() == "pronto" {
                            ready.store(true, Ordering::SeqCst);
                        }
                    })
                })
            })
        };
        let deadline = Instant::now() + Duration::from_secs(10);
        while !ready.load(Ordering::SeqCst) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        assert!(ready.load(Ordering::SeqCst));
        thread::sleep(Duration::from_millis(200));
        assert_eq!(infra.groups.ids().len(), 1);

        let started = Instant::now();
        let report =
            shutdown_for_close(&infra, Duration::from_secs(30), Duration::from_millis(500));
        assert!(!report.timed_out);
        assert!(started.elapsed() < Duration::from_secs(10));
        let outcome = runner.join().unwrap().unwrap();
        assert!(
            outcome.signal.is_some(),
            "a operação terminou por sinal: {outcome:?}"
        );
        assert!(infra.groups.is_empty());
    }
}
