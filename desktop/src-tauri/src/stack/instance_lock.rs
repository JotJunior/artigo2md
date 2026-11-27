//! `lifecycle.lock` e exclusão de operações (spec FR-015; plan.md M17; research Decision 14).
//!
//! Entre instâncias: `flock(LOCK_EX | LOCK_NB)` em `lifecycle.lock`, na pasta de dados do app do
//! Tauri. Quem não obtém o lock fica em modo somente leitura (mostra o retrato, recusa ações com
//! `anotherInstance`). O lock é do sistema operacional e some com o processo.
//!
//! Dentro da instância: uma operação de ciclo de vida por vez (iniciar/parar a stack, iniciar/
//! parar workers e preparar imagens, que também usa o Compose), recusada com
//! `operationInProgress`. "Preparar modelos" tem exclusão própria: convive com uma operação de
//! stack, mas não com outra "Preparar modelos".

use std::fs::{self, File, OpenOptions};
use std::io;
use std::os::fd::AsRawFd;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use serde::{Deserialize, Serialize};

use crate::error::{AppError, AppResult, ErrorCode};

/// Nome do arquivo de lock dentro de `app_data_dir`.
pub const LOCK_FILE_NAME: &str = "lifecycle.lock";

/// Modo das pastas do app (`app_data_dir` e `app_config_dir`) e do arquivo de lock.
const PRIVATE_DIR_MODE: u32 = 0o700;
const LOCK_FILE_MODE: u32 = 0o600;

/// Garante que a pasta existe com modo `0700` (cria com esse modo; se já existia mais aberta,
/// restringe). Falha com `io`/`permissionDenied` se não for possível.
pub fn ensure_private_dir(path: &Path) -> AppResult<()> {
    fs::DirBuilder::new()
        .recursive(true)
        .mode(PRIVATE_DIR_MODE)
        .create(path)?;
    let meta = fs::metadata(path)?;
    if !meta.is_dir() {
        return Err(AppError::new(
            ErrorCode::Io,
            format!("{} não é uma pasta", path.display()),
        ));
    }
    if meta.mode() & 0o777 != PRIVATE_DIR_MODE {
        fs::set_permissions(path, fs::Permissions::from_mode(PRIVATE_DIR_MODE))?;
    }
    Ok(())
}

/// Posse do `lifecycle.lock`; o `flock` é liberado quando o valor é descartado (ou o processo sai).
#[derive(Debug)]
pub struct InstanceLock {
    _file: File,
}

/// Resultado de tentar obter o lock.
#[derive(Debug)]
pub enum LockOutcome {
    Acquired(InstanceLock),
    /// Outra instância detém o lock: modo somente leitura.
    Busy,
}

/// Abre `<data_dir>/lifecycle.lock` (`O_CLOEXEC`, `O_NOFOLLOW`, `0600`) e tenta o `flock`.
/// Cria `data_dir` com modo `0700` se preciso.
pub fn acquire(data_dir: &Path) -> AppResult<LockOutcome> {
    ensure_private_dir(data_dir)?;
    let path = data_dir.join(LOCK_FILE_NAME);
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(LOCK_FILE_MODE)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(&path)?;
    let meta = file.metadata()?;
    if !meta.is_file() {
        return Err(AppError::new(
            ErrorCode::Io,
            format!("{} não é arquivo regular", path.display()),
        ));
    }
    if meta.mode() & 0o777 != LOCK_FILE_MODE {
        file.set_permissions(fs::Permissions::from_mode(LOCK_FILE_MODE))?;
    }
    // SAFETY: o descritor pertence a `file`, que continua aberto durante a chamada.
    let rc = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
    if rc == 0 {
        return Ok(LockOutcome::Acquired(InstanceLock { _file: file }));
    }
    let err = io::Error::last_os_error();
    if err.raw_os_error() == Some(libc::EWOULDBLOCK) {
        return Ok(LockOutcome::Busy);
    }
    Err(err.into())
}

/// Operações de ciclo de vida (data-model.md, entidade Operation).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum OperationKind {
    StartStack,
    StopStack,
    StartWorkers,
    StopWorkers,
    PrepareImages,
    PrepareModels,
}

impl OperationKind {
    /// `prepareModels` não usa a stack: tem exclusão própria.
    fn is_models(self) -> bool {
        self == OperationKind::PrepareModels
    }
}

/// Reserva de uma operação em andamento; libera ao ser descartada. É `Send` e não prende
/// referência ao gate, então pode acompanhar a tarefa em segundo plano até o fim.
#[derive(Debug)]
pub struct OperationGuard {
    kind: OperationKind,
    flag: Arc<AtomicBool>,
}

impl OperationGuard {
    pub fn kind(&self) -> OperationKind {
        self.kind
    }
}

impl Drop for OperationGuard {
    fn drop(&mut self) {
        self.flag.store(false, Ordering::SeqCst);
    }
}

/// Exclusão de operações da instância, somada ao estado do `lifecycle.lock`.
#[derive(Debug)]
pub struct InstanceGate {
    lock: Option<InstanceLock>,
    stack_busy: Arc<AtomicBool>,
    models_busy: Arc<AtomicBool>,
}

