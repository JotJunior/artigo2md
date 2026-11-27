//! Pool de workers (spec FR-018 a FR-024; research Decision 12; plan.md M2, M8, M18, M21;
//! data-model.md `WorkerPool` e `WorkerPoolRecord`).
//!
//! - **Iniciar** (`PoolManager::start`): `<uv> run --no-sync --project <repo> artigo2md worker …` em
//!   novo grupo de processos; credenciais e `HF_HUB_OFFLINE=1` só no ambiente do processo.
//! - **Parar** (`stop`): `SIGTERM` só ao processo `uv`, espera de até 45 s, depois `SIGKILL` ao grupo,
//!   depois de conferir os membros do grupo (M8); fim confirmado por `killpg(pgid, 0)` = `ESRCH`.
//! - **Registro** (`worker-pool.json`): gravado ao iniciar e apagado ao terminar; ao abrir o app,
//!   `detect_previous` o confere e oferece parar ou continuar usando. Qualquer conferência que falhe
//!   significa NENHUM sinal enviado e registro descartado (falha fechada de M8).
//! - **Término inesperado**: uma thread aguarda o filho; o código vira `WorkerExitReason`, o estado
//!   `crashed`, sem reinício automático.

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::error::{AppError, AppResult, ErrorCode};
use crate::stack::exec::{self, ExecOutcome, ExecSpec, Stream};
use crate::stack::instance_lock::ensure_private_dir;
use crate::stack::procinfo::{ProcInspector, SystemProc};
use crate::stack::secret::{Masker, Secret};

/// Últimas linhas de stderr do worker guardadas (data-model.md, `WorkerPool.lines`).
pub const MAX_LINES: usize = 200;
/// Prazo passado ao worker (`--shutdown-timeout`); a espera do app depende dele (Decision 12).
pub const SHUTDOWN_TIMEOUT_SECS: u32 = 30;
/// Nome do registro na pasta de dados do app do Tauri.
pub const RECORD_FILE: &str = "worker-pool.json";
const RECORD_TEMP: &str = "worker-pool.json.tmp";
const RECORD_SCHEMA: u32 = 1;
/// Aviso fixo de M8 quando o processo anterior não pôde ser confirmado como worker deste app.
pub const NOT_VERIFIED_NOTICE: &str = "Não foi possível confirmar com segurança que o processo anterior é um worker deste app; nada foi encerrado. Se ele ainda estiver ativo, encerre-o pelo Monitor de Atividade.";
/// Um worker não tem tempo máximo próprio; o `ExecSpec` exige um, então usamos um valor que nunca
/// vence na prática (um ano).
const WORKER_MAX_RUN: Duration = Duration::from_secs(365 * 24 * 3600);
const SPAWN_WAIT: Duration = Duration::from_secs(10);
const POLL: Duration = Duration::from_millis(25);

/// Esperas da parada calma (Decision 12: 30 s do worker + 10 s de junção + 5 s de folga = 45 s).
#[derive(Debug, Clone, Copy)]
pub struct PoolTimings {
    /// Espera depois do `SIGTERM` até o grupo esvaziar.
    pub term_wait: Duration,
    /// Espera depois do `SIGKILL` até o grupo esvaziar.
    pub kill_wait: Duration,
}

