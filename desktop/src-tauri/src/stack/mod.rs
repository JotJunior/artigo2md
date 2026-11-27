//! Controle da infraestrutura pelo núcleo Rust (plan.md, "Project Structure"): toda lógica de
//! sistema da feature desktop-stack-control fica aqui; a interface só apresenta o retrato.
//! Os submódulos abaixo são preenchidos pelas tarefas indicadas em cada arquivo.

pub mod clock;
pub mod config;
pub mod data_dir;
pub mod env_file;
pub mod exec;
pub mod first_run;
pub mod health;
pub mod images;
pub mod instance_lock;
pub mod lifecycle;
pub mod locate;
pub mod models;
pub mod ops;
pub mod pool;
pub mod procinfo;
pub mod repo;
pub mod runtime;
pub mod secret;
pub mod socket_owner;
pub mod stack_settings;

// ---------------------------------------------------------------------------------------------
// Retrato da infraestrutura (tarefa 7.6; research Decisions 10 e 11; contracts/tauri-commands.md:
// `get_infra_snapshot`, `refresh_infra`, `get_operation_log`)
// ---------------------------------------------------------------------------------------------

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::Manager;

use crate::error::ErrorCode;
use crate::stack::config::{Ports, StackContext};
use crate::stack::data_dir::DataDirCheck;
use crate::stack::env_file::{
    DEFAULT_API_PORT, DEFAULT_POSTGRES_PORT, DEFAULT_RABBITMQ_PORT, KEY_API_PORT,
    KEY_POSTGRES_PORT, KEY_RABBITMQ_PORT,
};
use crate::stack::exec::GroupRegistry;
use crate::stack::first_run::{DownloadFailure, FirstRunStep, StepKind, StepState};
use crate::stack::health::{ServiceState, ServiceStatus};
use crate::stack::instance_lock::OperationKind;
use crate::stack::lifecycle::{OperationStage, Reporter};
use crate::stack::models::{ModelsCheck, ModelsEnv};
use crate::stack::pool::{PoolManager, WorkerPool, WorkerPoolState};
use crate::stack::repo::{RepoCheck, RepoStatus};
use crate::stack::runtime::{DetectEnv, DockerEnv, RuntimeOverall, RuntimeReport};
use crate::stack::secret::Masked;

/// Espera entre rodadas do temporizador do núcleo (Decision 10: 2 s).
pub const TICK_INTERVAL: Duration = Duration::from_secs(2);
/// O runtime é verificado a cada 10 s, ou a cada 5 s com o guia de instalação aberto.
pub const RUNTIME_INTERVAL: Duration = Duration::from_secs(10);
pub const RUNTIME_INTERVAL_GUIDE: Duration = Duration::from_secs(5);
/// Linhas técnicas guardadas por operação (data-model.md, `Operation`).
pub const MAX_LOG_LINES: usize = 500;

/// Indicador resumido da barra lateral (FR-025; regra em research, Decision 10).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum Summary {
    Healthy,
    Partial,
    Stopped,
}

/// "Saudável" quando o runtime está pronto e os três serviços e o pool estão saudáveis; "parada"
/// quando os três serviços estão parados; "parcial" em qualquer outro caso.
pub fn compute_summary(
    runtime: &RuntimeReport,
    services: &[ServiceStatus],
    pool: &WorkerPool,
) -> Summary {
    if !services.is_empty() && services.iter().all(|s| s.state == ServiceState::Stopped) {
        return Summary::Stopped;
    }
    let services_healthy =
        !services.is_empty() && services.iter().all(|s| s.state == ServiceState::Healthy);
    if runtime.overall == RuntimeOverall::Ready
        && services_healthy
        && pool.state == WorkerPoolState::Running
    {
        Summary::Healthy
    } else {
        Summary::Partial
    }
}

/// Resultado de uma operação: `ok` ou um código de erro de contracts/tauri-commands.md, com o
/// serviço e a porta (portInUse, serviceUnhealthy), o motivo do download e o espaço que falta.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OperationResult {
    pub ok: bool,
    pub code: Option<ErrorCode>,
    pub service: Option<String>,
    pub port: Option<u16>,
    pub failure: Option<DownloadFailure>,
    pub shortfall_bytes: Option<u64>,
    pub already_in_state: Option<bool>,
}

impl OperationResult {
    pub fn ok(already_in_state: bool) -> Self {
        Self {
            ok: true,
            code: None,
            service: None,
            port: None,
            failure: None,
            shortfall_bytes: None,
            already_in_state: Some(already_in_state),
        }
    }

    pub fn error(code: ErrorCode) -> Self {
        Self {
            ok: false,
            code: Some(code),
            service: None,
            port: None,
            failure: None,
            shortfall_bytes: None,
            already_in_state: None,
        }
    }
}

/// Operação de ciclo de vida. A saída técnica não vai no retrato (é consultada sob demanda por
/// `get_operation_log`, FR-014).
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Operation {
    pub kind: OperationKind,
    pub stage: OperationStage,
    pub started_at: String,
    pub result: Option<OperationResult>,
    #[serde(skip)]
    pub technical_log: Vec<String>,
}

/// Retrato devolvido à interface (data-model.md, `InfraSnapshot`).
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InfraSnapshot {
    pub read_only: bool,
    pub runtime: RuntimeReport,
    pub repo: RepoCheck,
    pub data_dir: DataDirCheck,
    pub services: Vec<ServiceStatus>,
    pub pool: WorkerPool,
    pub first_run: Vec<FirstRunStep>,
    pub operations: Vec<Operation>,
    pub summary: Summary,
    pub consumer_connected: Option<bool>,
}

