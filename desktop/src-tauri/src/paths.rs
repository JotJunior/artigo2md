//! Confinamento de caminhos (FR-013, FR-030, FR-032, FR-040; contracts/commands.md).
//!
//! Ordem: (1) rejeita caminho absoluto; (2) resolve `.`/`..` por componentes SEM tocar o
//! disco (destino fora da raiz = `outsideFolder`, mesmo se inexistente); (3) confere que cada
//! componente existe com o nome EXATO do disco (sem normalização de acento nem de caixa);
//! (4) canoniza (resolve atalhos) e confere, por componente, que o resultado está dentro da raiz.

use std::ffi::OsStr;
use std::fs;
use std::path::{Path, PathBuf};

use crate::error::{AppError, AppResult, ErrorCode};

/// Resultado de uma resolução confinada.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Resolved {
    /// Caminho léxico relativo à raiz, separador `/`, sem `.` nem `..` (vazio = a raiz).
    pub relative: String,
    /// Caminho absoluto canonizado (atalhos resolvidos), garantidamente dentro da raiz.
    pub absolute: PathBuf,
}

fn outside(detail: &str) -> AppError {
    AppError::new(ErrorCode::OutsideFolder, detail)
}

/// Canoniza a raiz escolhida e confere que é uma pasta legível.
pub fn canonical_root(path: &Path) -> AppResult<PathBuf> {
    let canonical = fs::canonicalize(path).map_err(|e| match e.kind() {
        std::io::ErrorKind::NotFound | std::io::ErrorKind::NotADirectory => {
            AppError::new(ErrorCode::FolderUnavailable, e.to_string())
        }
        _ => AppError::from(e),
    })?;
    let meta = fs::metadata(&canonical)?;
    if !meta.is_dir() {
        return Err(AppError::new(
            ErrorCode::FolderUnavailable,
            "a raiz não é uma pasta",
        ));
    }
    // Confere a permissão de leitura abrindo a pasta.
    fs::read_dir(&canonical)?;
    Ok(canonical)
}

/// Passo 1 e 2: componentes léxicos de `base_dir` + `rel`, sem tocar o disco.
pub fn lexical_components(base_dir: &str, rel: &str) -> AppResult<Vec<String>> {
    if rel.contains('\0') || base_dir.contains('\0') {
        return Err(AppError::new(ErrorCode::NotFound, "caminho com byte nulo"));
    }
    if rel.starts_with('/') || base_dir.starts_with('/') {
        return Err(outside("caminho absoluto"));
    }
    let mut parts: Vec<String> = Vec::new();
    for seg in base_dir.split('/').chain(rel.split('/')) {
        match seg {
            "" | "." => {}
            ".." => {
                if parts.pop().is_none() {
                    return Err(outside("caminho sai da raiz"));
                }
            }
            s => parts.push(s.to_string()),
        }
    }
    Ok(parts)
}

/// Pasta (relativa, léxica) que contém um documento: tudo menos o último componente.
pub fn parent_dir_of(document_path: &str) -> AppResult<String> {
    let mut parts = lexical_components("", document_path)?;
    parts.pop();
    Ok(parts.join("/"))
}

/// Confere que `dir` tem uma entrada chamada exatamente `name` (byte a byte).
fn has_exact_entry(dir: &Path, name: &OsStr) -> AppResult<bool> {
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        if entry.file_name() == name {
            return Ok(true);
        }
    }
    Ok(false)
}

/// Resolve `rel` (relativo a `base_dir`, que é relativo à `root` canonizada) com confinamento.
pub fn resolve(root: &Path, base_dir: &str, rel: &str) -> AppResult<Resolved> {
    let comps = lexical_components(base_dir, rel)?;

    // Nome exato: cada componente precisa existir, com esse nome, na listagem da pasta-pai.
    let mut current = root.to_path_buf();
    for comp in &comps {
        if !has_exact_entry(&current, OsStr::new(comp))? {
            return Err(AppError::new(
                ErrorCode::NotFound,
                "nome não existe exatamente",
            ));
        }
        current.push(comp);
    }

    let absolute = fs::canonicalize(&current)?;
    if !absolute.starts_with(root) {
        return Err(outside("atalho aponta para fora da raiz"));
    }
    Ok(Resolved {
        relative: comps.join("/"),
        absolute,
    })
}

/// Extensão do nome comparada sem distinguir caixa.
pub fn has_extension(name: &str, ext: &str) -> bool {
    Path::new(name)
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.eq_ignore_ascii_case(ext))
        .unwrap_or(false)
}