impl Default for PoolTimings {
    fn default() -> Self {
        Self {
            term_wait: Duration::from_secs(45),
            kill_wait: Duration::from_secs(5),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum WorkerPoolState {
    Stopped,
    Starting,
    Running,
    Stopping,
    Crashed,
    OrphanFound,
}

/// Motivo do término do pool (`contracts/external-processes.md`), mais `killed` e `unknown`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum WorkerExitReason {
    Stopped,
    Failure,
    Usage,
    QueueExtraMissing,
    Credentials,
    Topology,
    Killed,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkerPool {
    pub state: WorkerPoolState,
    pub workers: u32,
    pub min_free_memory_mb: u32,
    pub exit_code: Option<i32>,
    pub reason: Option<WorkerExitReason>,
    /// Últimas mensagens (máximo 200, credenciais mascaradas).
    pub lines: Vec<String>,
    pub memory_warning: bool,
    pub adopted: bool,
}

impl WorkerPool {
    pub fn stopped(workers: u32, min_free_memory_mb: u32) -> Self {
        Self {
            state: WorkerPoolState::Stopped,
            workers,
            min_free_memory_mb,
            exit_code: None,
            reason: None,
            lines: Vec::new(),
            memory_warning: false,
            adopted: false,
        }
    }
}

// ---------------------------------------------------------------------------------------------
// Registro do pool (`worker-pool.json`)
// ---------------------------------------------------------------------------------------------

/// `worker-pool.json` (data-model.md, `WorkerPoolRecord`): atômico, `0600`, `deny_unknown_fields`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct WorkerPoolRecord {
    pub schema_version: u32,
    pub pid: i32,
    pub pgid: i32,
    /// Início do processo `uv`, em segundos Unix (`proc_pidinfo`).
    pub started_at: u64,
    pub repo_path: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecordRead {
    Absent,
    Valid(WorkerPoolRecord),
    /// Ilegível, de outro dono, com permissão aberta ou fora do esquema.
    Invalid,
}

fn record_path(dir: &Path) -> PathBuf {
    dir.join(RECORD_FILE)
}

pub fn read_record(dir: &Path) -> RecordRead {
    let mut file = match OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(record_path(dir))
    {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return RecordRead::Absent,
        Err(_) => return RecordRead::Invalid,
    };
    let Ok(meta) = file.metadata() else {
        return RecordRead::Invalid;
    };
    // SAFETY: `geteuid` não tem pré-condições.
    let uid = unsafe { libc::geteuid() };
    if !meta.is_file() || meta.uid() != uid || meta.mode() & 0o077 != 0 {
        return RecordRead::Invalid;
    }
    let mut bytes = Vec::new();
    if Read::by_ref(&mut file)
        .take(64 * 1024)
        .read_to_end(&mut bytes)
        .is_err()
    {
        return RecordRead::Invalid;
    }
    match serde_json::from_slice::<WorkerPoolRecord>(&bytes) {
        Ok(r) if r.schema_version == RECORD_SCHEMA && r.pid > 1 && r.pgid > 1 => {
            RecordRead::Valid(r)
        }
        _ => RecordRead::Invalid,
    }
}

pub fn write_record(dir: &Path, record: &WorkerPoolRecord) -> AppResult<()> {
    ensure_private_dir(dir)?;
    let json = serde_json::to_vec_pretty(record)
        .map_err(|e| AppError::new(ErrorCode::Io, e.to_string()))?;
    let temp = dir.join(RECORD_TEMP);
    {
        let mut f: File = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(&temp)?;
        f.write_all(&json)?;
        f.sync_all()?;
    }
    fs::rename(&temp, record_path(dir))?;
    Ok(())
}

pub fn remove_record(dir: &Path) {
    let _ = fs::remove_file(record_path(dir));
}

// ---------------------------------------------------------------------------------------------
// Sinais e conferências (M8)
// ---------------------------------------------------------------------------------------------

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Nenhum processo no grupo: `killpg(pgid, 0)` falha com `ESRCH` (Decision 12).
fn group_gone(pgid: i32) -> bool {
    // SAFETY: `killpg` só recebe inteiros; o sinal 0 apenas testa a existência do grupo.
    let rc = unsafe { libc::killpg(pgid, 0) };
    rc == -1 && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
}

fn pid_gone(pid: i32) -> bool {
    // SAFETY: `kill` só recebe inteiros; o sinal 0 apenas testa a existência do processo.
    let rc = unsafe { libc::kill(pid, 0) };
    rc == -1 && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
}

fn signal_pid(pid: i32, signal: libc::c_int) {
    // SAFETY: só inteiros; chamado depois da conferência de M8 (ou sobre um filho próprio).
    unsafe {
        libc::kill(pid, signal);
    }
}

fn signal_group(pgid: i32, signal: libc::c_int) {
    // SAFETY: só inteiros; chamado depois da conferência de M8.
    unsafe {
        libc::killpg(pgid, signal);
    }
}

fn wait_until(limit: Duration, mut done: impl FnMut() -> bool) -> bool {
    let deadline = Instant::now() + limit;
    loop {
        if done() {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        thread::sleep(POLL);
    }
}

/// Resultado de conferir os membros de um grupo antes de qualquer sinal.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum GroupCheck {
    /// Há membros e todos começaram em `startedAt` ou depois, no grupo esperado.
    Valid,
    /// Nenhum membro vivo.
    Empty,
    /// Algum membro começou antes de `startedAt` ou está em outro grupo: não é nosso.
    Foreign,
    /// A listagem falhou: não confirmado.
    Unknown,
}

fn check_group(inspector: &dyn ProcInspector, pgid: i32, started_at: u64) -> GroupCheck {
    let Some(members) = inspector.group_members(pgid) else {
        return GroupCheck::Unknown;
    };
    let mut seen = 0;
    for pid in members {
        // Membro que sumiu (ou zumbi) entre a listagem e a consulta: não conta.
        let Some(start) = inspector.start_time(pid) else {
            continue;
        };
        if start < started_at {
            return GroupCheck::Foreign;
        }
        if inspector.group_of(pid).is_some_and(|g| g != pgid) {
            return GroupCheck::Foreign;
        }
        seen += 1;
    }
    if seen == 0 {
        GroupCheck::Empty
    } else {
        GroupCheck::Valid
    }
}

/// Resultado da conferência completa de um registro de sessão anterior.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Verdict {
    /// O processo do registro não existe mais (ou o PID foi reutilizado): nada a fazer.
    Gone,
    Verified,
    /// Existe algo, mas não deu para confirmar que é o worker deste app: nenhum sinal.
    Unverified,
}

fn same_path(a: &Path, b: &Path) -> bool {
    match (fs::canonicalize(a), fs::canonicalize(b)) {
        (Ok(a), Ok(b)) => a == b,
        _ => false,
    }
}

fn has_worker_args(args: &[String]) -> bool {
    args.windows(2)
        .any(|w| w[0] == "artigo2md" && w[1] == "worker")
}

fn verify_orphan(
    inspector: &dyn ProcInspector,
    record: &WorkerPoolRecord,
    uv: Option<&Path>,
) -> Verdict {
    match inspector.start_time(record.pid) {
        None if pid_gone(record.pid) => return Verdict::Gone,
        None => return Verdict::Unverified,
        // Horário diferente: o PID foi reutilizado por outro processo; o worker de antes se foi.
        Some(start) if start != record.started_at => return Verdict::Gone,
        Some(_) => {}
    }
    if inspector.group_of(record.pid) != Some(record.pgid) {
        return Verdict::Unverified;
    }
    let (Some(exe), Some(uv)) = (inspector.exe_path(record.pid), uv) else {
        return Verdict::Unverified;
    };
    if !same_path(&exe, uv) {
        return Verdict::Unverified;
    }
    if !inspector
        .args(record.pid)
        .is_some_and(|args| has_worker_args(&args))
    {
        return Verdict::Unverified;
    }
    match check_group(inspector, record.pgid, record.started_at) {
        GroupCheck::Valid => Verdict::Verified,
        _ => Verdict::Unverified,
    }
}

// ---------------------------------------------------------------------------------------------
// Execução do pool
// ---------------------------------------------------------------------------------------------

/// Entradas de `start`: tudo o que o worker precisa, já validado pelo chamador.
pub struct StartParams {
    pub uv: PathBuf,
    pub repo: PathBuf,
    pub path_dirs: Vec<PathBuf>,
    pub data_dir: PathBuf,
    pub workers: u32,
    pub min_free_memory_mb: u32,
    pub rabbitmq_port: u16,
    pub rabbitmq_user: String,
    pub rabbitmq_pass: Secret,
}

/// Linha de comando e ambiente do worker (`contracts/external-processes.md`, `artigo2md worker`).
/// As credenciais vão só no ambiente do processo, nunca em argumentos (FR-011, M19).
pub fn worker_spec(params: &StartParams) -> ExecSpec {
    ExecSpec::new(&params.uv, WORKER_MAX_RUN)
        .for_uv()
        .args(["run", "--no-sync", "--project"])
        .arg(&params.repo)
        .args(["artigo2md", "worker", "--data-dir"])
        .arg(&params.data_dir)
        .arg("--workers")
        .arg(params.workers.to_string())
        .arg("--min-free-memory")
        .arg(params.min_free_memory_mb.to_string())
        .arg("--shutdown-timeout")
        .arg(SHUTDOWN_TIMEOUT_SECS.to_string())
        .current_dir(&params.repo)
        .path_dirs(params.path_dirs.clone())
        .env("RABBITMQ_DEFAULT_USER", &params.rabbitmq_user)
        .secret_env("RABBITMQ_DEFAULT_PASS", params.rabbitmq_pass.clone())
        .env("ARTIGO2MD_RABBITMQ_PORT", params.rabbitmq_port.to_string())
        // Sem esta variável o worker poderia baixar modelos escondido (FR-036, Decision 12).
        .env("HF_HUB_OFFLINE", "1")
}

/// Traduz o fim do processo `uv` (`contracts/external-processes.md`, tabela de saídas).
pub fn translate_exit(outcome: &ExecOutcome) -> (Option<i32>, WorkerExitReason) {
    if outcome.signal.is_some() {
        return (None, WorkerExitReason::Killed);
    }
    match outcome.code {
        Some(0) => (Some(0), WorkerExitReason::Stopped),
        Some(2) => (Some(2), WorkerExitReason::Usage),
        Some(3) => (Some(3), WorkerExitReason::QueueExtraMissing),
        Some(4) => (Some(4), WorkerExitReason::Credentials),
        Some(5) => (Some(5), WorkerExitReason::Topology),
        // 1 (falha) e qualquer código não documentado (incluindo o tempo máximo estourado).
        other => (other, WorkerExitReason::Failure),
    }
}

#[derive(Debug, Clone)]
struct Live {
    pid: i32,
    pgid: i32,
    started_at: u64,
    repo: String,
    adopted: bool,
    /// `uv` resolvido, para reconferir um pool adotado antes de sinalizar.
    uv: Option<PathBuf>,
    /// A thread que aguarda o filho já o colheu.
    exited: bool,
    stop_requested: bool,
}

struct Inner {
    view: WorkerPool,
    live: Option<Live>,
    /// Distingue execuções: uma thread antiga nunca altera o estado de um pool novo.
    generation: u64,
}

struct Shared {
    inner: Mutex<Inner>,
    record_dir: Option<PathBuf>,
    inspector: Arc<dyn ProcInspector>,
    timings: PoolTimings,
}

/// Gerente do pool: estado exibido (`WorkerPool`) e processo em execução.
#[derive(Clone)]
pub struct PoolManager {
    shared: Arc<Shared>,
}

impl Default for PoolManager {
    fn default() -> Self {
        Self::new(None)
    }
}

/// Resultado de `detect_previous`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DetectOutcome {
    /// Sem registro, ou o processo do registro não existe mais.
    Nothing,
    /// Pool da sessão anterior confirmado: o estado passa a `orphanFound`.
    Orphan,
    /// Não confirmado: nenhum sinal, registro descartado, aviso em `lines`.
    Unverified,
}

impl PoolManager {
    /// `record_dir` = pasta de dados do app do Tauri; `None` desliga o registro em disco (testes de
    /// retrato que não iniciam processos).
    pub fn new(record_dir: Option<PathBuf>) -> Self {
        Self::with_parts(
            record_dir,
            Arc::new(SystemProc),
            PoolTimings::default(),
            WorkerPool::stopped(
                crate::stack::stack_settings::DEFAULT_WORKERS,
                crate::stack::stack_settings::DEFAULT_MIN_FREE_MEMORY_MB,
            ),
        )
    }