/// Quais verificações uma rodada faz.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Scope {
    /// Rodada do temporizador: serviços sempre; runtime só se venceu o intervalo; imagens e modelos
    /// só na primeira rodada (abertura do app).
    Tick,
    /// "Verificar novamente": runtime, repositório, pasta de dados, serviços, imagens e modelos.
    Full,
}

struct Cache {
    runtime: Option<(Instant, RuntimeReport)>,
    repo: Option<(Option<String>, RepoCheck)>,
    first_run: [FirstRunStep; 2],
    first_run_checked: bool,
    models_unsupported: bool,
}

/// Estado do retrato, gerenciado pelo Tauri. A leitura (`snapshot`) só toca o `RwLock` do último
/// retrato: nunca o `Mutex` de operações nem o de verificação (plan.md M20).
pub struct InfraState {
    snapshot: RwLock<Option<InfraSnapshot>>,
    cache: Mutex<Cache>,
    ops: Mutex<Vec<Operation>>,
    last_op_kind: Mutex<Option<OperationKind>>,
    /// Uma verificação por vez.
    verify: Mutex<()>,
    guide_open: AtomicBool,
    /// Pool de workers: estado exibido e processo (FASE 8).
    pub pool: PoolManager,
    /// Grupos de processos das operações em andamento (parar tudo ao fechar o app).
    pub groups: Arc<GroupRegistry>,
}

impl Default for InfraState {
    fn default() -> Self {
        Self::new()
    }
}

fn is_models(kind: OperationKind) -> bool {
    kind == OperationKind::PrepareModels
}

impl InfraState {
    pub fn new() -> Self {
        Self::with_pool(PoolManager::default())
    }

    /// Com o registro do pool (`worker-pool.json`) gravado em `app_data_dir`.
    pub fn with_record_dir(dir: PathBuf) -> Self {
        Self::with_pool(PoolManager::new(Some(dir)))
    }

    pub fn with_pool(pool: PoolManager) -> Self {
        Self {
            snapshot: RwLock::new(None),
            cache: Mutex::new(Cache {
                runtime: None,
                repo: None,
                first_run: [
                    FirstRunStep::new(StepKind::Images),
                    FirstRunStep::new(StepKind::Models),
                ],
                first_run_checked: false,
                models_unsupported: false,
            }),
            ops: Mutex::new(Vec::new()),
            last_op_kind: Mutex::new(None),
            verify: Mutex::new(()),
            guide_open: AtomicBool::new(false),
            pool,
            groups: Arc::new(GroupRegistry::default()),
        }
    }

    /// Último retrato produzido pelo temporizador (sem executar verificação nem pegar o `Mutex` de
    /// operação). Antes da primeira rodada devolve um retrato inicial com `runtime.checkedAt` vazio.
    pub fn snapshot(&self) -> Option<InfraSnapshot> {
        self.snapshot
            .read()
            .unwrap_or_else(|p| p.into_inner())
            .clone()
    }

    fn store_snapshot(&self, snapshot: InfraSnapshot) {
        *self.snapshot.write().unwrap_or_else(|p| p.into_inner()) = Some(snapshot);
    }

    /// Avisa se o guia de instalação está aberto (runtime a cada 5 s em vez de 10 s).
    pub fn set_guide_open(&self, open: bool) {
        self.guide_open.store(open, Ordering::SeqCst);
    }

    fn runtime_interval(&self) -> Duration {
        if self.guide_open.load(Ordering::SeqCst) {
            RUNTIME_INTERVAL_GUIDE
        } else {
            RUNTIME_INTERVAL
        }
    }

    /// Substitui o estado do pool exibido (testes; o pool real se atualiza sozinho).
    pub fn set_pool(&self, pool: WorkerPool) {
        self.pool.set_view(pool);
    }

    /// Atualiza uma etapa da primeira execução (progresso de `prepare_images`/`prepare_models`).
    pub fn set_step(&self, step: &FirstRunStep) {
        let mut cache = self.cache.lock().unwrap_or_else(|p| p.into_inner());
        let index = match step.step {
            StepKind::Images => 0,
            StepKind::Models => 1,
        };
        cache.first_run[index] = step.clone();
    }

    /// Há uma operação desse tipo em andamento (sem resultado)?
    pub fn operation_active(&self, kind: OperationKind) -> bool {
        self.ops
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .iter()
            .any(|o| o.kind == kind && o.result.is_none())
    }

    /// Registra o início de uma operação (substitui a anterior do mesmo grupo: no máximo uma de
    /// stack e uma de modelos, `InfraSnapshot.operations` tem 0 a 2).
    pub fn begin_operation(&self, kind: OperationKind, stage: OperationStage) {
        let mut ops = self.ops.lock().unwrap_or_else(|p| p.into_inner());
        ops.retain(|o| is_models(o.kind) != is_models(kind));
        ops.push(Operation {
            kind,
            stage,
            started_at: clock::now_iso8601(),
            result: None,
            technical_log: Vec::new(),
        });
        *self.last_op_kind.lock().unwrap_or_else(|p| p.into_inner()) = Some(kind);
    }

    fn with_operation(&self, kind: OperationKind, f: impl FnOnce(&mut Operation)) {
        let mut ops = self.ops.lock().unwrap_or_else(|p| p.into_inner());
        if let Some(op) = ops.iter_mut().find(|o| o.kind == kind) {
            f(op);
        }
    }

    pub fn finish_operation(&self, kind: OperationKind, result: OperationResult) {
        self.with_operation(kind, |op| op.result = Some(result));
    }

