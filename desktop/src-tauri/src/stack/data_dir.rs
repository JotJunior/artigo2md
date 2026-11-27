//! Pasta de dados do app (spec FR-012; research Decision 6; plan.md M14).
//!
//! Proposta `~/artigo2md-dados`, criada com modo `0700` quando ausente. Antes de iniciar a stack
//! ou os workers a pasta precisa existir, ser diretório, ser gravável (criação e remoção de um
//! arquivo temporário) e ser igual ao seu caminho canônico, a mesma exigência do `up.sh`, que
//! compara com `pwd -P` (`stack/up.sh:128-139`). Pastas amplas demais são recusadas (`forbidden`).
//! O mesmo caminho vai ao `.env` (`ARTIGO2MD_DATA_DIR`) e ao `--data-dir` do worker.

use std::fs::{self, OpenOptions};
use std::io;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Component, Path, PathBuf};

use serde::Serialize;

use crate::stack::secret::random_hex;

/// Nome da pasta padrão dentro da pasta pessoal (research Decision 6).
pub const DEFAULT_DIR_NAME: &str = "artigo2md-dados";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum DataDirStatus {
    Ok,
    /// Não existe e a criação automática falhou (ou não foi pedida).
    Missing,
    NotDirectory,
    NotWritable,
    NotCanonical,
    /// Pasta ampla demais (plan.md M14).
    Forbidden,
}

/// `DataDirCheck` de `data-model.md`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DataDirCheck {
    pub path: String,
    pub status: DataDirStatus,
    pub canonical_path: Option<String>,
}

impl DataDirCheck {
    fn new(path: &Path, status: DataDirStatus, canonical: Option<&Path>) -> Self {
        Self {
            path: path.to_string_lossy().into_owned(),
            status,
            canonical_path: canonical.map(|c| c.to_string_lossy().into_owned()),
        }
    }

    pub fn is_ok(&self) -> bool {
        self.status == DataDirStatus::Ok
    }
}

/// `~/artigo2md-dados` com a pasta pessoal resolvida para o caminho real quando ela existe.
pub fn default_data_dir(home: &Path) -> PathBuf {
    let home = fs::canonicalize(home).unwrap_or_else(|_| home.to_path_buf());
    home.join(DEFAULT_DIR_NAME)
}

