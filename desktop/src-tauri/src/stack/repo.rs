//! `RepoCheck` e confiança na pasta do repositório (spec FR-016, FR-016A; research Decision 4;
//! plan.md M3, M4; data-model.md RepoCheck).
//!
//! O app não empacota o repositório: a pessoa indica a pasta (pelo seletor nativo, M1) e, antes de
//! qualquer operação, a verificação para no primeiro problema, nesta ordem: a pasta existe;
//! `stack/compose.yaml`, `stack/up.sh` e `stack/down.sh`; `pyproject.toml` e `uv.lock`; o `uv`
//! foi resolvido; `uv run --no-sync --project <repo> artigo2md --version` sai com 0.
//!
//! M3: a pasta, `stack/` e os três arquivos da stack precisam ser do uid corrente, regulares (sem
//! link simbólico) e sem escrita para grupo/outros. Um item que falha nessa confiança é tratado
//! como ausente: pasta → `missingFolder`; arquivo ou pasta da stack → `missingStackFiles`;
//! `pyproject.toml`/`uv.lock` não regulares → `missingProjectFiles`; o caminho relativo vai em
//! `missing`.
//!
//! `modelsUnsupported` (FR-016A) NÃO nasce aqui: é detectado ao rodar `artigo2md models status
//! --json` (saída 2, evento sem `schema_version` ou versão desconhecida), na verificação dos
//! modelos (FASE 7). Esta verificação não executa `models`; o tipo só carrega o estado.

use std::fs::{self, Metadata};
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::Serialize;

use crate::stack::exec::{self, ExecSpec};
use crate::stack::locate::{current_uid, Located};
use crate::stack::secret::Masker;

/// Arquivos da stack exigidos (relativos ao repositório), na ordem de verificação.
pub const STACK_FILES: [&str; 3] = ["stack/compose.yaml", "stack/up.sh", "stack/down.sh"];
/// Arquivos de projeto exigidos para o build da imagem da API e para o `uv run`.
pub const PROJECT_FILES: [&str; 2] = ["pyproject.toml", "uv.lock"];

