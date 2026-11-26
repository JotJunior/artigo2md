//! Leitura de `.md` confinada e limitada (`read_markdown`; FR-009, FR-018, FR-031, FR-034, FR-035).

use std::fs::{self, File};
use std::io::Read;
use std::path::Path;

use crate::error::{AppError, AppResult, ErrorCode};
use crate::limits::{LARGE_FILE_THRESHOLD_BYTES, MAX_MARKDOWN_BYTES};
use crate::model::MarkdownDocument;
use crate::paths::{has_extension, resolve};

/// Lê no máximo `limit` bytes de `reader`. Lê `limit + 1` para detectar excesso: se a fonte
/// passar do teto (arquivo que cresceu depois da conferência de metadados), devolve `tooLarge`
/// sem nunca manter na memória mais que `limit + 1` bytes (plan.md, "Leitura limitada ao teto").
pub fn read_limited<R: Read>(reader: R, limit: u64) -> AppResult<Vec<u8>> {
    let mut buf = Vec::new();
    reader.take(limit.saturating_add(1)).read_to_end(&mut buf)?;
    if buf.len() as u64 > limit {
        return Err(AppError::new(
            ErrorCode::TooLarge,
            "conteúdo acima do limite de leitura",
        ));
    }
    Ok(buf)
}

pub fn read_markdown(root: &Path, relative_path: &str) -> AppResult<MarkdownDocument> {
    read_markdown_with_limit(root, relative_path, MAX_MARKDOWN_BYTES)
}

/// Variante com teto explícito (permite testar o limite sem arquivos de 50 MB).
pub fn read_markdown_with_limit(
    root: &Path,
    relative_path: &str,
    max_bytes: u64,
) -> AppResult<MarkdownDocument> {
    let resolved = resolve(root, "", relative_path)?;
    let name = resolved.relative.rsplit('/').next().unwrap_or("");
    if !has_extension(name, "md") {
        return Err(AppError::new(
            ErrorCode::NotMarkdown,
            "extensão diferente de .md",
        ));
    }

    let meta = fs::metadata(&resolved.absolute)?;
    if !meta.is_file() {
        return Err(AppError::new(ErrorCode::NotFound, "não é um arquivo"));
    }
    if meta.len() > max_bytes {
        return Err(AppError::new(
            ErrorCode::TooLarge,
            "metadados acima do limite",
        ));
    }

    let bytes = read_limited(File::open(&resolved.absolute)?, max_bytes)?;
    let size_bytes = bytes.len() as u64;
    let content = String::from_utf8(bytes)
        .map_err(|_| AppError::new(ErrorCode::NotText, "conteúdo não é UTF-8 válido"))?;

    Ok(MarkdownDocument {
        relative_path: resolved.relative,
        content,
        size_bytes,
        is_large: size_bytes > LARGE_FILE_THRESHOLD_BYTES,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::paths::canonical_root;
    use std::io::Cursor;

    fn root_of(tmp: &tempfile::TempDir) -> std::path::PathBuf {
        canonical_root(tmp.path()).unwrap()
    }

    #[test]
    fn reads_markdown() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        fs::create_dir(root.join("Artigos")).unwrap();
        fs::write(root.join("Artigos/artigo.md"), "# Título\n\nOlá, mundo.").unwrap();
        let doc = read_markdown(&root, "Artigos/artigo.md").unwrap();
        assert_eq!(doc.relative_path, "Artigos/artigo.md");
        assert_eq!(doc.content, "# Título\n\nOlá, mundo.");
        assert_eq!(doc.size_bytes, doc.content.len() as u64);
        assert!(!doc.is_large);
    }

    #[test]
    fn extension_is_case_insensitive() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        fs::write(root.join("LEIA.MD"), "x").unwrap();
        assert!(read_markdown(&root, "LEIA.MD").is_ok());
    }

    #[test]
    fn rejects_non_markdown() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        fs::write(root.join("notas.txt"), "x").unwrap();
        assert_eq!(
            read_markdown(&root, "notas.txt").unwrap_err().code,
            ErrorCode::NotMarkdown
        );
    }

    #[test]
    fn rejects_non_utf8() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        fs::write(root.join("binario.md"), [0xff, 0xfe, 0x00, 0xc3, 0x28]).unwrap();
        assert_eq!(
            read_markdown(&root, "binario.md").unwrap_err().code,
            ErrorCode::NotText
        );
    }

    #[test]
    fn marks_large_above_five_million_bytes() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        fs::write(root.join("exato.md"), vec![b'a'; 5_000_000]).unwrap();
        fs::write(root.join("grande.md"), vec![b'a'; 5_000_001]).unwrap();
        assert!(!read_markdown(&root, "exato.md").unwrap().is_large);
        let big = read_markdown(&root, "grande.md").unwrap();
        assert!(big.is_large);
        assert_eq!(big.size_bytes, 5_000_001);
    }

    #[test]
    fn too_large_by_metadata() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        fs::write(root.join("a.md"), vec![b'a'; 101]).unwrap();
        fs::write(root.join("b.md"), vec![b'a'; 100]).unwrap();
        assert_eq!(
            read_markdown_with_limit(&root, "a.md", 100)
                .unwrap_err()
                .code,
            ErrorCode::TooLarge
        );
        assert!(read_markdown_with_limit(&root, "b.md", 100).is_ok());
    }

    #[test]
    fn real_limit_is_fifty_million_bytes() {
        assert_eq!(MAX_MARKDOWN_BYTES, 50_000_000);
    }

    #[test]
    fn bounded_read_catches_growth_after_metadata_check() {
        // A fonte entrega mais bytes do que os metadados anunciaram: a leitura limitada recusa.
        let grown = Cursor::new(vec![b'a'; 150]);
        assert_eq!(
            read_limited(grown, 100).unwrap_err().code,
            ErrorCode::TooLarge
        );
        let at_limit = Cursor::new(vec![b'a'; 100]);
        assert_eq!(read_limited(at_limit, 100).unwrap().len(), 100);
    }

    #[test]
    fn deleted_between_reads_is_not_found() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        fs::write(root.join("a.md"), "x").unwrap();
        assert!(read_markdown(&root, "a.md").is_ok());
        fs::remove_file(root.join("a.md")).unwrap();
        assert_eq!(
            read_markdown(&root, "a.md").unwrap_err().code,
            ErrorCode::NotFound
        );
    }

    #[test]
    fn rereading_returns_current_content() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        fs::write(root.join("a.md"), "um").unwrap();
        assert_eq!(read_markdown(&root, "a.md").unwrap().content, "um");
        fs::write(root.join("a.md"), "dois").unwrap();
        assert_eq!(read_markdown(&root, "a.md").unwrap().content, "dois");
    }

    #[test]
    fn outside_folder_is_confined() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        assert_eq!(
            read_markdown(&root, "../../x.md").unwrap_err().code,
            ErrorCode::OutsideFolder
        );
    }
}
