//! Erros do núcleo Rust. Para a interface o erro é sempre `{ code, detail: null }`:
//! o texto técnico (que pode ter caminho absoluto) fica só no log local.

use std::io;

use serde::ser::SerializeStruct;
use serde::{Serialize, Serializer};
use thiserror::Error;

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
}

impl ErrorCode {
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
}