    /// Acompanhante que alimenta a operação `kind` (etapa, linhas técnicas e progresso).
    pub fn tracker(&self, kind: OperationKind) -> OperationTracker<'_> {
        OperationTracker { state: self, kind }
    }

    /// Saída técnica mascarada da última operação (`get_operation_log`).
    pub fn operation_log(&self) -> Vec<String> {
        let Some(kind) = *self.last_op_kind.lock().unwrap_or_else(|p| p.into_inner()) else {
            return Vec::new();
        };
        let ops = self.ops.lock().unwrap_or_else(|p| p.into_inner());
        ops.iter()
            .find(|o| o.kind == kind)
            .map(|o| o.technical_log.clone())
            .unwrap_or_default()
    }

    fn operations(&self) -> Vec<Operation> {
        self.ops.lock().unwrap_or_else(|p| p.into_inner()).clone()
    }
}

/// `Reporter` de uma operação: etapa atual, linhas técnicas (máximo `MAX_LOG_LINES`) e progresso
/// das etapas da primeira execução.
pub struct OperationTracker<'a> {
    state: &'a InfraState,
    kind: OperationKind,
}

impl Reporter for OperationTracker<'_> {
    fn stage(&mut self, stage: OperationStage) {
        self.state.with_operation(self.kind, |op| op.stage = stage);
    }

    fn line(&mut self, line: Masked) {
        self.state.with_operation(self.kind, |op| {
            if op.technical_log.len() == MAX_LOG_LINES {
                op.technical_log.remove(0);
            }
            op.technical_log.push(line.into_string());
        });
    }

    fn step(&mut self, step: &FirstRunStep) {
        self.state.set_step(step);
    }
}

fn ports_of(repo: Option<&Path>) -> Ports {
    let defaults = Ports {
        postgres: DEFAULT_POSTGRES_PORT,
        rabbitmq: DEFAULT_RABBITMQ_PORT,
        api: DEFAULT_API_PORT,
    };
    let Some(repo) = repo else {
        return defaults;
    };
    match env_file::load(&env_file::env_path(repo)) {
        Ok(Some(env)) => Ports {
            postgres: env.port(KEY_POSTGRES_PORT).unwrap_or(defaults.postgres),
            rabbitmq: env.port(KEY_RABBITMQ_PORT).unwrap_or(defaults.rabbitmq),
            api: env.port(KEY_API_PORT).unwrap_or(defaults.api),
        },
        _ => defaults,
    }
}

fn images_step(
    docker: Option<&DockerEnv>,
    repo_ok: bool,
    data_dir: &DataDirCheck,
    data_dir_text: &str,
) -> FirstRunStep {
    let mut step = FirstRunStep::new(StepKind::Images);
    let (Some(env), true, true) = (docker, repo_ok, data_dir.is_ok()) else {
        return step;
    };
    // O Compose exige o `.env` (Decision 8): garante antes de qualquer chamada. Não baixa nada.
    if env_file::ensure(&env_file::env_path(&env.repo), data_dir_text).is_err() {
        return step;
    }
    if let Ok(status) = images::check(env) {
        step.items_total = status.images.len() as u32;
        step.items_done = (status.images.len() - status.missing.len()) as u32;
        if status.all_present() {
            step.state = StepState::Done;
        }
    }
    step
}

fn run_collect(ctx: &StackContext, infra: &InfraState, scope: Scope) -> InfraSnapshot {
    let settings = ctx.settings.load().settings;
    let located = ctx.located();
    let full = scope == Scope::Full;
    let repo_path: Option<PathBuf> = settings.repo_path.as_deref().map(PathBuf::from);

    // Runtime: a cada 10 s (5 s com o guia aberto) ou em "Verificar novamente".
    let cached_runtime = {
        let cache = infra.cache.lock().unwrap_or_else(|p| p.into_inner());
        cache
            .runtime
            .as_ref()
            .filter(|(at, _)| !full && at.elapsed() < infra.runtime_interval())
            .map(|(_, report)| report.clone())
    };
    let runtime = cached_runtime.unwrap_or_else(|| {
        let report = runtime::detect(&DetectEnv::from_located(located, &ctx.home));
        infra
            .cache
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .runtime = Some((Instant::now(), report.clone()));
        report
    });

    // Repositório: só quando o caminho muda ou em "Verificar novamente" (inclui um processo `uv`).
    let cached_repo = {
        let cache = infra.cache.lock().unwrap_or_else(|p| p.into_inner());
        cache
            .repo
            .as_ref()
            .filter(|(key, _)| !full && *key == settings.repo_path)
            .map(|(_, check)| check.clone())
    };
    let mut repo_check = cached_repo.unwrap_or_else(|| {
        let check = repo::check(repo_path.as_deref(), located);
        infra.cache.lock().unwrap_or_else(|p| p.into_inner()).repo =
            Some((settings.repo_path.clone(), check.clone()));
        check
    });

    let data_dir_check = data_dir::check(
        Path::new(&settings.data_dir),
        &ctx.home,
        repo_path.as_deref(),
        full,
    );

    let docker_env = match (&repo_path, repo_check.is_ok()) {
        (Some(repo), true) => DockerEnv::from_report(&runtime, located, repo),
        _ => None,
    };

    // Imagens e modelos: na abertura e em "Verificar novamente"; nunca sobre um download em curso.
    let first_run_due = full
        || !infra
            .cache
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .first_run_checked;
    if first_run_due {
        if !infra.operation_active(OperationKind::PrepareImages) {
            let step = images_step(
                docker_env.as_ref(),
                repo_check.is_ok(),
                &data_dir_check,
                &settings.data_dir,
            );
            infra.set_step(&step);
        }
        let mut unsupported = false;
        if !infra.operation_active(OperationKind::PrepareModels) {
            let step = match repo_path
                .as_deref()
                .filter(|_| repo_check.is_ok())
                .and_then(|repo| ModelsEnv::from_located(located, repo))
                .map(|env| models::check_models(&env))
            {
                Some(ModelsCheck::Checked(status)) => status.to_step(),
                Some(ModelsCheck::Unsupported) => {
                    unsupported = true;
                    FirstRunStep::new(StepKind::Models)
                }
                _ => FirstRunStep::new(StepKind::Models),
            };
            infra.set_step(&step);
            infra
                .cache
                .lock()
                .unwrap_or_else(|p| p.into_inner())
                .models_unsupported = unsupported;
        }
        infra
            .cache
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .first_run_checked = true;
    }

    // Pool adotado de sessão anterior: confere se ainda tem membros válidos (a cada rodada, 2 s).
    infra.pool.poll_adopted();
    let (first_run, models_unsupported) = {
        let cache = infra.cache.lock().unwrap_or_else(|p| p.into_inner());
        (cache.first_run.to_vec(), cache.models_unsupported)
    };
    let mut pool = infra.pool.view();
    if models_unsupported && repo_check.is_ok() {
        repo_check.status = RepoStatus::ModelsUnsupported;
    }
    if pool.state == WorkerPoolState::Stopped {
        // Pool parado mostra a configuração que será usada na próxima execução.
        pool.workers = settings.workers;
        pool.min_free_memory_mb = settings.min_free_memory_mb;
    }

    let operations = infra.operations();
    let start_in_progress = operations
        .iter()
        .any(|o| o.kind == OperationKind::StartStack && o.result.is_none());
    let probe = health::probe_services(
        docker_env.as_ref(),
        ports_of(repo_path.as_deref()),
        start_in_progress,
    );
    let summary = compute_summary(&runtime, &probe.services, &pool);

    InfraSnapshot {
        read_only: ctx.gate.read_only(),
        runtime,
        repo: repo_check,
        data_dir: data_dir_check,
        services: probe.services,
        pool,
        first_run,
        operations,
        summary,
        consumer_connected: probe.consumer_connected,
    }
}