    pub fn with_parts(
        record_dir: Option<PathBuf>,
        inspector: Arc<dyn ProcInspector>,
        timings: PoolTimings,
        view: WorkerPool,
    ) -> Self {
        Self {
            shared: Arc::new(Shared {
                inner: Mutex::new(Inner {
                    view,
                    live: None,
                    generation: 0,
                }),
                record_dir,
                inspector,
                timings,
            }),
        }
    }

    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.shared.lock()
    }

    /// Estado exibido no retrato.
    pub fn view(&self) -> WorkerPool {
        self.lock().view.clone()
    }

    /// Substitui o estado exibido (testes do retrato).
    pub fn set_view(&self, pool: WorkerPool) {
        self.lock().view = pool;
    }

    /// Há pool nosso vivo, ou em parada? (fechar o app deve esperar por ele; `orphanFound` não conta:
    /// ainda não é nosso, só a pessoa o resolve)
    pub fn is_active(&self) -> bool {
        matches!(
            self.lock().view.state,
            WorkerPoolState::Starting | WorkerPoolState::Running | WorkerPoolState::Stopping
        )
    }

    /// PID e grupo do pool vivo, para o registro de remanescentes ao fechar.
    pub fn live_ids(&self) -> Option<(i32, i32)> {
        self.lock().live.as_ref().map(|l| (l.pid, l.pgid))
    }

    /// Inicia o pool. `Ok(true)` quando já estava em execução (`alreadyInState`); as pré-condições
    /// (stack, modelos) são do chamador. Aceito em `stopped` e `crashed`.
    pub fn start(&self, params: StartParams) -> AppResult<bool> {
        let generation = {
            let mut inner = self.lock();
            match inner.view.state {
                WorkerPoolState::Running | WorkerPoolState::Starting => return Ok(true),
                WorkerPoolState::Stopping => {
                    return Err(AppError::new(
                        ErrorCode::OperationInProgress,
                        "o pool está sendo encerrado",
                    ))
                }
                WorkerPoolState::OrphanFound => {
                    return Err(AppError::new(
                        ErrorCode::OperationInProgress,
                        "há um pool da sessão anterior a resolver",
                    ))
                }
                WorkerPoolState::Stopped | WorkerPoolState::Crashed => {}
            }
            inner.generation += 1;
            inner.live = None;
            inner.view = WorkerPool {
                state: WorkerPoolState::Starting,
                workers: params.workers,
                min_free_memory_mb: params.min_free_memory_mb,
                exit_code: None,
                reason: None,
                lines: Vec::new(),
                memory_warning: false,
                adopted: false,
            };
            inner.generation
        };

        let mut masker = Masker::new();
        masker.register(&params.rabbitmq_pass);
        let spec = worker_spec(&params);
        let repo = params.repo.clone();
        let (tx, rx) = mpsc::channel::<AppResult<()>>();
        let shared = Arc::clone(&self.shared);
        thread::Builder::new()
            .name("worker-pool".into())
            .spawn(move || {
                let spawned = std::cell::Cell::new(false);
                let result = exec::run_observed(
                    spec,
                    &masker,
                    |pid| {
                        spawned.set(true);
                        shared.registered(generation, pid as i32, &repo);
                        let _ = tx.send(Ok(()));
                    },
                    |line| shared.on_line(generation, &line),
                );
                match result {
                    Ok(outcome) => shared.on_exit(generation, &outcome),
                    Err(e) if !spawned.get() => {
                        let _ = tx.send(Err(e));
                    }
                    Err(_) => shared.on_exit(
                        generation,
                        &ExecOutcome {
                            code: Some(1),
                            signal: None,
                            timed_out: false,
                        },
                    ),
                }
            })
            .map_err(|e| AppError::new(ErrorCode::Io, format!("thread do pool: {e}")))?;

        match rx.recv_timeout(SPAWN_WAIT) {
            Ok(Ok(())) => Ok(false),
            Ok(Err(e)) => {
                self.shared.reset_to_stopped(generation);
                Err(e)
            }
            Err(_) => {
                self.shared.reset_to_stopped(generation);
                Err(AppError::new(
                    ErrorCode::Io,
                    "o processo do worker não iniciou a tempo",
                ))
            }
        }
    }

    /// Parada calma. `Ok(true)` se já estava parado (`alreadyInState`). `on_force` é chamado quando
    /// a espera calma esgota e o grupo vai receber `SIGKILL`.
    pub fn stop(&self, on_force: &mut dyn FnMut()) -> AppResult<bool> {
        {
            let inner = self.lock();
            match inner.view.state {
                WorkerPoolState::Stopped | WorkerPoolState::Crashed => return Ok(true),
                WorkerPoolState::OrphanFound => {
                    return Err(AppError::new(
                        ErrorCode::OperationInProgress,
                        "há um pool da sessão anterior a resolver",
                    ))
                }
                WorkerPoolState::Stopping => {
                    return Err(AppError::new(
                        ErrorCode::OperationInProgress,
                        "o pool já está sendo encerrado",
                    ))
                }
                WorkerPoolState::Starting | WorkerPoolState::Running => {}
            }
            if inner.live.is_none() {
                return Err(AppError::new(
                    ErrorCode::OperationInProgress,
                    "o pool ainda está iniciando",
                ));
            }
        }
        self.shared.stop_live(on_force)?;
        Ok(false)
    }

    /// Verifica a cada rodada do temporizador (2 s) se um pool adotado ainda tem membros válidos;
    /// se não tiver, vira `crashed` com motivo `unknown` (Decision 12).
    pub fn poll_adopted(&self) {
        let (generation, pgid, started_at) = {
            let inner = self.lock();
            match (&inner.view.state, &inner.live) {
                (WorkerPoolState::Running, Some(l)) if l.adopted && !l.stop_requested => {
                    (inner.generation, l.pgid, l.started_at)
                }
                _ => return,
            }
        };
        match check_group(self.shared.inspector.as_ref(), pgid, started_at) {
            GroupCheck::Valid | GroupCheck::Unknown => {}
            GroupCheck::Empty | GroupCheck::Foreign => {
                let mut inner = self.lock();
                if inner.generation == generation && inner.view.state == WorkerPoolState::Running {
                    inner.view.state = WorkerPoolState::Crashed;
                    inner.view.exit_code = None;
                    inner.view.reason = Some(WorkerExitReason::Unknown);
                    inner.live = None;
                    drop(inner);
                    self.shared.remove_record();
                }
            }
        }
    }

    /// Ao abrir o app: confere `worker-pool.json` (M8, M18). Só a instância com o lock chama isto.
    /// `repo` é o `repoPath` atual; `uv` o executável resolvido.
    pub fn detect_previous(&self, repo: Option<&str>, uv: Option<&Path>) -> DetectOutcome {
        let Some(dir) = self.shared.record_dir.clone() else {
            return DetectOutcome::Nothing;
        };
        if self.lock().view.state != WorkerPoolState::Stopped {
            return DetectOutcome::Nothing;
        }
        let record = match read_record(&dir) {
            RecordRead::Absent => return DetectOutcome::Nothing,
            RecordRead::Invalid => {
                remove_record(&dir);
                return DetectOutcome::Nothing;
            }
            RecordRead::Valid(r) => r,
        };
        if repo != Some(record.repo_path.as_str()) {
            // Outro repositório: não é "o" pool desta configuração; nada é adotado nem sinalizado.
            remove_record(&dir);
            let alive = self.shared.inspector.start_time(record.pid) == Some(record.started_at);
            if alive {
                self.shared.notice(NOT_VERIFIED_NOTICE);
                return DetectOutcome::Unverified;
            }
            return DetectOutcome::Nothing;
        }
        match verify_orphan(self.shared.inspector.as_ref(), &record, uv) {
            Verdict::Gone => {
                remove_record(&dir);
                DetectOutcome::Nothing
            }
            Verdict::Unverified => {
                remove_record(&dir);
                self.shared.notice(NOT_VERIFIED_NOTICE);
                DetectOutcome::Unverified
            }
            Verdict::Verified => {
                let mut inner = self.lock();
                inner.generation += 1;
                inner.live = Some(Live {
                    pid: record.pid,
                    pgid: record.pgid,
                    started_at: record.started_at,
                    repo: record.repo_path,
                    adopted: true,
                    uv: uv.map(Path::to_path_buf),
                    exited: false,
                    stop_requested: false,
                });
                inner.view.state = WorkerPoolState::OrphanFound;
                inner.view.adopted = false;
                DetectOutcome::Orphan
            }
        }
    }