impl InstanceGate {
    pub fn new(outcome: LockOutcome) -> Self {
        let lock = match outcome {
            LockOutcome::Acquired(lock) => Some(lock),
            LockOutcome::Busy => None,
        };
        Self {
            lock,
            stack_busy: Arc::new(AtomicBool::new(false)),
            models_busy: Arc::new(AtomicBool::new(false)),
        }
    }

    /// `true` quando outra instância detém o lock (o retrato fica `readOnly`).
    pub fn read_only(&self) -> bool {
        self.lock.is_none()
    }

    /// Reserva uma operação. Sem o lock: `anotherInstance`. Outra operação do mesmo grupo em
    /// andamento: `operationInProgress`.
    pub fn begin(&self, kind: OperationKind) -> AppResult<OperationGuard> {
        if self.read_only() {
            return Err(AppError::new(
                ErrorCode::AnotherInstance,
                "outra instância detém o lifecycle.lock",
            ));
        }
        let flag = if kind.is_models() {
            &self.models_busy
        } else {
            &self.stack_busy
        };
        flag.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .map_err(|_| {
                AppError::new(
                    ErrorCode::OperationInProgress,
                    "outra operação de ciclo de vida está em andamento",
                )
            })?;
        Ok(OperationGuard {
            kind,
            flag: Arc::clone(flag),
        })
    }

    /// Recusa (sem reservar nada) quando outra instância detém o lock (`anotherInstance`) ou há
    /// qualquer operação em andamento (`operationInProgress`): usado por quem muda configuração
    /// que as operações leem (pasta do repositório, pasta de dados, parâmetros).
    pub fn ensure_idle(&self) -> AppResult<()> {
        if self.read_only() {
            return Err(AppError::new(
                ErrorCode::AnotherInstance,
                "outra instância detém o lifecycle.lock",
            ));
        }
        if self.stack_operation_active() || self.models_operation_active() {
            return Err(AppError::new(
                ErrorCode::OperationInProgress,
                "há uma operação de ciclo de vida em andamento",
            ));
        }
        Ok(())
    }

    /// Há uma operação de stack (inclui preparar imagens) em andamento?
    pub fn stack_operation_active(&self) -> bool {
        self.stack_busy.load(Ordering::SeqCst)
    }