/// Retrato inicial, antes da primeira rodada do temporizador: sem nenhuma verificação (nenhum
/// processo, nenhuma conexão), com `runtime.checkedAt` vazio para a interface mostrar "verificando".
pub fn initial_snapshot(ctx: &StackContext) -> InfraSnapshot {
    let settings = ctx.settings.load().settings;
    let runtime = RuntimeReport {
        runtimes: runtime::RuntimeKind::ALL
            .iter()
            .map(|kind| runtime::RuntimeEntry {
                kind: *kind,
                state: runtime::RuntimeState::NotInstalled,
                context: None,
            })
            .collect(),
        in_use: None,
        overall: RuntimeOverall::NoRuntime,
        docker_path: None,
        checked_at: String::new(),
    };
    let pool = WorkerPool::stopped(settings.workers, settings.min_free_memory_mb);
    let services = health::probe_services(
        None,
        ports_of(settings.repo_path.as_deref().map(Path::new)),
        false,
    )
    .services;
    let summary = compute_summary(&runtime, &services, &pool);
    InfraSnapshot {
        read_only: ctx.gate.read_only(),
        runtime,
        repo: RepoCheck {
            status: RepoStatus::NotSet,
            uv_path: None,
            missing: Vec::new(),
        },
        data_dir: DataDirCheck {
            path: settings.data_dir,
            status: data_dir::DataDirStatus::Missing,
            canonical_path: None,
        },
        services,
        pool,
        first_run: vec![
            FirstRunStep::new(StepKind::Images),
            FirstRunStep::new(StepKind::Models),
        ],
        operations: Vec::new(),
        summary,
        consumer_connected: None,
    }
}

/// Uma rodada completa do temporizador. `None` se já há uma verificação em curso (a rodada é
/// pulada: uma por vez, M20).
pub fn tick(ctx: &StackContext, infra: &InfraState) -> Option<InfraSnapshot> {
    let _guard = infra.verify.try_lock().ok()?;
    let snapshot = run_collect(ctx, infra, Scope::Tick);
    infra.store_snapshot(snapshot.clone());
    Some(snapshot)
}

/// `refresh_infra`: verificação completa; espera uma rodada em curso terminar.
pub fn refresh_infra(ctx: &StackContext, infra: &InfraState) -> InfraSnapshot {
    let _guard = infra.verify.lock().unwrap_or_else(|p| p.into_inner());
    let snapshot = run_collect(ctx, infra, Scope::Full);
    infra.store_snapshot(snapshot.clone());
    snapshot
}

/// Temporizador do núcleo: atualiza o retrato a cada 2 s, independente da interface (o indicador
/// da barra lateral é sempre visível, FR-025).
pub fn spawn_timer(app: tauri::AppHandle) {
    thread::spawn(move || loop {
        {
            let ctx = app.state::<StackContext>();
            let infra = app.state::<InfraState>();
            let _ = tick(&ctx, &infra);
        }
        thread::sleep(TICK_INTERVAL);
    });
}

#[cfg(test)]
mod infra_tests {
    use super::*;
    use crate::stack::data_dir::DataDirStatus;
    use crate::stack::health::ServiceName;
    use crate::stack::instance_lock::{self, InstanceGate, LockOutcome};
    use crate::stack::locate::Located;
    use crate::stack::pool::WorkerExitReason;
    use crate::stack::runtime::{RuntimeEntry, RuntimeKind, RuntimeState};
    use std::fs;
    use std::net::TcpListener;
    use std::os::unix::fs::PermissionsExt;

