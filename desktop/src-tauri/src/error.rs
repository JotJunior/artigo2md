//! Erros do núcleo Rust. Para a interface o erro é sempre `{ code, detail: null }`:
//! o texto técnico (que pode ter caminho absoluto) fica só no log local.

use std::io;

use serde::ser::SerializeStruct;
use serde::{Serialize, Serializer};
use thiserror::Error;

use crate::stack::secret::Masked;

/// Códigos estáveis de erro (contracts/commands.md, "Erros"), serializados em camelCase.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum ErrorCode {
    NoFolderOpen,
    FolderUnavailable,
    PermissionDenied,
    NotFound,
    OutsideFolder,
    NotMarkdown,
    NotText,
    UnsupportedFormat,
    TooLarge,
    InvalidSettings,
    Io,
    // Códigos da infraestrutura (contracts/tauri-commands.md, "Error Responses").
    OperationInProgress,
    AnotherInstance,
    DockerNotReady,
    RepoInvalid,
    DataDirInvalid,
    ImagesMissing,
    ConfirmationRequired,
    InsufficientDisk,
    InvalidParams,
    DockerUnavailable,
    PortInUse,
    ServiceUnhealthy,
    StopFailed,
    ScriptFailed,
    ModelsMissing,
    StackNotRunning,
    WorkerExited,
    DownloadFailed,
    InvalidStackSettings,
}

impl ErrorCode {
    /// Todos os códigos, na ordem de `ERROR_CODES` em `desktop/src/lib/types.ts`.
    pub const ALL: [ErrorCode; 30] = [
        ErrorCode::NoFolderOpen,
        ErrorCode::FolderUnavailable,
        ErrorCode::PermissionDenied,
        ErrorCode::NotFound,
        ErrorCode::OutsideFolder,
        ErrorCode::NotMarkdown,
        ErrorCode::NotText,
        ErrorCode::UnsupportedFormat,
        ErrorCode::TooLarge,
        ErrorCode::InvalidSettings,
        ErrorCode::Io,
        ErrorCode::OperationInProgress,
        ErrorCode::AnotherInstance,
        ErrorCode::DockerNotReady,
        ErrorCode::RepoInvalid,
        ErrorCode::DataDirInvalid,
        ErrorCode::ImagesMissing,
        ErrorCode::ConfirmationRequired,
        ErrorCode::InsufficientDisk,
        ErrorCode::InvalidParams,
        ErrorCode::DockerUnavailable,
        ErrorCode::PortInUse,
        ErrorCode::ServiceUnhealthy,
        ErrorCode::StopFailed,
        ErrorCode::ScriptFailed,
        ErrorCode::ModelsMissing,
        ErrorCode::StackNotRunning,
        ErrorCode::WorkerExited,
        ErrorCode::DownloadFailed,
        ErrorCode::InvalidStackSettings,
    ];

    /// Nome camelCase do código, para o log.
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorCode::NoFolderOpen => "noFolderOpen",
            ErrorCode::FolderUnavailable => "folderUnavailable",
            ErrorCode::PermissionDenied => "permissionDenied",
            ErrorCode::NotFound => "notFound",
            ErrorCode::OutsideFolder => "outsideFolder",
            ErrorCode::NotMarkdown => "notMarkdown",
            ErrorCode::NotText => "notText",
            ErrorCode::UnsupportedFormat => "unsupportedFormat",
            ErrorCode::TooLarge => "tooLarge",
            ErrorCode::InvalidSettings => "invalidSettings",
            ErrorCode::Io => "io",
            ErrorCode::OperationInProgress => "operationInProgress",
            ErrorCode::AnotherInstance => "anotherInstance",
            ErrorCode::DockerNotReady => "dockerNotReady",
            ErrorCode::RepoInvalid => "repoInvalid",
            ErrorCode::DataDirInvalid => "dataDirInvalid",
            ErrorCode::ImagesMissing => "imagesMissing",
            ErrorCode::ConfirmationRequired => "confirmationRequired",
            ErrorCode::InsufficientDisk => "insufficientDisk",
            ErrorCode::InvalidParams => "invalidParams",
            ErrorCode::DockerUnavailable => "dockerUnavailable",
            ErrorCode::PortInUse => "portInUse",
            ErrorCode::ServiceUnhealthy => "serviceUnhealthy",
            ErrorCode::StopFailed => "stopFailed",
            ErrorCode::ScriptFailed => "scriptFailed",
            ErrorCode::ModelsMissing => "modelsMissing",
            ErrorCode::StackNotRunning => "stackNotRunning",
            ErrorCode::WorkerExited => "workerExited",
            ErrorCode::DownloadFailed => "downloadFailed",
            ErrorCode::InvalidStackSettings => "invalidStackSettings",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[error("{}: {detail}", code.as_str())]
pub struct AppError {
    pub code: ErrorCode,
    detail: String,
}

impl AppError {
    pub fn new(code: ErrorCode, detail: impl Into<String>) -> Self {
        Self {
            code,
            detail: detail.into(),
        }
    }

