//! Tipos compartilhados da lista da primeira execução (data-model.md, `FirstRunStep`): as etapas
//! "imagens da infraestrutura" (`images.rs`) e "modelos de conversão" (`models.rs`).
//!
//! O estado nunca é persistido: é recalculado a cada verificação (`docker image inspect`,
//! `artigo2md models status --json`). Um download interrompido aparece como `pending` ou `failed`,
//! nunca como `done` (FR-030, FR-034).

use std::ffi::CString;
use std::os::unix::ffi::OsStrExt;
use std::path::Path;

use serde::Serialize;

use crate::error::AppError;
use crate::stack::clock;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum StepKind {
    Images,
    Models,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum StepState {
    Pending,
    Downloading,
    Done,
    Failed,
}

/// Motivo da falha de um download (`FirstRunStep.failure`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum DownloadFailure {
    NoNetwork,
    DiskFull,
    Interrupted,
    Other,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FirstRunStep {
    pub step: StepKind,
    pub state: StepState,
    pub downloaded_bytes: Option<u64>,
    pub total_bytes: Option<u64>,
    pub items_done: u32,
    pub items_total: u32,
    /// Hora do último progresso recebido; `None` antes do primeiro evento (sinal de atividade de
    /// FR-032 quando `total_bytes` é desconhecido).
    pub updated_at: Option<String>,
    pub failure: Option<DownloadFailure>,
    pub approx_size: String,
    pub location: String,
}

/// Tamanho aproximado das imagens: soma das imagens locais medidas (research Decision 8,
/// 701 MB + 456 MB + 339 MB, observado em 2026-11-26).
pub const IMAGES_APPROX_TEXT: &str = "cerca de 1,5 GB";
/// Mesmo valor em bytes, usado na checagem de espaço (FR-033).
pub const IMAGES_APPROX_BYTES: u64 = 1_500_000_000;
pub const IMAGES_LOCATION: &str = "disco do runtime Docker";
/// Tamanho aproximado dos modelos (`README.md:34`, research Decision 9).
pub const MODELS_APPROX_TEXT: &str = "cerca de 3 GB livres";
pub const MODELS_LOCATION: &str = "cache do Hugging Face";

impl FirstRunStep {
    pub fn new(kind: StepKind) -> Self {
        let (approx, location) = match kind {
            StepKind::Images => (IMAGES_APPROX_TEXT, IMAGES_LOCATION),
            StepKind::Models => (MODELS_APPROX_TEXT, MODELS_LOCATION),
        };
        Self {
            step: kind,
            state: StepState::Pending,
            downloaded_bytes: None,
            total_bytes: None,
            items_done: 0,
            items_total: 0,
            updated_at: None,
            failure: None,
            approx_size: approx.to_string(),
            location: location.to_string(),
        }
    }

    /// Marca atividade agora (o campo `updatedAt` prova que o download não parou).
    pub fn touch(&mut self) {
        self.updated_at = Some(clock::now_iso8601());
    }

    pub fn finish_ok(&mut self) {
        self.state = StepState::Done;
        self.failure = None;
        self.touch();
    }

    pub fn finish_failed(&mut self, failure: DownloadFailure) {
        self.state = StepState::Failed;
        self.failure = Some(failure);
        self.touch();
    }
}

/// Diálogo nativo de confirmação (plan.md M16): o controle real fica no Rust; `confirmed` vindo da
/// interface é só UX. Os comandos Tauri implementam isto com `tauri-plugin-dialog`; os testes usam
/// uma implementação fixa.
pub trait Confirmer {
    fn confirm(&self, title: &str, message: &str) -> bool;
}

/// Falha de `prepare_images` e `prepare_models`: o código do IPC, o motivo traduzido, o que falta de
/// espaço (para a interface mostrar quanto falta) e a etapa no estado final (para o retrato).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PrepareError {
    pub error: AppError,
    pub failure: Option<DownloadFailure>,
    pub shortfall_bytes: Option<u64>,
    pub step: Option<Box<FirstRunStep>>,
}

impl From<AppError> for PrepareError {
    fn from(error: AppError) -> Self {
        Self {
            error,
            failure: None,
            shortfall_bytes: None,
            step: None,
        }
    }
}

/// Espaço livre, em bytes, do volume que contém `path` (`statvfs`); `None` se a chamada falhar.
pub fn free_bytes(path: &Path) -> Option<u64> {
    let c_path = CString::new(path.as_os_str().as_bytes()).ok()?;
    // SAFETY: `statvfs` é uma estrutura de inteiros, válida zerada; `c_path` termina em NUL e
    // `st` é um destino válido durante a chamada.
    let mut st: libc::statvfs = unsafe { std::mem::zeroed() };
    let rc = unsafe { libc::statvfs(c_path.as_ptr(), &mut st) };
    if rc != 0 {
        return None;
    }
    Some((st.f_bavail as u64).saturating_mul(st.f_frsize as u64))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_steps_are_pending_with_their_sources() {
        let images = FirstRunStep::new(StepKind::Images);
        assert_eq!(images.state, StepState::Pending);
        assert_eq!(images.approx_size, "cerca de 1,5 GB");
        assert_eq!(images.location, "disco do runtime Docker");
        let models = FirstRunStep::new(StepKind::Models);
        assert_eq!(models.approx_size, "cerca de 3 GB livres");
        assert!(models.updated_at.is_none() && models.failure.is_none());
    }

    #[test]
    fn serializes_in_camel_case() {
        let mut step = FirstRunStep::new(StepKind::Models);
        step.finish_failed(DownloadFailure::NoNetwork);
        let json = serde_json::to_value(&step).unwrap();
        assert_eq!(json["step"], "models");
        assert_eq!(json["state"], "failed");
        assert_eq!(json["failure"], "noNetwork");
        assert!(json["downloadedBytes"].is_null());
        assert!(json["updatedAt"].is_string());
        assert_eq!(json["itemsDone"], 0);
    }

    #[test]
    fn finish_ok_clears_a_previous_failure() {
        let mut step = FirstRunStep::new(StepKind::Images);
        step.finish_failed(DownloadFailure::Other);
        step.finish_ok();
        assert_eq!(step.state, StepState::Done);
        assert_eq!(step.failure, None);
    }

    #[test]
    fn free_bytes_reads_the_volume_and_fails_on_a_missing_path() {
        assert!(free_bytes(Path::new("/tmp")).unwrap() > 0);
        assert!(free_bytes(Path::new("/caminho/que/nao/existe")).is_none());
    }
}