    const FAKE_DOCKER: &str = r#"#!/bin/sh
d=$(dirname "$0")
case "$1" in
  context) cat "$d/contexts.json"; exit 0;;
  --context=*)
    c=${1#--context=}
    case " $* " in
      *" info "*) [ -e "$d/running-$c" ] && { echo '{}'; exit 0; }; exit 1;;
      *" compose version "*) exit 0;;
      *" ps --status running --services "*)
        [ -e "$d/ps.hang" ] && sleep 10
        cat "$d/running.txt" 2>/dev/null; exit 0;;
      *" config --images "*) cat "$d/images.txt"; exit 0;;
      *" image inspect "*) for a in "$@"; do last=$a; done; grep -qx "$last" "$d/present.txt" 2>/dev/null; exit $?;;
    esac;;
esac
exit 99
"#;

    const FAKE_UV: &str = r#"#!/bin/sh
d=$(dirname "$0")
case " $* " in
  *" artigo2md --version "*) echo "artigo2md 0.1"; exit 0;;
  *" models status --json "*) cat "$d/models.out" 2>/dev/null; exit $(cat "$d/models.code" 2>/dev/null || echo 0);;
esac
exit 99
"#;

    const IMAGES: &str = "artigo2md-api:local\npostgres:18-trixie\nrabbitmq:4.4-management\n";
    const MODELS_OK: &str = "{\"schema_version\":1,\"event\":\"status\",\"cache_dir\":\"/c\",\"models\":[{\"id\":\"layout\",\"present\":true},{\"id\":\"tableformer\",\"present\":true}]}\n";

    struct Fx {
        _tmp: tempfile::TempDir,
        bin: PathBuf,
        repo: PathBuf,
        ctx: StackContext,
        infra: InfraState,
    }

    fn free_port() -> u16 {
        TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap()
            .port()
    }

    impl Fx {
        fn new() -> Fx {
            Fx::with_lock(false)
        }

        fn with_lock(busy: bool) -> Fx {
            let tmp = tempfile::tempdir().unwrap();
            let root = fs::canonicalize(tmp.path()).unwrap();
            let (bin, repo, home) = (root.join("bin"), root.join("repo"), root.join("home"));
            for d in [&bin, &repo.join("stack"), &home.join("artigo2md-dados")] {
                fs::create_dir_all(d).unwrap();
            }
            for (name, body) in [("docker", FAKE_DOCKER), ("uv", FAKE_UV)] {
                let path = bin.join(name);
                fs::write(&path, body).unwrap();
                fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
            }
            for file in [
                "stack/compose.yaml",
                "stack/up.sh",
                "stack/down.sh",
                "pyproject.toml",
                "uv.lock",
            ] {
                fs::write(repo.join(file), "").unwrap();
            }
            let outcome = if busy {
                LockOutcome::Busy
            } else {
                instance_lock::acquire(&root.join("appdata")).unwrap()
            };
            let located = Located {
                docker: Some(bin.join("docker")),
                uv: Some(bin.join("uv")),
                colima: None,
            };
            let ctx = StackContext::with_located(
                root.join("config"),
                home,
                InstanceGate::new(outcome),
                located,
            );
            let fx = Fx {
                _tmp: tmp,
                bin,
                repo,
                ctx,
                infra: InfraState::new(),
            };
            fx.set("contexts.json", "{\"Current\":true,\"Name\":\"desktop-linux\",\"DockerEndpoint\":\"unix:///x.sock\"}\n");
            fx.set("running-desktop-linux", "");
            fx.set("images.txt", IMAGES);
            fx.set("present.txt", IMAGES);
            fx.set("models.out", MODELS_OK);
            // Direto no repositório de configurações: com o lock ocupado `apply_picked_repo` recusa.
            fx.ctx
                .settings
                .set_repo_path(Some(fx.repo.to_str().unwrap().to_string()))
                .unwrap();
            fx.write_env([free_port(), free_port(), free_port()]);
            fx
        }

        fn set(&self, name: &str, text: &str) {
            fs::write(self.bin.join(name), text).unwrap();
        }

        /// `.env` com portas de teste: nunca as do operador (5432, 5672, 8778).
        fn write_env(&self, ports: [u16; 3]) {
            let data = self.ctx.home.join("artigo2md-dados");
            let text = format!(
                "ARTIGO2MD_DATA_DIR={}\nARTIGO2MD_POSTGRES_PORT={}\nARTIGO2MD_RABBITMQ_PORT={}\n\
                 ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT=15672\nARTIGO2MD_API_PORT={}\nPOSTGRES_USER=artigo2md\n\
                 POSTGRES_PASSWORD=pg-Segredo-Muito-Longo-123\nPOSTGRES_DB=artigo2md\nRABBITMQ_DEFAULT_USER=artigo2md\n\
                 RABBITMQ_DEFAULT_PASS=mq-Segredo-Muito-Longo-456\n",
                data.display(),
                ports[0],
                ports[1],
                ports[2]
            );
            let path = self.repo.join("stack/.env");
            fs::write(&path, text).unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
            // O `.env` é lido por `ensure`/`load`; a pasta de dados do espelho segue o `.env`.
            self.ctx
                .settings
                .mirror_data_dir(data.to_str().unwrap().to_string())
                .unwrap();
        }
    }

    fn pool_in(state: WorkerPoolState) -> WorkerPool {
        let mut pool = WorkerPool::stopped(4, 4822);
        pool.state = state;
        pool
    }

    fn runtime_with(overall: RuntimeOverall) -> RuntimeReport {
        RuntimeReport {
            runtimes: RuntimeKind::ALL
                .iter()
                .map(|k| RuntimeEntry {
                    kind: *k,
                    state: if *k == RuntimeKind::DockerDesktop {
                        RuntimeState::Running
                    } else {
                        RuntimeState::NotInstalled
                    },
                    context: (*k == RuntimeKind::DockerDesktop)
                        .then(|| "desktop-linux".to_string()),
                })
                .collect(),
            in_use: Some(RuntimeKind::DockerDesktop),
            overall,
            docker_path: Some("/Users/x/.docker/bin/docker".to_string()),
            checked_at: "2026-11-26T12:00:00Z".to_string(),
        }
    }

    fn services_in(states: [ServiceState; 3]) -> Vec<ServiceStatus> {
        ServiceName::ALL
            .iter()
            .zip(states)
            .map(|(name, state)| ServiceStatus {
                service: *name,
                state,
                address: "127.0.0.1:1".to_string(),
                checked_at: "2026-11-26T12:00:00Z".to_string(),
            })
            .collect()
    }

    #[test]
    fn summary_composition_follows_the_decision_10_rule() {
        use ServiceState::{Healthy, Starting, Stopped, Unhealthy};
        let ready = runtime_with(RuntimeOverall::Ready);
        let running = pool_in(WorkerPoolState::Running);
        let stopped_pool = pool_in(WorkerPoolState::Stopped);
        // Tudo saudável.
        assert_eq!(
            compute_summary(&ready, &services_in([Healthy; 3]), &running),
            Summary::Healthy
        );
        // Serviços saudáveis com o pool parado, iniciando ou com falha: parcial.
        for state in [
            WorkerPoolState::Stopped,
            WorkerPoolState::Starting,
            WorkerPoolState::Stopping,
            WorkerPoolState::Crashed,
            WorkerPoolState::OrphanFound,
        ] {
            assert_eq!(
                compute_summary(&ready, &services_in([Healthy; 3]), &pool_in(state)),
                Summary::Partial,
                "{state:?}"
            );
        }
        // Os três parados: parada, com qualquer pool.
        assert_eq!(
            compute_summary(&ready, &services_in([Stopped; 3]), &stopped_pool),
            Summary::Stopped
        );
        assert_eq!(
            compute_summary(
                &runtime_with(RuntimeOverall::Stopped),
                &services_in([Stopped; 3]),
                &stopped_pool
            ),
            Summary::Stopped
        );
        // Um serviço com problema, parado ou iniciando: parcial.
        for mixed in [
            [Healthy, Healthy, Unhealthy],
            [Healthy, Stopped, Healthy],
            [Starting, Starting, Starting],
            [Healthy, Healthy, Starting],
        ] {
            assert_eq!(
                compute_summary(&ready, &services_in(mixed), &running),
                Summary::Partial,
                "{mixed:?}"
            );
        }
        // Runtime com problema, mesmo com tudo saudável: parcial.
        for overall in [RuntimeOverall::ComposeMissing, RuntimeOverall::Ambiguous] {
            assert_eq!(
                compute_summary(&runtime_with(overall), &services_in([Healthy; 3]), &running),
                Summary::Partial
            );
        }
    }

    #[test]
    fn full_refresh_composes_the_whole_snapshot_from_the_fakes() {
        let fx = Fx::new();
        fx.set("running.txt", "postgres\nrabbitmq\napi\n");
        assert!(fx.infra.snapshot().is_none());
        let snap = refresh_infra(&fx.ctx, &fx.infra);
        assert!(!snap.read_only);
        assert_eq!(snap.runtime.overall, RuntimeOverall::Ready);
        assert_eq!(snap.repo.status, RepoStatus::Ok);
        assert_eq!(snap.data_dir.status, DataDirStatus::Ok);
        assert_eq!(snap.first_run.len(), 2);
        assert!(snap.first_run.iter().all(|s| s.state == StepState::Done));
        // Compose lista os serviços em execução, mas nada responde nas portas de teste.
        assert_eq!(snap.services.len(), 3);
        assert!(snap
            .services
            .iter()
            .all(|s| s.state == ServiceState::Unhealthy));
        assert_eq!(snap.summary, Summary::Partial);
        assert_eq!(snap.consumer_connected, None);
        assert_eq!(snap.pool.state, WorkerPoolState::Stopped);
        assert!(snap.operations.is_empty());
        // O retrato guardado é o mesmo que a interface lê.
        assert_eq!(fx.infra.snapshot().unwrap().summary, Summary::Partial);
    }

    #[test]
    fn nothing_running_is_a_stopped_summary_and_missing_images_stay_pending() {
        let fx = Fx::new();
        fx.set("present.txt", "postgres:18-trixie\n");
        let snap = refresh_infra(&fx.ctx, &fx.infra);
        assert_eq!(snap.summary, Summary::Stopped);
        assert_eq!(snap.first_run[0].state, StepState::Pending);
        assert_eq!(
            (snap.first_run[0].items_done, snap.first_run[0].items_total),
            (1, 3)
        );
        assert_eq!(snap.first_run[1].state, StepState::Done);
    }

    #[test]
    fn tick_reuses_the_runtime_until_the_interval_or_a_full_refresh() {
        let fx = Fx::new();
        let first = tick(&fx.ctx, &fx.infra).unwrap();
        thread::sleep(Duration::from_millis(1100));
        let second = tick(&fx.ctx, &fx.infra).unwrap();
        assert_eq!(first.runtime.checked_at, second.runtime.checked_at);
        // Os serviços, ao contrário, são sondados a cada rodada.
        assert_ne!(first.services[0].checked_at, second.services[0].checked_at);
        let full = refresh_infra(&fx.ctx, &fx.infra);
        assert_ne!(full.runtime.checked_at, first.runtime.checked_at);
        // Com o guia aberto o intervalo cai para 5 s; fechado, volta a 10 s.
        fx.infra.set_guide_open(true);
        assert_eq!(fx.infra.runtime_interval(), RUNTIME_INTERVAL_GUIDE);
        fx.infra.set_guide_open(false);
        assert_eq!(fx.infra.runtime_interval(), RUNTIME_INTERVAL);
    }

    #[test]
    fn worst_case_round_stays_far_below_ten_seconds() {
        let fx = Fx::new();
        // Servidores que aceitam e nunca respondem, e um `compose ps` que trava.
        let silent: Vec<TcpListener> = (0..3)
            .map(|_| TcpListener::bind("127.0.0.1:0").unwrap())
            .collect();
        let ports: Vec<u16> = silent
            .iter()
            .map(|l| l.local_addr().unwrap().port())
            .collect();
        fx.write_env([ports[0], ports[1], ports[2]]);
        fx.set("ps.hang", "");
        let started = Instant::now();
        let snap = tick(&fx.ctx, &fx.infra).unwrap();
        let elapsed = started.elapsed();
        // Rodada: no máximo 3 s (`ps`) + o resto, folgado; com 2 s de espera e 3 s de leitura da
        // interface, o total de Decision 10 (8 s) fica abaixo de 10 s (SC-003).
        assert!(elapsed < Duration::from_secs(6), "{elapsed:?}");
        assert!(snap
            .services
            .iter()
            .all(|s| s.state == ServiceState::Stopped));
        drop(silent);
    }

    #[test]
    fn second_instance_gets_a_read_only_snapshot() {
        let fx = Fx::with_lock(true);
        let snap = tick(&fx.ctx, &fx.infra).unwrap();
        assert!(snap.read_only);
        assert!(initial_snapshot(&fx.ctx).read_only);
        let owner = Fx::new();
        assert!(!tick(&owner.ctx, &owner.infra).unwrap().read_only);
    }

    #[test]
    fn reading_the_snapshot_never_waits_for_the_operation_mutex() {
        let fx = Fx::new();
        tick(&fx.ctx, &fx.infra).unwrap();
        let _ops = fx.infra.ops.lock().unwrap();
        let _verify = fx.infra.verify.lock().unwrap();
        let _cache = fx.infra.cache.lock().unwrap();
        // Com os três `Mutex` presos, `snapshot()` ainda responde (só usa o `RwLock`).
        assert!(fx.infra.snapshot().is_some());
    }

    #[test]
    fn a_second_verification_is_skipped_while_one_runs() {
        let fx = Fx::new();
        let _running = fx.infra.verify.lock().unwrap();
        assert!(tick(&fx.ctx, &fx.infra).is_none());
    }

    #[test]
    fn operations_track_stage_lines_and_result_in_two_groups() {
        let infra = InfraState::new();
        assert!(infra.operation_log().is_empty());
        infra.begin_operation(OperationKind::StartStack, OperationStage::EnsuringParams);
        assert!(infra.operation_active(OperationKind::StartStack));
        {
            let mut tracker = infra.tracker(OperationKind::StartStack);
            tracker.stage(OperationStage::RunningUp);
            for i in 0..(MAX_LOG_LINES + 100) {
                tracker.line(Masked::non_secret(&format!("linha {i}")));
            }
        }
        let log = infra.operation_log();
        assert_eq!(log.len(), MAX_LOG_LINES);
        assert_eq!(log[0], "linha 100");
        assert_eq!(
            log.last().unwrap(),
            &format!("linha {}", MAX_LOG_LINES + 99)
        );

        // Preparar modelos convive com a operação de stack: dois itens, e o log é o da última.
        infra.begin_operation(OperationKind::PrepareModels, OperationStage::CheckingDisk);
        assert_eq!(infra.operations().len(), 2);
        infra
            .tracker(OperationKind::PrepareModels)
            .line(Masked::non_secret("baixando"));
        assert_eq!(infra.operation_log(), ["baixando"]);

        let mut result = OperationResult::error(ErrorCode::PortInUse);
        result.service = Some("postgres".to_string());
        result.port = Some(5432);
        infra.finish_operation(OperationKind::StartStack, result);
        assert!(!infra.operation_active(OperationKind::StartStack));
        assert!(infra.operation_active(OperationKind::PrepareModels));

        // Nova operação de stack substitui a anterior do mesmo grupo; a de modelos continua.
        infra.begin_operation(OperationKind::StopStack, OperationStage::RunningDown);
        let ops = infra.operations();
        assert_eq!(ops.len(), 2);
        assert!(ops.iter().any(|o| o.kind == OperationKind::StopStack));
        assert!(ops.iter().all(|o| o.kind != OperationKind::StartStack));
    }

    #[test]
    fn operation_serialization_omits_the_technical_log() {
        let infra = InfraState::new();
        infra.begin_operation(OperationKind::StartStack, OperationStage::RunningUp);
        infra
            .tracker(OperationKind::StartStack)
            .line(Masked::non_secret("segredo técnico"));
        let json = serde_json::to_value(infra.operations()).unwrap();
        assert!(json[0].get("technicalLog").is_none());
        assert!(!json.to_string().contains("segredo técnico"));
        assert_eq!(json[0]["kind"], "startStack");
        assert_eq!(json[0]["stage"], "runningUp");
        assert!(json[0]["result"].is_null());
    }

    #[test]
    fn start_in_progress_turns_failing_services_into_starting() {
        let fx = Fx::new();
        fx.infra
            .begin_operation(OperationKind::StartStack, OperationStage::RunningUp);
        let snap = tick(&fx.ctx, &fx.infra).unwrap();
        assert!(snap
            .services
            .iter()
            .all(|s| s.state == ServiceState::Starting));
        assert_eq!(snap.operations.len(), 1);
        assert_eq!(snap.summary, Summary::Partial);
    }

    #[test]
    fn a_refresh_never_overwrites_a_download_in_progress() {
        let fx = Fx::new();
        fx.infra
            .begin_operation(OperationKind::PrepareImages, OperationStage::PullingImages);
        let mut step = FirstRunStep::new(StepKind::Images);
        step.state = StepState::Downloading;
        step.items_total = 3;
        fx.infra.set_step(&step);
        let snap = refresh_infra(&fx.ctx, &fx.infra);
        assert_eq!(snap.first_run[0].state, StepState::Downloading);
        // Os modelos, sem operação em curso, são verificados normalmente.
        assert_eq!(snap.first_run[1].state, StepState::Done);
    }

    #[test]
    fn models_command_missing_in_the_repo_marks_models_unsupported() {
        let fx = Fx::new();
        fx.set("models.code", "2");
        let snap = refresh_infra(&fx.ctx, &fx.infra);
        assert_eq!(snap.repo.status, RepoStatus::ModelsUnsupported);
        assert_eq!(snap.first_run[1].state, StepState::Pending);
        // Repositório corrigido: a próxima verificação completa limpa o estado.
        fx.set("models.code", "0");
        assert_eq!(
            refresh_infra(&fx.ctx, &fx.infra).repo.status,
            RepoStatus::Ok
        );
    }

    #[test]
    fn initial_snapshot_runs_nothing_and_is_marked_unchecked() {
        let fx = Fx::new();
        let snap = initial_snapshot(&fx.ctx);
        assert_eq!(snap.runtime.checked_at, "");
        assert_eq!(snap.runtime.overall, RuntimeOverall::NoRuntime);
        assert_eq!(snap.summary, Summary::Stopped);
        assert_eq!(snap.first_run.len(), 2);
        assert!(snap
            .services
            .iter()
            .all(|s| s.state == ServiceState::Stopped));
        assert_eq!(snap.pool.state, WorkerPoolState::Stopped);
    }

    /// Retrato determinístico que cobre todos os campos e variantes usadas pela interface.
    fn sample_snapshot() -> InfraSnapshot {
        let mut downloading = FirstRunStep::new(StepKind::Models);
        downloading.state = StepState::Downloading;
        downloading.downloaded_bytes = Some(1_200_000_000);
        downloading.items_done = 1;
        downloading.items_total = 2;
        downloading.updated_at = Some("2026-11-26T12:00:05Z".to_string());
        let mut images = FirstRunStep::new(StepKind::Images);
        images.state = StepState::Failed;
        images.failure = Some(DownloadFailure::NoNetwork);
        images.items_total = 3;
        images.updated_at = Some("2026-11-26T11:59:00Z".to_string());
        let mut pool = WorkerPool::stopped(4, 4822);
        pool.state = WorkerPoolState::Crashed;
        pool.exit_code = Some(4);
        pool.reason = Some(WorkerExitReason::Credentials);
        pool.lines = vec!["linha do worker".to_string()];
        pool.memory_warning = true;
        let mut start = OperationResult::error(ErrorCode::PortInUse);
        start.service = Some("postgres".to_string());
        start.port = Some(5432);
        InfraSnapshot {
            read_only: false,
            runtime: runtime_with(RuntimeOverall::Ready),
            repo: RepoCheck {
                status: RepoStatus::ModelsUnsupported,
                uv_path: Some("/opt/homebrew/bin/uv".to_string()),
                missing: vec!["uv.lock".to_string()],
            },
            data_dir: DataDirCheck {
                path: "/Users/x/artigo2md-dados".to_string(),
                status: DataDirStatus::Ok,
                canonical_path: Some("/Users/x/artigo2md-dados".to_string()),
            },
            services: services_in([
                ServiceState::Healthy,
                ServiceState::Unhealthy,
                ServiceState::Starting,
            ]),
            pool,
            first_run: vec![images, downloading],
            operations: vec![
                Operation {
                    kind: OperationKind::StartStack,
                    stage: OperationStage::RunningUp,
                    started_at: "2026-11-26T12:00:00Z".to_string(),
                    result: Some(start),
                    technical_log: vec!["não vai no retrato".to_string()],
                },
                Operation {
                    kind: OperationKind::PrepareModels,
                    stage: OperationStage::DownloadingModels,
                    started_at: "2026-11-26T12:00:01Z".to_string(),
                    result: None,
                    technical_log: Vec::new(),
                },
            ],
            summary: Summary::Partial,
            consumer_connected: Some(false),
        }
    }

    /// Fixture versionada para a paridade Rust ↔ TypeScript (tarefa 9.3): `cargo test` a cria se
    /// faltar, `UPDATE_FIXTURES=1` a regrava e, fora isso, uma mudança no tipo falha aqui.
    #[test]
    fn snapshot_fixture_matches_the_serialized_shape() {
        let json = serde_json::to_string_pretty(&sample_snapshot()).unwrap() + "\n";
        let path =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/infra-snapshot.json");
        let update = std::env::var_os("UPDATE_FIXTURES").is_some();
        if update || !path.exists() {
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, &json).unwrap();
        }
        assert_eq!(
            fs::read_to_string(&path).unwrap(),
            json,
            "regrave com UPDATE_FIXTURES=1 cargo test"
        );
        let value: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert!(value["operations"][0].get("technicalLog").is_none());
        for key in [
            "readOnly",
            "runtime",
            "repo",
            "dataDir",
            "services",
            "pool",
            "firstRun",
            "operations",
            "summary",
            "consumerConnected",
        ] {
            assert!(value.get(key).is_some(), "{key}");
        }
    }
}