    /// Erro cujo texto técnico já passou por `Masker::mask` (plan.md M10): é o único construtor
    /// que o módulo `stack/` deve usar com texto vindo de processo externo.
    pub fn from_masked(code: ErrorCode, detail: &Masked) -> Self {
        Self::new(code, detail.as_str())
    }

    /// Texto técnico: só para o log, nunca para a interface.
    pub fn detail(&self) -> &str {
        &self.detail
    }
}

impl Serialize for AppError {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut state = serializer.serialize_struct("AppError", 2)?;
        state.serialize_field("code", &self.code)?;
        // `detail` é sempre null na interface (gate owasp-security L2).
        state.serialize_field("detail", &Option::<()>::None)?;
        state.end()
    }
}

impl From<io::Error> for AppError {
    fn from(e: io::Error) -> Self {
        let code = match e.kind() {
            io::ErrorKind::NotFound | io::ErrorKind::NotADirectory => ErrorCode::NotFound,
            io::ErrorKind::PermissionDenied => ErrorCode::PermissionDenied,
            _ => ErrorCode::Io,
        };
        AppError::new(code, e.to_string())
    }
}

pub type AppResult<T> = Result<T, AppError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn serializes_with_null_detail() {
        let err = AppError::new(ErrorCode::OutsideFolder, "/Users/x/segredo");
        let json = serde_json::to_value(&err).unwrap();
        assert_eq!(
            json,
            serde_json::json!({"code": "outsideFolder", "detail": null})
        );
        assert!(!json.to_string().contains("/Users/x"));
    }

    #[test]
    fn maps_io_kinds() {
        let nf: AppError = io::Error::from(io::ErrorKind::NotFound).into();
        assert_eq!(nf.code, ErrorCode::NotFound);
        let pd: AppError = io::Error::from(io::ErrorKind::PermissionDenied).into();
        assert_eq!(pd.code, ErrorCode::PermissionDenied);
        let other: AppError = io::Error::from(io::ErrorKind::TimedOut).into();
        assert_eq!(other.code, ErrorCode::Io);
    }

    #[test]
    fn all_codes_serialize_in_camel_case_with_null_detail() {
        for code in ErrorCode::ALL {
            let err = AppError::new(code, "texto técnico /Users/x/segredo");
            let json = serde_json::to_value(&err).unwrap();
            assert_eq!(json["code"], serde_json::json!(code.as_str()));
            assert!(json["detail"].is_null(), "{}", code.as_str());
            assert_eq!(json.as_object().unwrap().len(), 2);
            assert!(!json.to_string().contains("/Users/x"));
            // camelCase: começa em minúscula e não tem separador.
            let name = code.as_str();
            assert!(name.chars().next().unwrap().is_ascii_lowercase());
            assert!(name.chars().all(|c| c.is_ascii_alphanumeric()));
        }
    }

    #[test]
    fn codes_are_unique() {
        let mut names: Vec<&str> = ErrorCode::ALL.iter().map(|c| c.as_str()).collect();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), ErrorCode::ALL.len());
    }

    /// Paridade com `ERROR_CODES` de `desktop/src/lib/types.ts` (a fonte do lado da interface).
    #[test]
    fn code_set_matches_error_codes_in_types_ts() {
        let ts = include_str!("../../src/lib/types.ts");
        let start = ts
            .find("export const ERROR_CODES = [")
            .expect("ERROR_CODES");
        let body = &ts[start..];
        let body = &body[body.find('[').unwrap() + 1..];
        let body = &body[..body.find("] as const").expect("fim de ERROR_CODES")];
        let ts_codes: Vec<String> = body
            .split(',')
            .map(|piece| piece.trim().trim_matches('"').to_string())
            .filter(|piece| !piece.is_empty())
            .collect();
        let rust_codes: Vec<String> = ErrorCode::ALL
            .iter()
            .map(|c| c.as_str().to_string())
            .collect();
        assert_eq!(rust_codes, ts_codes);
    }
}
