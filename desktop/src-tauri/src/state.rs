//! Estado do app: a raiz da pasta aberta fica só no Rust (Decision 6).

use std::path::PathBuf;
use std::sync::Mutex;

use crate::error::{AppError, AppResult, ErrorCode};
use crate::log::Logger;
use crate::settings::SettingsStore;

pub struct AppState {
    /// Raiz canonizada da pasta aberta; `None` enquanto nenhuma pasta foi aberta.
    pub root: Mutex<Option<PathBuf>>,
    pub settings: SettingsStore,
    pub log: Logger,
}

impl AppState {
    pub fn new(config_dir: PathBuf, log_dir: PathBuf) -> Self {
        Self {
            root: Mutex::new(None),
            settings: SettingsStore::new(config_dir),
            log: Logger::new(log_dir),
        }
    }

    pub fn set_root(&self, root: Option<PathBuf>) {
        *self.root.lock().unwrap_or_else(|p| p.into_inner()) = root;
    }

    /// Raiz corrente ou `noFolderOpen`.
    pub fn current_root(&self) -> AppResult<PathBuf> {
        self.root
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .clone()
            .ok_or_else(|| AppError::new(ErrorCode::NoFolderOpen, "nenhuma pasta aberta"))
    }

    /// Registra o texto técnico no log local e devolve o resultado inalterado.
    pub fn logged<T>(&self, result: AppResult<T>) -> AppResult<T> {
        if let Err(e) = &result {
            self.log.error(e.code.as_str(), e.detail());
        }
        result
    }
}
