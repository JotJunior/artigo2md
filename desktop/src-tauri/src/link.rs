//! Resolução confinada de links relativos para `.md` (`resolve_link`; FR-040, Decision 9).

use std::fs;
use std::path::Path;

use crate::error::{AppError, AppResult, ErrorCode};
use crate::paths::{has_extension, has_scheme_or_authority, parent_dir_of, resolve};

/// Resolve `href` (relativo ao `.md` de origem `document_path`) para um caminho relativo
/// confinado, pronto para `read_markdown`. Fragmento (`#...`) e querystring (`?...`) são
/// descartados antes da resolução. Esquema, `//` ou caminho absoluto → `outsideFolder`.
pub fn resolve_link(root: &Path, document_path: &str, href: &str) -> AppResult<String> {
    if has_scheme_or_authority(href) {
        return Err(AppError::new(ErrorCode::OutsideFolder, "link com esquema"));
    }
    let without_fragment = href.split('#').next().unwrap_or("");
    let target = without_fragment.split('?').next().unwrap_or("");
    if target.is_empty() {
        return Err(AppError::new(
            ErrorCode::NotFound,
            "link sem destino de arquivo",
        ));
    }

    let base_dir = parent_dir_of(document_path)?;
    let resolved = resolve(root, &base_dir, target)?;
    let name = resolved.relative.rsplit('/').next().unwrap_or("");
    if !has_extension(name, "md") {
        return Err(AppError::new(ErrorCode::NotMarkdown, "destino não é .md"));
    }
    if !fs::metadata(&resolved.absolute)?.is_file() {
        return Err(AppError::new(
            ErrorCode::NotFound,
            "destino não é um arquivo",
        ));
    }
    Ok(resolved.relative)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::paths::canonical_root;

    fn setup() -> (tempfile::TempDir, std::path::PathBuf) {
        let tmp = tempfile::tempdir().unwrap();
        let root = canonical_root(tmp.path()).unwrap();
        fs::create_dir_all(root.join("Artigos")).unwrap();
        fs::write(root.join("Artigos/links.md"), "x").unwrap();
        fs::write(root.join("Artigos/artigo.md"), "x").unwrap();
        fs::write(root.join("Artigos/notas.txt"), "x").unwrap();
        fs::write(root.join("raiz.md"), "x").unwrap();
        (tmp, root)
    }

    #[test]
    fn resolves_sibling_and_parent() {
        let (_t, root) = setup();
        assert_eq!(
            resolve_link(&root, "Artigos/links.md", "artigo.md").unwrap(),
            "Artigos/artigo.md"
        );
        assert_eq!(
            resolve_link(&root, "Artigos/links.md", "../raiz.md").unwrap(),
            "raiz.md"
        );
    }

    #[test]
    fn drops_fragment_and_query() {
        let (_t, root) = setup();
        assert_eq!(
            resolve_link(&root, "Artigos/links.md", "artigo.md#secao").unwrap(),
            "Artigos/artigo.md"
        );
        assert_eq!(
            resolve_link(&root, "Artigos/links.md", "artigo.md?x=1#y").unwrap(),
            "Artigos/artigo.md"
        );
    }

    #[test]
    fn outside_the_folder() {
        let (_t, root) = setup();
        for href in [
            "../../x.md",
            "/etc/x.md",
            "https://example.org/a.md",
            "//h/a.md",
        ] {
            assert_eq!(
                resolve_link(&root, "Artigos/links.md", href)
                    .unwrap_err()
                    .code,
                ErrorCode::OutsideFolder,
                "{href}"
            );
        }
    }

    #[test]
    fn missing_and_not_markdown_and_anchor_only() {
        let (_t, root) = setup();
        assert_eq!(
            resolve_link(&root, "Artigos/links.md", "nao.md")
                .unwrap_err()
                .code,
            ErrorCode::NotFound
        );
        assert_eq!(
            resolve_link(&root, "Artigos/links.md", "notas.txt")
                .unwrap_err()
                .code,
            ErrorCode::NotMarkdown
        );
        assert_eq!(
            resolve_link(&root, "Artigos/links.md", "#topo")
                .unwrap_err()
                .code,
            ErrorCode::NotFound
        );
    }
}