/// Normaliza `.`, `..` e barras repetidas sem tocar no disco (para comparar com as pastas
/// proibidas); `/a/../b` vira `/b`.
fn lexical(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for comp in path.components() {
        match comp {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    out
}

/// Formas comparáveis de um caminho: a léxica e, se existir, a canônica.
fn forms(path: &Path) -> Vec<PathBuf> {
    let mut v = vec![lexical(path)];
    if let Ok(c) = fs::canonicalize(path) {
        if !v.contains(&c) {
            v.push(c);
        }
    }
    v
}

/// `path` é `/`, a pasta pessoal, `~/Library`, a pasta do repositório ou um ancestral dela?
pub fn is_forbidden(path: &Path, home: &Path, repo: Option<&Path>) -> bool {
    let candidates = forms(path);
    let mut banned: Vec<PathBuf> = vec![PathBuf::from("/")];
    for h in forms(home) {
        banned.push(h.join("Library"));
        banned.push(h);
    }
    let repo_forms = repo.map(forms).unwrap_or_default();
    banned.extend(repo_forms.iter().cloned());
    for c in &candidates {
        if banned.contains(c) {
            return true;
        }
        // Ancestral estrito do repositório.
        if repo_forms.iter().any(|r| r.starts_with(c)) {
            return true;
        }
    }
    false
}

/// Verifica (e, com `create`, cria com modo `0700`) a pasta de dados. A ordem evita criar
/// qualquer coisa num caminho proibido.
pub fn check(path: &Path, home: &Path, repo: Option<&Path>, create: bool) -> DataDirCheck {
    if !path.is_absolute() {
        return DataDirCheck::new(path, DataDirStatus::NotCanonical, None);
    }
    if is_forbidden(path, home, repo) {
        return DataDirCheck::new(path, DataDirStatus::Forbidden, None);
    }
    if fs::symlink_metadata(path).is_err() && (!create || create_private(path).is_err()) {
        return DataDirCheck::new(path, DataDirStatus::Missing, None);
    }
    match fs::metadata(path) {
        Ok(meta) if meta.is_dir() => {}
        Ok(_) => return DataDirCheck::new(path, DataDirStatus::NotDirectory, None),
        Err(_) => return DataDirCheck::new(path, DataDirStatus::Missing, None),
    }
    let canonical = match fs::canonicalize(path) {
        Ok(c) => c,
        Err(_) => return DataDirCheck::new(path, DataDirStatus::NotWritable, None),
    };
    if canonical != path {
        let status = if is_forbidden(&canonical, home, repo) {
            DataDirStatus::Forbidden
        } else {
            DataDirStatus::NotCanonical
        };
        return DataDirCheck::new(path, status, Some(&canonical));
    }
    if !is_writable(path) {
        return DataDirCheck::new(path, DataDirStatus::NotWritable, Some(&canonical));
    }
    DataDirCheck::new(path, DataDirStatus::Ok, Some(&canonical))
}

fn create_private(path: &Path) -> io::Result<()> {
    fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(path)
}

/// Grava e remove um arquivo temporário dentro da pasta.
fn is_writable(dir: &Path) -> bool {
    let Ok(suffix) = random_hex(8) else {
        return false;
    };
    let probe = dir.join(format!(".artigo2md-write-test-{suffix}"));
    let created = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&probe)
        .is_ok();
    if created {
        let _ = fs::remove_file(&probe);
    }
    created
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{symlink, MetadataExt, PermissionsExt};

    struct Fixture {
        _tmp: tempfile::TempDir,
        home: PathBuf,
    }

    fn fixture() -> Fixture {
        let tmp = tempfile::tempdir().unwrap();
        let home = fs::canonicalize(tmp.path()).unwrap().join("home");
        fs::create_dir(&home).unwrap();
        Fixture { _tmp: tmp, home }
    }

    #[test]
    fn default_is_home_plus_artigo2md_dados() {
        let f = fixture();
        assert_eq!(default_data_dir(&f.home), f.home.join("artigo2md-dados"));
    }

    #[test]
    fn missing_folder_is_created_with_mode_0700() {
        let f = fixture();
        let dir = default_data_dir(&f.home);
        assert_eq!(
            check(&dir, &f.home, None, false).status,
            DataDirStatus::Missing
        );
        assert!(!dir.exists());

        let c = check(&dir, &f.home, None, true);
        assert_eq!(c.status, DataDirStatus::Ok);
        assert_eq!(c.canonical_path.as_deref(), dir.to_str());
        assert_eq!(fs::metadata(&dir).unwrap().mode() & 0o777, 0o700);
        // O arquivo de prova não fica para trás.
        assert_eq!(fs::read_dir(&dir).unwrap().count(), 0);
    }

    #[test]
    fn symlink_in_the_path_is_not_canonical() {
        let f = fixture();
        let real = f.home.join("real");
        fs::create_dir(&real).unwrap();
        let link = f.home.join("link");
        symlink(&real, &link).unwrap();
        let c = check(&link, &f.home, None, true);
        assert_eq!(c.status, DataDirStatus::NotCanonical);
        assert_eq!(c.canonical_path.as_deref(), real.to_str());

        let dotted = f.home.join("real").join("..").join("real");
        assert_eq!(
            check(&dotted, &f.home, None, true).status,
            DataDirStatus::NotCanonical
        );
        assert_eq!(
            check(Path::new("relativa"), &f.home, None, true).status,
            DataDirStatus::NotCanonical
        );
    }

    #[test]
    fn read_only_folder_is_not_writable() {
        let f = fixture();
        let dir = f.home.join("ro");
        fs::create_dir(&dir).unwrap();
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o500)).unwrap();
        let status = check(&dir, &f.home, None, true).status;
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        // Como root o teste de escrita passaria; o app não roda como root, mas o teste tolera.
        if crate::stack::locate::current_uid() != 0 {
            assert_eq!(status, DataDirStatus::NotWritable);
        }
    }

    #[test]
    fn regular_file_is_not_a_directory() {
        let f = fixture();
        let file = f.home.join("arquivo");
        fs::write(&file, b"x").unwrap();
        assert_eq!(
            check(&file, &f.home, None, true).status,
            DataDirStatus::NotDirectory
        );
    }

    #[test]
    fn broad_folders_are_forbidden_and_nothing_is_created() {
        let f = fixture();
        let repo = f.home.join("Projects").join("artigo2md");
        fs::create_dir_all(&repo).unwrap();
        fs::create_dir(f.home.join("Library")).unwrap();
        for p in [
            PathBuf::from("/"),
            f.home.clone(),
            f.home.join("Library"),
            repo.clone(),
            f.home.join("Projects"),
            f.home.join("Projects").join("..").join("Projects"),
        ] {
            let c = check(&p, &f.home, Some(&repo), true);
            assert_eq!(c.status, DataDirStatus::Forbidden, "{}", p.display());
        }
        // Subpasta própria da pasta pessoal e do repositório irmão é aceita.
        assert_eq!(
            check(&f.home.join("artigo2md-dados"), &f.home, Some(&repo), true).status,
            DataDirStatus::Ok
        );

        // Um link para a pasta pessoal também é proibido (pela forma canônica).
        let sneaky = f.home.join("atalho");
        symlink(&f.home, &sneaky).unwrap();
        assert_eq!(
            check(&sneaky, &f.home, Some(&repo), true).status,
            DataDirStatus::Forbidden
        );
    }

    #[test]
    fn serializes_in_camel_case() {
        let f = fixture();
        let c = check(&f.home.join("d"), &f.home, None, true);
        let v = serde_json::to_value(&c).unwrap();
        assert_eq!(v["status"], "ok");
        assert!(v.get("canonicalPath").is_some());
        let m = check(&f.home.join("x"), &f.home, None, false);
        let v = serde_json::to_value(&m).unwrap();
        assert_eq!(v["status"], "missing");
        assert!(v["canonicalPath"].is_null());
    }
}