/// Tempo máximo do `uv run --no-sync … --version`.
pub const UV_CHECK_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum RepoStatus {
    Ok,
    NotSet,
    MissingFolder,
    MissingStackFiles,
    MissingProjectFiles,
    UvNotFound,
    UvEnvBroken,
    ModelsUnsupported,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RepoCheck {
    pub status: RepoStatus,
    pub uv_path: Option<String>,
    /// Caminhos relativos ao repositório dos arquivos ausentes (ou não confiáveis).
    pub missing: Vec<String>,
}

impl RepoCheck {
    fn status_only(status: RepoStatus, uv: Option<&Path>) -> Self {
        Self {
            status,
            uv_path: uv.map(|p| p.to_string_lossy().into_owned()),
            missing: Vec::new(),
        }
    }

    fn with_missing(status: RepoStatus, missing: Vec<String>) -> Self {
        Self {
            status,
            uv_path: None,
            missing,
        }
    }

    pub fn is_ok(&self) -> bool {
        self.status == RepoStatus::Ok
    }
}

/// Dono é `uid`, sem escrita para grupo/outros (M3).
fn is_owned_and_closed(meta: &Metadata, uid: u32) -> bool {
    meta.uid() == uid && meta.mode() & 0o022 == 0
}

/// Pasta de verdade (não link), do uid corrente e sem escrita para grupo/outros.
fn trusted_dir(path: &Path, uid: u32) -> bool {
    match fs::symlink_metadata(path) {
        Ok(m) => m.is_dir() && is_owned_and_closed(&m, uid),
        Err(_) => false,
    }
}

/// Arquivo regular (não link), do uid corrente e sem escrita para grupo/outros.
fn trusted_file(path: &Path, uid: u32) -> bool {
    match fs::symlink_metadata(path) {
        Ok(m) => m.is_file() && is_owned_and_closed(&m, uid),
        Err(_) => false,
    }
}

/// Caminho canônico da pasta indicada, para mostrar à pessoa e gravar em `repoPath` (M3).
pub fn canonical_repo(path: &Path) -> Option<PathBuf> {
    fs::canonicalize(path).ok().filter(|c| c.is_dir())
}

/// Verifica o repositório com o uid do processo.
pub fn check(repo: Option<&Path>, located: &Located) -> RepoCheck {
    check_with_uid(repo, located, current_uid())
}

/// Núcleo testável: `uid` explícito.
pub fn check_with_uid(repo: Option<&Path>, located: &Located, uid: u32) -> RepoCheck {
    let Some(repo) = repo else {
        return RepoCheck::status_only(RepoStatus::NotSet, None);
    };
    if !trusted_dir(repo, uid) && !is_trusted_via_canonical(repo, uid) {
        return RepoCheck::with_missing(RepoStatus::MissingFolder, vec![".".to_string()]);
    }
    // O caminho gravado já é canônico; se a pasta indicada for link para uma pasta confiável,
    // trabalha-se no destino, mas os arquivos de dentro continuam sendo conferidos sem seguir links.
    let repo = canonical_repo(repo).unwrap_or_else(|| repo.to_path_buf());

    // Pasta `stack/` e os três arquivos da stack.
    let mut missing = Vec::new();
    if !trusted_dir(&repo.join("stack"), uid) {
        missing.push("stack".to_string());
    } else {
        for rel in STACK_FILES {
            if !trusted_file(&repo.join(rel), uid) {
                missing.push(rel.to_string());
            }
        }
    }
    if !missing.is_empty() {
        return RepoCheck::with_missing(RepoStatus::MissingStackFiles, missing);
    }

    // `pyproject.toml` e `uv.lock`: precisam existir como arquivos regulares.
    let missing: Vec<String> = PROJECT_FILES
        .iter()
        .filter(|rel| {
            !fs::symlink_metadata(repo.join(rel))
                .map(|m| m.is_file())
                .unwrap_or(false)
        })
        .map(|rel| rel.to_string())
        .collect();
    if !missing.is_empty() {
        return RepoCheck::with_missing(RepoStatus::MissingProjectFiles, missing);
    }

    let Some(uv) = located.uv.as_deref() else {
        return RepoCheck::status_only(RepoStatus::UvNotFound, None);
    };

    if uv_version_ok(uv, &repo, located) {
        RepoCheck::status_only(RepoStatus::Ok, Some(uv))
    } else {
        RepoCheck::status_only(RepoStatus::UvEnvBroken, Some(uv))
    }
}

/// A pasta indicada pode ser um link para uma pasta confiável (o app grava o caminho canônico).
fn is_trusted_via_canonical(path: &Path, uid: u32) -> bool {
    match canonical_repo(path) {
        Some(c) => trusted_dir(&c, uid),
        None => false,
    }
}

/// `uv run --no-sync --project <repo> artigo2md --version` com saída 0 (research Decision 4;
/// `--no-sync` evita sincronizar o ambiente e baixar pacotes, M4).
fn uv_version_ok(uv: &Path, repo: &Path, located: &Located) -> bool {
    let spec = ExecSpec::new(uv, UV_CHECK_TIMEOUT)
        .arg("run")
        .arg("--no-sync")
        .arg("--project")
        .arg(repo)
        .arg("artigo2md")
        .arg("--version")
        .for_uv()
        .path_dirs(located.path_dirs());
    matches!(exec::run_collect(spec, &Masker::new()), Ok((o, _)) if o.success())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{symlink, PermissionsExt};

    struct Fake {
        _tmp: tempfile::TempDir,
        repo: PathBuf,
        located: Located,
        args_file: PathBuf,
    }

    fn write_exec(path: &Path, body: &str) {
        fs::write(path, format!("#!/bin/sh\n{body}\n")).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
    }

    /// Repositório falso completo + `uv` falso que grava os argumentos e sai com `uv_exit`.
    fn fake(uv_exit: i32) -> Fake {
        let tmp = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(tmp.path()).unwrap();
        let repo = root.join("repo");
        fs::create_dir_all(repo.join("stack")).unwrap();
        for rel in STACK_FILES.iter().chain(PROJECT_FILES.iter()) {
            fs::write(repo.join(rel), b"x").unwrap();
            fs::set_permissions(repo.join(rel), fs::Permissions::from_mode(0o644)).unwrap();
        }
        for dir in [repo.clone(), repo.join("stack")] {
            fs::set_permissions(&dir, fs::Permissions::from_mode(0o755)).unwrap();
        }
        let bin = root.join("bin");
        fs::create_dir(&bin).unwrap();
        let uv = bin.join("uv");
        let args_file = root.join("uv-args.txt");
        write_exec(
            &uv,
            &format!(
                "printf '%s\\n' \"$@\" > '{}'\nprintf '%s\\n' \"UV_OFFLINE=$UV_OFFLINE\" >> '{}'\nexit {uv_exit}",
                args_file.display(),
                args_file.display()
            ),
        );
        Fake {
            _tmp: tmp,
            repo,
            located: Located {
                docker: None,
                uv: Some(uv),
                colima: None,
            },
            args_file,
        }
    }

    #[test]
    fn not_set_and_missing_folder() {
        let f = fake(0);
        assert_eq!(check(None, &f.located).status, RepoStatus::NotSet);
        let gone = f.repo.join("nao-existe");
        let c = check(Some(&gone), &f.located);
        assert_eq!(c.status, RepoStatus::MissingFolder);
    }

    #[test]
    fn complete_repo_is_ok_and_runs_uv_without_sync_and_offline() {
        let f = fake(0);
        let c = check(Some(&f.repo), &f.located);
        assert_eq!(c.status, RepoStatus::Ok, "{c:?}");
        assert!(c.missing.is_empty());
        assert_eq!(
            c.uv_path.as_deref(),
            f.located.uv.as_ref().unwrap().to_str()
        );
        let args = fs::read_to_string(&f.args_file).unwrap();
        let lines: Vec<&str> = args.lines().collect();
        assert_eq!(
            lines,
            [
                "run",
                "--no-sync",
                "--project",
                f.repo.to_str().unwrap(),
                "artigo2md",
                "--version",
                "UV_OFFLINE=1"
            ]
        );
    }

    #[test]
    fn each_missing_stack_file_is_reported_relative_to_the_repo() {
        for rel in STACK_FILES {
            let f = fake(0);
            fs::remove_file(f.repo.join(rel)).unwrap();
            let c = check(Some(&f.repo), &f.located);
            assert_eq!(c.status, RepoStatus::MissingStackFiles, "{rel}");
            assert_eq!(c.missing, vec![rel.to_string()]);
        }
        let f = fake(0);
        fs::remove_dir_all(f.repo.join("stack")).unwrap();
        let c = check(Some(&f.repo), &f.located);
        assert_eq!(c.status, RepoStatus::MissingStackFiles);
        assert_eq!(c.missing, vec!["stack".to_string()]);
    }

    #[test]
    fn each_missing_project_file_is_reported() {
        for rel in PROJECT_FILES {
            let f = fake(0);
            fs::remove_file(f.repo.join(rel)).unwrap();
            let c = check(Some(&f.repo), &f.located);
            assert_eq!(c.status, RepoStatus::MissingProjectFiles, "{rel}");
            assert_eq!(c.missing, vec![rel.to_string()]);
        }
    }

    #[test]
    fn order_stack_files_come_before_project_files() {
        let f = fake(0);
        fs::remove_file(f.repo.join("uv.lock")).unwrap();
        fs::remove_file(f.repo.join("stack/up.sh")).unwrap();
        assert_eq!(
            check(Some(&f.repo), &f.located).status,
            RepoStatus::MissingStackFiles
        );
    }

    #[test]
    fn uv_not_found_and_broken_environment() {
        let mut f = fake(0);
        f.located.uv = None;
        assert_eq!(
            check(Some(&f.repo), &f.located).status,
            RepoStatus::UvNotFound
        );

        let broken = fake(1);
        let c = check(Some(&broken.repo), &broken.located);
        assert_eq!(c.status, RepoStatus::UvEnvBroken);
        assert!(c.uv_path.is_some());
    }

    #[test]
    fn symlinked_stack_file_is_refused() {
        let f = fake(0);
        let target = f.repo.join("external-target.sh");
        fs::write(&target, b"x").unwrap();
        fs::remove_file(f.repo.join("stack/up.sh")).unwrap();
        symlink(&target, f.repo.join("stack/up.sh")).unwrap();
        let c = check(Some(&f.repo), &f.located);
        assert_eq!(c.status, RepoStatus::MissingStackFiles);
        assert_eq!(c.missing, vec!["stack/up.sh".to_string()]);
    }

    #[test]
    fn symlinked_stack_folder_is_refused() {
        let f = fake(0);
        let real = f.repo.join("stack-real");
        fs::rename(f.repo.join("stack"), &real).unwrap();
        symlink(&real, f.repo.join("stack")).unwrap();
        let c = check(Some(&f.repo), &f.located);
        assert_eq!(c.status, RepoStatus::MissingStackFiles);
    }

    #[test]
    fn group_or_other_writable_is_refused() {
        for (rel, mode) in [
            ("stack/down.sh", 0o664),
            ("stack/compose.yaml", 0o646),
            ("stack", 0o775),
        ] {
            let f = fake(0);
            fs::set_permissions(f.repo.join(rel), fs::Permissions::from_mode(mode)).unwrap();
            let c = check(Some(&f.repo), &f.located);
            assert_eq!(c.status, RepoStatus::MissingStackFiles, "{rel}");
        }
        let f = fake(0);
        fs::set_permissions(&f.repo, fs::Permissions::from_mode(0o777)).unwrap();
        assert_eq!(
            check(Some(&f.repo), &f.located).status,
            RepoStatus::MissingFolder
        );
    }

    #[test]
    fn other_owner_is_refused() {
        let f = fake(0);
        // Simula um dono diferente passando outro uid como "corrente".
        let c = check_with_uid(Some(&f.repo), &f.located, current_uid().wrapping_add(1));
        assert_eq!(c.status, RepoStatus::MissingFolder);
    }

    #[test]
    fn serializes_in_camel_case() {
        let f = fake(0);
        let mut c = check(Some(&f.repo), &f.located);
        c.missing.push("x".into());
        let v = serde_json::to_value(&c).unwrap();
        assert_eq!(v["status"], "ok");
        assert!(v.get("uvPath").is_some());
        let v = serde_json::to_value(RepoStatus::ModelsUnsupported).unwrap();
        assert_eq!(v, "modelsUnsupported");
        let v = serde_json::to_value(RepoStatus::MissingStackFiles).unwrap();
        assert_eq!(v, "missingStackFiles");
    }
}