    /// `resolve_previous_pool` com `keep`: adota o pool da sessão anterior (reconfere antes).
    pub fn keep_previous(&self) -> AppResult<WorkerPool> {
        let record = self.orphan_record()?;
        let uv = self.lock().live.as_ref().and_then(|l| l.uv.clone());
        match verify_orphan(self.shared.inspector.as_ref(), &record, uv.as_deref()) {
            Verdict::Verified => {
                let mut inner = self.lock();
                inner.view.state = WorkerPoolState::Running;
                inner.view.adopted = true;
                inner.view.lines.clear();
                Ok(inner.view.clone())
            }
            Verdict::Gone => {
                self.shared.discard_orphan(None);
                Ok(self.view())
            }
            Verdict::Unverified => {
                self.shared.discard_orphan(Some(NOT_VERIFIED_NOTICE));
                Ok(self.view())
            }
        }
    }

    /// `resolve_previous_pool` com `stop`: parada calma do pool da sessão anterior.
    pub fn stop_previous(&self, on_force: &mut dyn FnMut()) -> AppResult<WorkerPool> {
        let record = self.orphan_record()?;
        let uv = self.lock().live.as_ref().and_then(|l| l.uv.clone());
        match verify_orphan(self.shared.inspector.as_ref(), &record, uv.as_deref()) {
            Verdict::Verified => {
                {
                    let mut inner = self.lock();
                    inner.view.state = WorkerPoolState::Running;
                    inner.view.adopted = true;
                }
                self.shared.stop_live(on_force)?;
            }
            Verdict::Gone => self.shared.discard_orphan(None),
            Verdict::Unverified => self.shared.discard_orphan(Some(NOT_VERIFIED_NOTICE)),
        }
        Ok(self.view())
    }

    fn orphan_record(&self) -> AppResult<WorkerPoolRecord> {
        let inner = self.lock();
        match (&inner.view.state, &inner.live) {
            (WorkerPoolState::OrphanFound, Some(l)) => Ok(WorkerPoolRecord {
                schema_version: RECORD_SCHEMA,
                pid: l.pid,
                pgid: l.pgid,
                started_at: l.started_at,
                repo_path: l.repo.clone(),
            }),
            _ => Err(AppError::new(
                ErrorCode::OperationInProgress,
                "não há pool da sessão anterior a resolver",
            )),
        }
    }
}

impl Shared {
    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn remove_record(&self) {
        if let Some(dir) = &self.record_dir {
            remove_record(dir);
        }
    }

    /// Acrescenta uma mensagem do app (não do worker) às linhas do retrato.
    fn notice(&self, text: &str) {
        let mut inner = self.lock();
        push_line(&mut inner.view, text.to_string());
    }

    fn reset_to_stopped(&self, generation: u64) {
        let mut inner = self.lock();
        if inner.generation == generation {
            inner.live = None;
            inner.view.state = WorkerPoolState::Stopped;
        }
    }

    /// Descarta o registro e o pool anterior sem sinalizar nada (falha fechada de M8).
    fn discard_orphan(&self, notice: Option<&str>) {
        self.remove_record();
        let mut inner = self.lock();
        inner.live = None;
        inner.view.state = WorkerPoolState::Stopped;
        inner.view.adopted = false;
        if let Some(text) = notice {
            push_line(&mut inner.view, text.to_string());
        }
    }

    /// Chamado na thread do worker assim que o processo existe.
    fn registered(&self, generation: u64, pid: i32, repo: &Path) {
        let started_at = self.inspector.start_time(pid).unwrap_or_else(now_secs);
        let repo_text = repo.to_string_lossy().into_owned();
        {
            let mut inner = self.lock();
            if inner.generation != generation {
                return;
            }
            inner.live = Some(Live {
                pid,
                pgid: pid,
                started_at,
                repo: repo_text.clone(),
                adopted: false,
                uv: None,
                exited: false,
                stop_requested: false,
            });
            inner.view.state = WorkerPoolState::Running;
        }
        if let Some(dir) = &self.record_dir {
            let record = WorkerPoolRecord {
                schema_version: RECORD_SCHEMA,
                pid,
                pgid: pid,
                started_at,
                repo_path: repo_text,
            };
            if write_record(dir, &record).is_err() {
                // O pool segue (e para normalmente ao fechar), mas sem o registro um fechamento à
                // força não deixaria rastro para a próxima abertura.
                self.notice(
                    "Não foi possível gravar o registro do pool de workers neste computador.",
                );
            }
        }
    }

    fn on_line(&self, generation: u64, line: &exec::ExecLine) {
        if line.stream != Stream::Stderr {
            return;
        }
        let mut inner = self.lock();
        if inner.generation != generation {
            return;
        }
        let text = line.text.as_str();
        if text.starts_with("Aviso:") {
            inner.view.memory_warning = true;
        }
        push_line(&mut inner.view, text.to_string());
    }

    /// Chamado na thread do worker quando o `uv` terminou (e foi colhido).
    fn on_exit(&self, generation: u64, outcome: &ExecOutcome) {
        let (pgid, started_at, unexpected) = {
            let mut inner = self.lock();
            if inner.generation != generation {
                return;
            }
            let Some(live) = inner.live.as_mut() else {
                return;
            };
            live.exited = true;
            (live.pgid, live.started_at, !live.stop_requested)
        };
        if !unexpected {
            // `stop_live` conduz o resto.
            return;
        }
        // Sobras do grupo (filhos de conversão) não podem ficar para trás: só depois de conferir.
        self.kill_leftovers(pgid, started_at);
        let (code, reason) = translate_exit(outcome);
        let mut inner = self.lock();
        if inner.generation != generation {
            return;
        }
        inner.live = None;
        inner.view.state = WorkerPoolState::Crashed;
        inner.view.exit_code = code;
        inner.view.reason = Some(reason);
        drop(inner);
        self.remove_record();
    }

    fn kill_leftovers(&self, pgid: i32, started_at: u64) {
        if group_gone(pgid) {
            return;
        }
        if check_group(self.inspector.as_ref(), pgid, started_at) == GroupCheck::Valid {
            signal_group(pgid, libc::SIGKILL);
            wait_until(self.timings.kill_wait, || group_gone(pgid));
        }
    }