/// Verdadeiro se `value` começa com um esquema (`http:`, `file:`...) ou com `//`.
pub fn has_scheme_or_authority(value: &str) -> bool {
    if value.starts_with("//") {
        return true;
    }
    let head = value.split(['/', '?', '#']).next().unwrap_or("");
    match head.find(':') {
        Some(i) if i > 0 => {
            let scheme = &head[..i];
            let mut chars = scheme.chars();
            chars.next().is_some_and(|c| c.is_ascii_alphabetic())
                && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'))
        }
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    fn setup() -> (tempfile::TempDir, PathBuf) {
        let tmp = tempfile::tempdir().unwrap();
        let root = canonical_root(tmp.path()).unwrap();
        fs::create_dir_all(root.join("docs/img")).unwrap();
        fs::write(root.join("docs/a.md"), "a").unwrap();
        fs::write(root.join("docs/img/fig.png"), "x").unwrap();
        (tmp, root)
    }

    #[test]
    fn relative_inside_resolves() {
        let (_t, root) = setup();
        let r = resolve(&root, "docs", "img/fig.png").unwrap();
        assert_eq!(r.relative, "docs/img/fig.png");
        assert!(r.absolute.starts_with(&root));
        let r2 = resolve(&root, "docs/img", "../a.md").unwrap();
        assert_eq!(r2.relative, "docs/a.md");
    }

    #[test]
    fn dotdot_leaving_root_is_outside() {
        let (_t, root) = setup();
        let e = resolve(&root, "docs", "../../x.md").unwrap_err();
        assert_eq!(e.code, ErrorCode::OutsideFolder);
        let e = resolve(&root, "docs", "../../x.png").unwrap_err();
        assert_eq!(e.code, ErrorCode::OutsideFolder);
    }

    #[test]
    fn absolute_is_outside() {
        let (_t, root) = setup();
        let e = resolve(&root, "", "/etc/passwd").unwrap_err();
        assert_eq!(e.code, ErrorCode::OutsideFolder);
    }

    #[test]
    fn missing_inside_is_not_found() {
        let (_t, root) = setup();
        let e = resolve(&root, "docs", "nao-existe.md").unwrap_err();
        assert_eq!(e.code, ErrorCode::NotFound);
    }

    #[test]
    fn symlink_to_outside_is_outside() {
        let (_t, root) = setup();
        let outside_dir = tempfile::tempdir().unwrap();
        fs::write(outside_dir.path().join("segredo.png"), "x").unwrap();
        symlink(outside_dir.path(), root.join("docs/atalho")).unwrap();
        let e = resolve(&root, "docs", "atalho/segredo.png").unwrap_err();
        assert_eq!(e.code, ErrorCode::OutsideFolder);
    }

    #[test]
    fn symlink_inside_resolves() {
        let (_t, root) = setup();
        symlink(root.join("docs/a.md"), root.join("link.md")).unwrap();
        let r = resolve(&root, "", "link.md").unwrap();
        assert_eq!(r.relative, "link.md");
        assert!(r.absolute.ends_with("docs/a.md"));
    }

    #[test]
    fn case_difference_resolves_only_exact_name() {
        let (_t, root) = setup();
        assert!(resolve(&root, "docs/img", "fig.png").is_ok());
        let e = resolve(&root, "docs/img", "FIG.png").unwrap_err();
        assert_eq!(e.code, ErrorCode::NotFound);
        let e = resolve(&root, "", "DOCS/a.md").unwrap_err();
        assert_eq!(e.code, ErrorCode::NotFound);
    }

    #[test]
    fn accent_difference_resolves_only_exact_name() {
        let (_t, root) = setup();
        let nfc = "caf\u{e9}.md"; // é composto
        let nfd = "cafe\u{301}.md"; // e + acento combinante
        let plain = "cafe.md";
        fs::write(root.join(nfc), "x").unwrap();
        assert!(resolve(&root, "", nfc).is_ok());
        assert_eq!(
            resolve(&root, "", nfd).unwrap_err().code,
            ErrorCode::NotFound
        );
        assert_eq!(
            resolve(&root, "", plain).unwrap_err().code,
            ErrorCode::NotFound
        );
    }

    #[test]
    fn scheme_detection() {
        assert!(has_scheme_or_authority("https://x.org/a.png"));
        assert!(has_scheme_or_authority("data:image/png;base64,AA"));
        assert!(has_scheme_or_authority("file:///etc/passwd"));
        assert!(has_scheme_or_authority("//cdn.x/a.png"));
        assert!(!has_scheme_or_authority("imagens/a.png"));
        assert!(!has_scheme_or_authority("./a:b.png"));
        assert!(!has_scheme_or_authority("pasta/a:b.png"));
    }

    #[test]
    fn extension_is_case_insensitive() {
        assert!(has_extension("A.MD", "md"));
        assert!(!has_extension("a.txt", "md"));
        assert!(!has_extension("md", "md"));
    }

    #[test]
    fn parent_dir() {
        assert_eq!(parent_dir_of("a/b/c.md").unwrap(), "a/b");
        assert_eq!(parent_dir_of("c.md").unwrap(), "");
    }
}
