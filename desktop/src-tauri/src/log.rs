//! Log local do lado Rust (plan.md, "Log do lado Rust"). O texto técnico dos erros (que pode
//! conter caminho absoluto) vai só para este arquivo, nunca para a interface nem para a rede.
//! Formato: `<segundos Unix> <código> <detalhe>`. Rotação: acima de `MAX_LOG_BYTES` o arquivo
//! vira `artigo2md.log.1` (substitui o anterior): no máximo 2 arquivos.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::limits::MAX_LOG_BYTES;

const LOG_NAME: &str = "artigo2md.log";
const ROTATED_NAME: &str = "artigo2md.log.1";

pub struct Logger {
    dir: PathBuf,
    max_bytes: u64,
}

impl Logger {
    pub fn new(dir: PathBuf) -> Self {
        Self::with_max_bytes(dir, MAX_LOG_BYTES)
    }

    pub fn with_max_bytes(dir: PathBuf, max_bytes: u64) -> Self {
        Self { dir, max_bytes }
    }

    /// Registra uma linha. Falhas ao gravar são ignoradas de propósito: o log nunca derruba um
    /// comando nem vira mensagem para a interface.
    pub fn error(&self, code: &str, detail: &str) {
        let _ = self.try_write(code, detail);
    }

    fn try_write(&self, code: &str, detail: &str) -> std::io::Result<()> {
        fs::create_dir_all(&self.dir)?;
        let path = self.dir.join(LOG_NAME);
        if fs::metadata(&path)
            .map(|m| m.len() > self.max_bytes)
            .unwrap_or(false)
        {
            fs::rename(&path, self.dir.join(ROTATED_NAME))?;
        }
        let secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let detail = detail.replace(['\n', '\r'], " ");
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .mode(0o600)
            .open(&path)?;
        writeln!(file, "{secs} {code} {detail}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn writes_one_line_per_event_with_private_permissions() {
        let tmp = tempfile::tempdir().unwrap();
        let log = Logger::new(tmp.path().join("logs"));
        log.error("notFound", "linha1\nlinha2 /Users/x/segredo.md");
        log.error("io", "outra");
        let text = fs::read_to_string(tmp.path().join("logs/artigo2md.log")).unwrap();
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(lines.len(), 2);
        assert!(lines[0].contains(" notFound linha1 linha2 /Users/x/segredo.md"));
        let mode = fs::metadata(tmp.path().join("logs/artigo2md.log"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o600);
    }

    #[test]
    fn rotates_keeping_at_most_two_files() {
        let tmp = tempfile::tempdir().unwrap();
        let log = Logger::with_max_bytes(tmp.path().to_path_buf(), 100);
        for i in 0..40 {
            log.error("io", &format!("evento {i} com texto para encher o arquivo"));
        }
        let names: Vec<String> = fs::read_dir(tmp.path())
            .unwrap()
            .map(|e| e.unwrap().file_name().into_string().unwrap())
            .collect();
        assert!(names.contains(&"artigo2md.log".to_string()));
        assert!(names.contains(&"artigo2md.log.1".to_string()));
        assert_eq!(names.len(), 2);
    }

    #[test]
    fn failure_to_write_is_ignored() {
        let tmp = tempfile::tempdir().unwrap();
        let blocker = tmp.path().join("arquivo");
        fs::write(&blocker, "x").unwrap();
        // O "diretório" de log é um arquivo comum: criar a pasta falha, e isso não pode panicar.
        Logger::new(blocker.join("logs")).error("io", "x");
    }
}