    /// Há um "Preparar modelos" em andamento?
    pub fn models_operation_active(&self) -> bool {
        self.models_busy.load(Ordering::SeqCst)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn acquired(dir: &Path) -> InstanceLock {
        match acquire(dir).unwrap() {
            LockOutcome::Acquired(lock) => lock,
            LockOutcome::Busy => panic!("o lock deveria estar livre"),
        }
    }

    /// Depois de largar o lock, outro `acquire` o obtém. Um processo filho que acabou de ser criado
    /// por outro teste (`fork` antes do `exec`) herda o descritor por alguns milissegundos e segura o
    /// `flock`; por isso a nova tentativa espera um pouco em vez de falhar à toa.
    fn acquired_eventually(dir: &Path) -> InstanceLock {
        for _ in 0..100 {
            if let LockOutcome::Acquired(lock) = acquire(dir).unwrap() {
                return lock;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        panic!("o lock não foi liberado");
    }

    fn mode_of(path: &Path) -> u32 {
        fs::metadata(path).unwrap().mode() & 0o777
    }

    #[test]
    fn second_attempt_sees_busy_until_the_first_is_dropped() {
        let tmp = tempfile::tempdir().unwrap();
        let data = tmp.path().join("dados");
        let first = acquired(&data);
        assert!(matches!(acquire(&data).unwrap(), LockOutcome::Busy));
        drop(first);
        let _again = acquired_eventually(&data);
    }

    #[test]
    fn creates_the_lock_file_and_directory_with_private_modes() {
        let tmp = tempfile::tempdir().unwrap();
        let data = tmp.path().join("a/b/dados");
        let _lock = acquired(&data);
        assert_eq!(mode_of(&data), 0o700);
        assert_eq!(mode_of(&data.join(LOCK_FILE_NAME)), 0o600);
    }

    #[test]
    fn tightens_existing_wider_modes() {
        let tmp = tempfile::tempdir().unwrap();
        let data = tmp.path().join("dados");
        fs::create_dir(&data).unwrap();
        fs::set_permissions(&data, fs::Permissions::from_mode(0o755)).unwrap();
        let lock_path = data.join(LOCK_FILE_NAME);
        fs::write(&lock_path, "").unwrap();
        fs::set_permissions(&lock_path, fs::Permissions::from_mode(0o666)).unwrap();
        let _lock = acquired(&data);
        assert_eq!(mode_of(&data), 0o700);
        assert_eq!(mode_of(&lock_path), 0o600);
    }

    #[test]
    fn ensure_private_dir_is_idempotent_and_rejects_a_regular_file() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("config");
        ensure_private_dir(&dir).unwrap();
        ensure_private_dir(&dir).unwrap();
        assert_eq!(mode_of(&dir), 0o700);
        let file = tmp.path().join("arquivo");
        fs::write(&file, "x").unwrap();
        assert!(ensure_private_dir(&file).is_err());
    }

    #[test]
    fn a_symlinked_lock_file_is_refused() {
        let tmp = tempfile::tempdir().unwrap();
        let data = tmp.path().join("dados");
        fs::create_dir(&data).unwrap();
        let victim = tmp.path().join("vitima");
        fs::write(&victim, "intacto").unwrap();
        std::os::unix::fs::symlink(&victim, data.join(LOCK_FILE_NAME)).unwrap();
        assert!(acquire(&data).is_err());
        assert_eq!(fs::read_to_string(&victim).unwrap(), "intacto");
    }

    #[test]
    fn lock_descriptor_is_not_inherited_by_children() {
        let tmp = tempfile::tempdir().unwrap();
        let data = tmp.path().join("dados");
        let lock = acquired(&data);
        // SAFETY: `fcntl(F_GETFD)` só lê as flags de um descritor válido de `lock`.
        let flags = unsafe { libc::fcntl(lock._file.as_raw_fd(), libc::F_GETFD) };
        assert!(flags >= 0);
        assert_ne!(flags & libc::FD_CLOEXEC, 0, "FD_CLOEXEC ausente");
    }

    fn gate(dir: &Path) -> InstanceGate {
        InstanceGate::new(acquire(dir).unwrap())
    }

    #[test]
    fn second_stack_operation_is_refused_with_operation_in_progress() {
        let tmp = tempfile::tempdir().unwrap();
        let gate = gate(&tmp.path().join("dados"));
        let first = gate.begin(OperationKind::StartStack).unwrap();
        assert!(gate.stack_operation_active());
        for kind in [
            OperationKind::StartStack,
            OperationKind::StopStack,
            OperationKind::StartWorkers,
            OperationKind::StopWorkers,
            OperationKind::PrepareImages,
        ] {
            let err = gate.begin(kind).unwrap_err();
            assert_eq!(err.code, ErrorCode::OperationInProgress, "{kind:?}");
        }
        drop(first);
        assert!(!gate.stack_operation_active());
        let _next = gate.begin(OperationKind::PrepareImages).unwrap();
    }

    #[test]
    fn prepare_models_coexists_with_a_stack_operation_but_not_with_itself() {
        let tmp = tempfile::tempdir().unwrap();
        let gate = gate(&tmp.path().join("dados"));
        let stack = gate.begin(OperationKind::StartStack).unwrap();
        let models = gate.begin(OperationKind::PrepareModels).unwrap();
        assert_eq!(models.kind(), OperationKind::PrepareModels);
        assert!(gate.models_operation_active());
        let err = gate.begin(OperationKind::PrepareModels).unwrap_err();
        assert_eq!(err.code, ErrorCode::OperationInProgress);
        // Parar a stack continua recusado enquanto a outra operação de stack roda, não por causa
        // dos modelos.
        assert_eq!(
            gate.begin(OperationKind::StopStack).unwrap_err().code,
            ErrorCode::OperationInProgress
        );
        drop(stack);
        let _stop = gate.begin(OperationKind::StopStack).unwrap();
        drop(models);
        let _models_again = gate.begin(OperationKind::PrepareModels).unwrap();
    }

    #[test]
    fn stack_operation_does_not_block_while_only_models_run() {
        let tmp = tempfile::tempdir().unwrap();
        let gate = gate(&tmp.path().join("dados"));
        let _models = gate.begin(OperationKind::PrepareModels).unwrap();
        assert!(!gate.stack_operation_active());
        let _stack = gate.begin(OperationKind::StopStack).unwrap();
    }

    #[test]
    fn second_instance_is_read_only_and_every_action_is_refused() {
        let tmp = tempfile::tempdir().unwrap();
        let data = tmp.path().join("dados");
        let first = gate(&data);
        let second = gate(&data);
        assert!(!first.read_only());
        assert!(second.read_only());
        for kind in [
            OperationKind::StartStack,
            OperationKind::StopStack,
            OperationKind::StartWorkers,
            OperationKind::StopWorkers,
            OperationKind::PrepareImages,
            OperationKind::PrepareModels,
        ] {
            assert_eq!(
                second.begin(kind).unwrap_err().code,
                ErrorCode::AnotherInstance,
                "{kind:?}"
            );
        }
        // Quando a primeira instância sai, uma nova obtém o lock.
        drop(first);
        let _next = acquired_eventually(&data);
    }

    #[test]
    fn guard_is_send_and_the_kind_serializes_in_camel_case() {
        fn assert_send<T: Send + 'static>() {}
        assert_send::<OperationGuard>();
        assert_eq!(
            serde_json::to_string(&OperationKind::PrepareModels).unwrap(),
            "\"prepareModels\""
        );
        assert_eq!(
            serde_json::from_str::<OperationKind>("\"startWorkers\"").unwrap(),
            OperationKind::StartWorkers
        );
    }
}