    /// Parada calma do pool vivo (próprio ou adotado).
    fn stop_live(&self, on_force: &mut dyn FnMut()) -> AppResult<()> {
        let (generation, live) = {
            let mut inner = self.lock();
            let generation = inner.generation;
            let Some(live) = inner.live.as_mut() else {
                return Err(AppError::new(
                    ErrorCode::OperationInProgress,
                    "sem pool a parar",
                ));
            };
            live.stop_requested = true;
            let live = live.clone();
            inner.view.state = WorkerPoolState::Stopping;
            (generation, live)
        };

        // 1. `SIGTERM` só ao processo `uv` (ele repassa ao worker).
        let may_signal_leader = if live.adopted {
            let record = WorkerPoolRecord {
                schema_version: RECORD_SCHEMA,
                pid: live.pid,
                pgid: live.pgid,
                started_at: live.started_at,
                repo_path: live.repo.clone(),
            };
            verify_orphan(self.inspector.as_ref(), &record, live.uv.as_deref()) == Verdict::Verified
        } else {
            // Filho nosso: enquanto a thread de espera não o colheu, o PID é dele.
            !self.lock().live.as_ref().is_none_or(|l| l.exited)
        };
        if may_signal_leader {
            signal_pid(live.pid, libc::SIGTERM);
        } else if live.adopted {
            // Não confirmado: nenhum sinal e o registro cai (falha fechada).
            self.finish_stop(generation, Some(NOT_VERIFIED_NOTICE));
            return Ok(());
        }

        // 2. Espera calma até o grupo esvaziar.
        if wait_until(self.timings.term_wait, || group_gone(live.pgid)) {
            self.finish_stop(generation, None);
            return Ok(());
        }

        // 3. `SIGKILL` ao grupo, depois de conferir os membros (M8).
        match check_group(self.inspector.as_ref(), live.pgid, live.started_at) {
            GroupCheck::Empty => {
                self.finish_stop(generation, None);
                Ok(())
            }
            GroupCheck::Valid => {
                on_force();
                signal_group(live.pgid, libc::SIGKILL);
                if wait_until(self.timings.kill_wait, || group_gone(live.pgid)) {
                    self.finish_stop(generation, None);
                    Ok(())
                } else {
                    self.mark_unstoppable(generation);
                    Err(AppError::new(
                        ErrorCode::Io,
                        format!("o grupo {} ainda tem processos após o SIGKILL", live.pgid),
                    ))
                }
            }
            GroupCheck::Foreign | GroupCheck::Unknown => {
                self.finish_stop(generation, Some(NOT_VERIFIED_NOTICE));
                Ok(())
            }
        }
    }

    fn finish_stop(&self, generation: u64, notice: Option<&str>) {
        self.remove_record();
        let mut inner = self.lock();
        if inner.generation != generation {
            return;
        }
        inner.live = None;
        inner.view.state = WorkerPoolState::Stopped;
        inner.view.exit_code = None;
        inner.view.reason = None;
        if let Some(text) = notice {
            push_line(&mut inner.view, text.to_string());
        }
    }

    fn mark_unstoppable(&self, generation: u64) {
        let mut inner = self.lock();
        if inner.generation == generation {
            inner.view.state = WorkerPoolState::Crashed;
            inner.view.exit_code = None;
            inner.view.reason = Some(WorkerExitReason::Unknown);
        }
    }
}

fn push_line(view: &mut WorkerPool, text: String) {
    if view.lines.len() == MAX_LINES {
        view.lines.remove(0);
    }
    view.lines.push(text);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::process::CommandExt;
    use std::process::{Child, Command, Stdio};
    use std::sync::atomic::{AtomicBool, Ordering};

    const PASSWORD: &str = "mq-Segredo-Muito-Longo-456";

    const FAKE_UV: &str = r#"#!/bin/sh
d=$(dirname "$0")
echo "$@" > "$d/args.txt"
env > "$d/env.txt"
echo run >> "$d/runs.txt"
echo "Aviso: 8 conversores podem esgotar a memória" >&2
echo "senha=$RABBITMQ_DEFAULT_PASS" >&2
mode=$(cat "$d/mode")
case "$mode" in
  calm) trap 'exit 0' TERM; while :; do sleep 0.1; done;;
  stubborn) trap '' TERM; while :; do sleep 1; done;;
  flood) i=0; while [ $i -lt 250 ]; do echo "linha $i" >&2; i=$((i+1)); done; exit 1;;
  kill) kill -KILL $$;;
  exit*) exit ${mode#exit};;
esac
"#;

    struct Fx {
        _tmp: tempfile::TempDir,
        bin: PathBuf,
        repo: PathBuf,
        data: PathBuf,
        app: PathBuf,
    }

    impl Fx {
        fn new() -> Fx {
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
            let uv = bin.join("uv");
            fs::write(&uv, FAKE_UV).unwrap();
            fs::set_permissions(&uv, fs::Permissions::from_mode(0o755)).unwrap();
            let fx = Fx {
                _tmp: tmp,
                bin,
                repo,
                data,
                app,
            };
            fx.mode("calm");
            fx
        }

        fn mode(&self, mode: &str) {
            fs::write(self.bin.join("mode"), mode).unwrap();
        }

        fn params(&self) -> StartParams {
            StartParams {
                uv: self.bin.join("uv"),
                repo: self.repo.clone(),
                path_dirs: vec![self.bin.clone()],
                data_dir: self.data.clone(),
                workers: 3,
                min_free_memory_mb: 1234,
                rabbitmq_port: 5999,
                rabbitmq_user: "artigo2md".to_string(),
                rabbitmq_pass: Secret::new(PASSWORD),
            }
        }

        fn manager(&self, timings: PoolTimings) -> PoolManager {
            PoolManager::with_parts(
                Some(self.app.clone()),
                Arc::new(SystemProc),
                timings,
                WorkerPool::stopped(3, 1234),
            )
        }

        fn runs(&self) -> usize {
            fs::read_to_string(self.bin.join("runs.txt"))
                .map(|t| t.lines().count())
                .unwrap_or(0)
        }
    }

    fn quick() -> PoolTimings {
        PoolTimings {
            term_wait: Duration::from_secs(10),
            kill_wait: Duration::from_secs(5),
        }
    }

    fn wait_for(what: &str, mut f: impl FnMut() -> bool) {
        assert!(
            wait_until(Duration::from_secs(15), &mut f),
            "esperava: {what}"
        );
    }

    fn stop_quietly(mgr: &PoolManager) {
        let _ = mgr.stop(&mut || {});
    }

    #[test]
    fn stopped_pool_serializes_in_camel_case() {
        let json = serde_json::to_value(WorkerPool::stopped(4, 4822)).unwrap();
        assert_eq!(json["state"], "stopped");
        assert_eq!(json["minFreeMemoryMb"], 4822);
        assert!(json["exitCode"].is_null() && json["reason"].is_null());
        assert_eq!(json["memoryWarning"], false);
        assert_eq!(json["lines"], serde_json::json!([]));
    }

    // 8.1.4: argumentos e ambiente corretos, senha ausente de `ps`.
    #[test]
    fn start_passes_exact_arguments_and_credentials_only_in_the_environment() {
        let fx = Fx::new();
        let mgr = fx.manager(quick());
        assert!(!mgr.start(fx.params()).unwrap());
        assert_eq!(mgr.view().state, WorkerPoolState::Running);
        wait_for("o worker falso escreveu o ambiente", || {
            fs::read_to_string(fx.bin.join("env.txt")).is_ok_and(|t| t.contains("HF_HUB_OFFLINE"))
        });

        let args = fs::read_to_string(fx.bin.join("args.txt")).unwrap();
        let expected = format!(
            "run --no-sync --project {} artigo2md worker --data-dir {} --workers 3 \
             --min-free-memory 1234 --shutdown-timeout 30\n",
            fx.repo.display(),
            fx.data.display()
        );
        assert_eq!(args, expected);
        assert!(!args.contains(PASSWORD));

        let env = fs::read_to_string(fx.bin.join("env.txt")).unwrap();
        for line in [
            format!("RABBITMQ_DEFAULT_PASS={PASSWORD}"),
            "RABBITMQ_DEFAULT_USER=artigo2md".to_string(),
            "ARTIGO2MD_RABBITMQ_PORT=5999".to_string(),
            "HF_HUB_OFFLINE=1".to_string(),
        ] {
            assert!(env.lines().any(|l| l == line), "falta {line} no ambiente");
        }
        assert!(!env.contains("ARTIGO2MD_STACK_ENV"));

        // A senha não aparece na lista de processos.
        let (pid, _) = mgr.live_ids().unwrap();
        let ps = Command::new("/bin/ps")
            .args(["-ww", "-o", "command=", "-p", &pid.to_string()])
            .output()
            .unwrap();
        let ps = String::from_utf8_lossy(&ps.stdout);
        assert!(
            ps.contains("artigo2md worker") && !ps.contains(PASSWORD),
            "ps: {ps}"
        );

        stop_quietly(&mgr);
    }

    #[test]
    fn calm_stop_removes_the_group_and_the_record() {
        let fx = Fx::new();
        let mgr = fx.manager(quick());
        mgr.start(fx.params()).unwrap();
        let (pid, pgid) = mgr.live_ids().unwrap();
        assert_eq!(pid, pgid);
        let meta = fs::metadata(fx.app.join(RECORD_FILE)).unwrap();
        assert_eq!(meta.mode() & 0o777, 0o600);
        let RecordRead::Valid(record) = read_record(&fx.app) else {
            panic!("registro inválido");
        };
        assert_eq!(record.pid, pid);
        assert_eq!(record.repo_path, fx.repo.to_string_lossy());

        let mut forced = false;
        assert!(!mgr.stop(&mut || forced = true).unwrap());
        assert!(!forced, "a parada calma não deve forçar");
        assert!(group_gone(pgid));
        let view = mgr.view();
        assert_eq!(view.state, WorkerPoolState::Stopped);
        assert!(view.exit_code.is_none() && view.reason.is_none());
        assert_eq!(read_record(&fx.app), RecordRead::Absent);
        // Parar de novo: já estava parado.
        assert!(mgr.stop(&mut || {}).unwrap());
    }

    #[test]
    fn forced_stop_kills_the_whole_group_when_sigterm_is_ignored() {
        let fx = Fx::new();
        fx.mode("stubborn");
        let mgr = fx.manager(PoolTimings {
            term_wait: Duration::from_millis(400),
            kill_wait: Duration::from_secs(5),
        });
        mgr.start(fx.params()).unwrap();
        let (_, pgid) = mgr.live_ids().unwrap();
        // Dá tempo ao `sh` de instalar o `trap ''` antes do SIGTERM.
        wait_for("o worker falso rodou", || fx.runs() == 1);
        std::thread::sleep(Duration::from_millis(300));

        let mut forced = false;
        mgr.stop(&mut || forced = true).unwrap();
        assert!(forced, "o SIGTERM é ignorado: o grupo deve receber SIGKILL");
        assert!(group_gone(pgid), "sobrou processo no grupo");
        assert_eq!(mgr.view().state, WorkerPoolState::Stopped);
        assert_eq!(read_record(&fx.app), RecordRead::Absent);
    }

    // 8.3.4: cada código de saída, ausência de laço de reinício.
    #[test]
    fn unexpected_exits_are_translated_and_never_restarted() {
        let cases: [(&str, Option<i32>, WorkerExitReason); 8] = [
            ("exit0", Some(0), WorkerExitReason::Stopped),
            ("exit1", Some(1), WorkerExitReason::Failure),
            ("exit2", Some(2), WorkerExitReason::Usage),
            ("exit3", Some(3), WorkerExitReason::QueueExtraMissing),
            ("exit4", Some(4), WorkerExitReason::Credentials),
            ("exit5", Some(5), WorkerExitReason::Topology),
            ("exit9", Some(9), WorkerExitReason::Failure),
            ("kill", None, WorkerExitReason::Killed),
        ];
        for (mode, code, reason) in cases {
            let fx = Fx::new();
            fx.mode(mode);
            let mgr = fx.manager(quick());
            mgr.start(fx.params()).unwrap();
            wait_for("estado crashed", || {
                mgr.view().state == WorkerPoolState::Crashed
            });
            let view = mgr.view();
            assert_eq!(view.exit_code, code, "{mode}");
            assert_eq!(view.reason, Some(reason), "{mode}");
            assert_eq!(read_record(&fx.app), RecordRead::Absent, "{mode}");
            // Sem laço de reinício: continua `crashed` e o worker rodou uma vez só.
            std::thread::sleep(Duration::from_millis(400));
            assert_eq!(mgr.view().state, WorkerPoolState::Crashed, "{mode}");
            assert_eq!(fx.runs(), 1, "{mode}");
        }
    }

    #[test]
    fn restart_is_accepted_from_crashed_and_second_start_is_already_in_state() {
        let fx = Fx::new();
        fx.mode("exit4");
        let mgr = fx.manager(quick());
        mgr.start(fx.params()).unwrap();
        wait_for("estado crashed", || {
            mgr.view().state == WorkerPoolState::Crashed
        });

        fx.mode("calm");
        assert!(
            !mgr.start(fx.params()).unwrap(),
            "Reiniciar é aceito em crashed"
        );
        let view = mgr.view();
        assert_eq!(view.state, WorkerPoolState::Running);
        assert!(view.exit_code.is_none() && view.reason.is_none());
        wait_for("segunda execução", || fx.runs() == 2);

        // Pool ativo: nenhum segundo pool.
        assert!(mgr.start(fx.params()).unwrap());
        std::thread::sleep(Duration::from_millis(200));
        assert_eq!(fx.runs(), 2);
        stop_quietly(&mgr);
    }

    #[test]
    fn memory_warning_is_highlighted_and_credentials_never_reach_the_lines() {
        let fx = Fx::new();
        fx.mode("exit1");
        let mgr = fx.manager(quick());
        mgr.start(fx.params()).unwrap();
        wait_for("estado crashed", || {
            mgr.view().state == WorkerPoolState::Crashed
        });
        let view = mgr.view();
        assert!(view.memory_warning);
        assert!(view
            .lines
            .iter()
            .any(|l| l.starts_with("Aviso: 8 conversores")));
        assert!(
            view.lines.iter().any(|l| l == "senha=***"),
            "{:?}",
            view.lines
        );
        let wire = serde_json::to_string(&view).unwrap();
        assert!(!wire.contains(PASSWORD));
    }

    #[test]
    fn only_the_last_200_stderr_lines_are_kept() {
        let fx = Fx::new();
        fx.mode("flood");
        let mgr = fx.manager(quick());
        mgr.start(fx.params()).unwrap();
        wait_for("estado crashed", || {
            mgr.view().state == WorkerPoolState::Crashed
        });
        let view = mgr.view();
        assert_eq!(view.lines.len(), MAX_LINES);
        assert_eq!(view.lines.last().unwrap(), "linha 249");
    }

    #[test]
    fn spawn_failure_leaves_the_pool_stopped() {
        let fx = Fx::new();
        let mgr = fx.manager(quick());
        let mut params = fx.params();
        params.uv = fx.bin.join("nao-existe");
        let err = mgr.start(params).unwrap_err();
        assert_eq!(err.code, ErrorCode::NotFound);
        assert_eq!(mgr.view().state, WorkerPoolState::Stopped);
        assert_eq!(read_record(&fx.app), RecordRead::Absent);
    }

    // M8: o SIGKILL só sai depois de conferir os membros do grupo.
    struct Overlay {
        distrust: AtomicBool,
        list_fails: bool,
    }

    impl ProcInspector for Overlay {
        fn group_members(&self, pgid: i32) -> Option<Vec<i32>> {
            if self.distrust.load(Ordering::SeqCst) && self.list_fails {
                return None;
            }
            SystemProc.group_members(pgid)
        }
        fn start_time(&self, pid: i32) -> Option<u64> {
            if self.distrust.load(Ordering::SeqCst) {
                return Some(1); // começou antes de `startedAt`: não é nosso
            }
            SystemProc.start_time(pid)
        }
        fn group_of(&self, pid: i32) -> Option<i32> {
            SystemProc.group_of(pid)
        }
        fn exe_path(&self, pid: i32) -> Option<PathBuf> {
            SystemProc.exe_path(pid)
        }
        fn args(&self, pid: i32) -> Option<Vec<String>> {
            SystemProc.args(pid)
        }
    }

    fn unconfirmed_group_is_never_killed(list_fails: bool) {
        let fx = Fx::new();
        fx.mode("stubborn");
        let overlay = Arc::new(Overlay {
            distrust: AtomicBool::new(false),
            list_fails,
        });
        let mgr = PoolManager::with_parts(
            Some(fx.app.clone()),
            overlay.clone(),
            PoolTimings {
                term_wait: Duration::from_millis(400),
                kill_wait: Duration::from_secs(2),
            },
            WorkerPool::stopped(3, 1234),
        );
        mgr.start(fx.params()).unwrap();
        let (_, pgid) = mgr.live_ids().unwrap();
        wait_for("o worker falso rodou", || fx.runs() == 1);
        std::thread::sleep(Duration::from_millis(300));

        overlay.distrust.store(true, Ordering::SeqCst);
        let mut forced = false;
        mgr.stop(&mut || forced = true).unwrap();
        assert!(!forced, "sem conferência, nenhum SIGKILL");
        assert!(!group_gone(pgid), "o grupo não deveria ter sido encerrado");
        let view = mgr.view();
        assert_eq!(view.state, WorkerPoolState::Stopped);
        assert!(view.lines.iter().any(|l| l == NOT_VERIFIED_NOTICE));
        assert_eq!(read_record(&fx.app), RecordRead::Absent);

        // Limpeza do teste (o app não o faria).
        signal_group(pgid, libc::SIGKILL);
        wait_for("grupo do teste encerrado", || group_gone(pgid));
    }

    #[test]
    fn group_with_a_member_older_than_the_record_gets_no_sigkill() {
        unconfirmed_group_is_never_killed(false);
    }

    #[test]
    fn group_that_cannot_be_listed_gets_no_sigkill() {
        unconfirmed_group_is_never_killed(true);
    }

    // 8.2.4: órfão.
    #[derive(Default)]
    struct FakeProc {
        start: HashMap<i32, u64>,
        group: HashMap<i32, i32>,
        exe: HashMap<i32, PathBuf>,
        args: HashMap<i32, Vec<String>>,
        members: HashMap<i32, Vec<i32>>,
    }

    impl ProcInspector for FakeProc {
        fn group_members(&self, pgid: i32) -> Option<Vec<i32>> {
            self.members.get(&pgid).cloned()
        }
        fn start_time(&self, pid: i32) -> Option<u64> {
            self.start.get(&pid).copied()
        }
        fn group_of(&self, pid: i32) -> Option<i32> {
            self.group.get(&pid).copied()
        }
        fn exe_path(&self, pid: i32) -> Option<PathBuf> {
            self.exe.get(&pid).cloned()
        }
        fn args(&self, pid: i32) -> Option<Vec<String>> {
            self.args.get(&pid).cloned()
        }
    }

    const STARTED: u64 = 1_700_000_000;

    /// "Worker" real (um `sleep` em grupo próprio) que o teste colhe numa thread; o `FakeProc`
    /// descreve-o como o `uv` do registro.
    struct Orphan {
        pid: i32,
        reaper: Option<thread::JoinHandle<()>>,
        uv: PathBuf,
        repo: String,
    }

    impl Orphan {
        fn spawn(fx: &Fx) -> Orphan {
            let mut child: Child = Command::new("/bin/sleep")
                .arg("60")
                .stdin(Stdio::null())
                .process_group(0)
                .spawn()
                .unwrap();
            let pid = child.id() as i32;
            let reaper = thread::spawn(move || {
                let _ = child.wait();
            });
            Orphan {
                pid,
                reaper: Some(reaper),
                uv: fx.bin.join("uv"),
                repo: fx.repo.to_string_lossy().into_owned(),
            }
        }

        fn proc_ok(&self) -> FakeProc {
            let mut p = FakeProc::default();
            p.start.insert(self.pid, STARTED);
            p.group.insert(self.pid, self.pid);
            p.exe.insert(self.pid, self.uv.clone());
            p.args.insert(
                self.pid,
                ["uv", "run", "--no-sync", "artigo2md", "worker"]
                    .map(String::from)
                    .to_vec(),
            );
            p.members.insert(self.pid, vec![self.pid]);
            p
        }

        fn record(&self) -> WorkerPoolRecord {
            WorkerPoolRecord {
                schema_version: 1,
                pid: self.pid,
                pgid: self.pid,
                started_at: STARTED,
                repo_path: self.repo.clone(),
            }
        }

        fn alive(&self) -> bool {
            !pid_gone(self.pid)
        }
    }

    impl Drop for Orphan {
        fn drop(&mut self) {
            signal_group(self.pid, libc::SIGKILL);
            if let Some(h) = self.reaper.take() {
                let _ = h.join();
            }
        }
    }

    fn orphan_manager(fx: &Fx, proc_: FakeProc) -> PoolManager {
        PoolManager::with_parts(
            Some(fx.app.clone()),
            Arc::new(proc_),
            quick(),
            WorkerPool::stopped(3, 1234),
        )
    }

    #[test]
    fn verified_orphan_is_offered_and_stopped_with_the_calm_stop() {
        let fx = Fx::new();
        let orphan = Orphan::spawn(&fx);
        write_record(&fx.app, &orphan.record()).unwrap();
        let mgr = orphan_manager(&fx, orphan.proc_ok());

        assert_eq!(
            mgr.detect_previous(Some(&orphan.repo), Some(&orphan.uv)),
            DetectOutcome::Orphan
        );
        let view = mgr.view();
        assert_eq!(view.state, WorkerPoolState::OrphanFound);
        assert!(!view.adopted);
        assert!(!mgr.is_active(), "orphanFound ainda não é um pool nosso");

        let view = mgr.stop_previous(&mut || {}).unwrap();
        assert_eq!(view.state, WorkerPoolState::Stopped);
        assert!(!orphan.alive());
        assert_eq!(read_record(&fx.app), RecordRead::Absent);
    }

    /// `FakeProc` que o teste altera depois de o gerente já tê-lo recebido.
    struct SharedProc(Arc<Mutex<FakeProc>>);

    impl ProcInspector for SharedProc {
        fn group_members(&self, pgid: i32) -> Option<Vec<i32>> {
            self.0.lock().unwrap().group_members(pgid)
        }
        fn start_time(&self, pid: i32) -> Option<u64> {
            self.0.lock().unwrap().start_time(pid)
        }
        fn group_of(&self, pid: i32) -> Option<i32> {
            self.0.lock().unwrap().group_of(pid)
        }
        fn exe_path(&self, pid: i32) -> Option<PathBuf> {
            self.0.lock().unwrap().exe_path(pid)
        }
        fn args(&self, pid: i32) -> Option<Vec<String>> {
            self.0.lock().unwrap().args(pid)
        }
    }

    fn shared_manager(fx: &Fx, proc_: FakeProc) -> (PoolManager, Arc<Mutex<FakeProc>>) {
        let shared = Arc::new(Mutex::new(proc_));
        let mgr = PoolManager::with_parts(
            Some(fx.app.clone()),
            Arc::new(SharedProc(Arc::clone(&shared))),
            quick(),
            WorkerPool::stopped(3, 1234),
        );
        (mgr, shared)
    }

    #[test]
    fn kept_orphan_is_adopted_and_becomes_crashed_unknown_when_it_vanishes() {
        let fx = Fx::new();
        let orphan = Orphan::spawn(&fx);
        write_record(&fx.app, &orphan.record()).unwrap();
        let (mgr, proc_) = shared_manager(&fx, orphan.proc_ok());
        mgr.detect_previous(Some(&orphan.repo), Some(&orphan.uv));

        let view = mgr.keep_previous().unwrap();
        assert_eq!(view.state, WorkerPoolState::Running);
        assert!(view.adopted);
        assert!(mgr.is_active());
        mgr.poll_adopted();
        assert_eq!(mgr.view().state, WorkerPoolState::Running);

        // O grupo some: na próxima rodada o pool adotado vira `crashed`, motivo `unknown`.
        proc_.lock().unwrap().members.insert(orphan.pid, vec![]);
        mgr.poll_adopted();
        let view = mgr.view();
        assert_eq!(view.state, WorkerPoolState::Crashed);
        assert_eq!(view.reason, Some(WorkerExitReason::Unknown));
        assert!(view.exit_code.is_none());
        assert_eq!(read_record(&fx.app), RecordRead::Absent);
    }

    #[test]
    fn reused_pid_is_discarded_and_the_other_process_is_never_signaled() {
        let fx = Fx::new();
        let orphan = Orphan::spawn(&fx);
        write_record(&fx.app, &orphan.record()).unwrap();
        let mut p = orphan.proc_ok();
        p.start.insert(orphan.pid, STARTED + 500); // outro horário de início: PID reutilizado
        let mgr = orphan_manager(&fx, p);
        assert_eq!(
            mgr.detect_previous(Some(&orphan.repo), Some(&orphan.uv)),
            DetectOutcome::Nothing
        );
        assert_eq!(mgr.view().state, WorkerPoolState::Stopped);
        assert_eq!(read_record(&fx.app), RecordRead::Absent);
        assert!(orphan.alive());
    }

    #[test]
    fn unconfirmed_orphan_gets_no_signal_and_a_warning() {
        type Tweak = fn(&mut FakeProc, i32);
        let tweaks: [(&str, Tweak); 5] = [
            ("executável diferente", |p, pid| {
                p.exe.insert(pid, PathBuf::from("/bin/sleep"));
            }),
            ("sem argumentos do worker", |p, pid| {
                p.args
                    .insert(pid, vec!["uv".into(), "run".into(), "other-thing".into()]);
            }),
            ("grupo diferente", |p, pid| {
                p.group.insert(pid, pid + 1);
            }),
            ("membro anterior ao registro", |p, pid| {
                p.members.insert(pid, vec![pid, pid + 7]);
                p.start.insert(pid + 7, STARTED - 10);
            }),
            ("sem argumentos legíveis", |p, pid| {
                p.args.remove(&pid);
            }),
        ];
        for (name, tweak) in tweaks {
            let fx = Fx::new();
            let orphan = Orphan::spawn(&fx);
            write_record(&fx.app, &orphan.record()).unwrap();
            let mut p = orphan.proc_ok();
            tweak(&mut p, orphan.pid);
            let mgr = orphan_manager(&fx, p);
            assert_eq!(
                mgr.detect_previous(Some(&orphan.repo), Some(&orphan.uv)),
                DetectOutcome::Unverified,
                "{name}"
            );
            let view = mgr.view();
            assert_eq!(view.state, WorkerPoolState::Stopped, "{name}");
            assert!(
                view.lines.iter().any(|l| l == NOT_VERIFIED_NOTICE),
                "{name}"
            );
            assert_eq!(read_record(&fx.app), RecordRead::Absent, "{name}");
            assert!(orphan.alive(), "{name}: nenhum sinal podia ser enviado");
        }
    }

    #[test]
    fn orphan_is_reverified_right_before_the_signal() {
        let fx = Fx::new();
        let orphan = Orphan::spawn(&fx);
        write_record(&fx.app, &orphan.record()).unwrap();
        let (mgr, proc_) = shared_manager(&fx, orphan.proc_ok());
        mgr.detect_previous(Some(&orphan.repo), Some(&orphan.uv));
        assert_eq!(mgr.view().state, WorkerPoolState::OrphanFound);

        // Entre a abertura e o clique em "Parar" o PID passou a ser de outro processo.
        proc_.lock().unwrap().start.insert(orphan.pid, STARTED + 99);
        let view = mgr.stop_previous(&mut || {}).unwrap();
        assert_eq!(view.state, WorkerPoolState::Stopped);
        assert!(
            orphan.alive(),
            "nenhum sinal ao processo que não é mais o worker"
        );
        assert_eq!(read_record(&fx.app), RecordRead::Absent);
    }

    #[test]
    fn orphan_that_stops_matching_after_adoption_is_not_signaled_by_stop() {
        let fx = Fx::new();
        let orphan = Orphan::spawn(&fx);
        write_record(&fx.app, &orphan.record()).unwrap();
        let (mgr, proc_) = shared_manager(&fx, orphan.proc_ok());
        mgr.detect_previous(Some(&orphan.repo), Some(&orphan.uv));
        mgr.keep_previous().unwrap();

        proc_
            .lock()
            .unwrap()
            .exe
            .insert(orphan.pid, PathBuf::from("/bin/sleep"));
        mgr.stop(&mut || {}).unwrap();
        let view = mgr.view();
        assert_eq!(view.state, WorkerPoolState::Stopped);
        assert!(view.lines.iter().any(|l| l == NOT_VERIFIED_NOTICE));
        assert!(orphan.alive());
    }

    #[test]
    fn record_for_another_repo_is_discarded_without_touching_the_process() {
        let fx = Fx::new();
        let orphan = Orphan::spawn(&fx);
        write_record(&fx.app, &orphan.record()).unwrap();
        let mgr = orphan_manager(&fx, orphan.proc_ok());
        let outcome = mgr.detect_previous(Some("/outro/repositorio"), Some(&orphan.uv));
        assert_eq!(outcome, DetectOutcome::Unverified);
        assert_eq!(mgr.view().state, WorkerPoolState::Stopped);
        assert_eq!(read_record(&fx.app), RecordRead::Absent);
        assert!(orphan.alive());
    }

    #[test]
    fn user_worker_without_a_record_is_never_touched() {
        let fx = Fx::new();
        let orphan = Orphan::spawn(&fx); // um worker "do usuário": nenhum registro
        let mgr = orphan_manager(&fx, orphan.proc_ok());
        assert_eq!(
            mgr.detect_previous(Some(&orphan.repo), Some(&orphan.uv)),
            DetectOutcome::Nothing
        );
        assert_eq!(mgr.view().state, WorkerPoolState::Stopped);
        assert!(orphan.alive());
    }

    #[test]
    fn edited_record_is_rejected() {
        let fx = Fx::new();
        let orphan = Orphan::spawn(&fx);
        let mgr = orphan_manager(&fx, orphan.proc_ok());
        let path = fx.app.join(RECORD_FILE);
        fs::create_dir_all(&fx.app).unwrap();

        let cases: [(&str, String, u32); 4] = [
            (
                "campo desconhecido",
                format!(
                    "{{\"schemaVersion\":1,\"pid\":{p},\"pgid\":{p},\"startedAt\":{s},\"repoPath\":\"{r}\",\"extra\":1}}",
                    p = orphan.pid,
                    s = STARTED,
                    r = orphan.repo
                ),
                0o600,
            ),
            ("não é JSON", "{".to_string(), 0o600),
            (
                "esquema de outra versão",
                format!(
                    "{{\"schemaVersion\":2,\"pid\":{p},\"pgid\":{p},\"startedAt\":{s},\"repoPath\":\"{r}\"}}",
                    p = orphan.pid,
                    s = STARTED,
                    r = orphan.repo
                ),
                0o600,
            ),
            (
                "permissão aberta",
                serde_json::to_string(&orphan.record()).unwrap(),
                0o644,
            ),
        ];
        for (name, text, mode) in cases {
            fs::write(&path, text).unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(mode)).unwrap();
            assert_eq!(
                mgr.detect_previous(Some(&orphan.repo), Some(&orphan.uv)),
                DetectOutcome::Nothing,
                "{name}"
            );
            assert_eq!(
                read_record(&fx.app),
                RecordRead::Absent,
                "{name}: registro descartado"
            );
            assert!(orphan.alive(), "{name}");
        }
    }

    #[test]
    fn resolve_previous_is_valid_only_with_an_orphan() {
        let fx = Fx::new();
        let mgr = fx.manager(quick());
        assert!(mgr.keep_previous().is_err());
        assert!(mgr.stop_previous(&mut || {}).is_err());
    }

    #[test]
    fn translate_exit_covers_signal_and_timeout() {
        let by_signal = ExecOutcome {
            code: None,
            signal: Some(9),
            timed_out: false,
        };
        assert_eq!(translate_exit(&by_signal), (None, WorkerExitReason::Killed));
        let timed_out = ExecOutcome {
            code: None,
            signal: None,
            timed_out: true,
        };
        assert_eq!(
            translate_exit(&timed_out),
            (None, WorkerExitReason::Failure)
        );
    }
}
