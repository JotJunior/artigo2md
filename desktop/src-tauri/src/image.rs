//! Leitura de imagem confinada, validada por extensão e assinatura (`read_image`;
//! FR-013, FR-014, FR-016, FR-032, FR-041; research R8).
//!
//! Ordem do contrato: confinamento, existência, extensão, assinatura, tamanho.

use std::fs::{self, File};
use std::io::Read;
use std::path::Path;

use crate::document::read_limited;
use crate::error::{AppError, AppResult, ErrorCode};
use crate::limits::MAX_IMAGE_BYTES;
use crate::paths::{has_scheme_or_authority, parent_dir_of, resolve};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Format {
    Png,
    Jpeg,
    Gif,
    Webp,
}

fn format_from_extension(name: &str) -> Option<Format> {
    let ext = Path::new(name).extension()?.to_str()?.to_ascii_lowercase();
    match ext.as_str() {
        "png" => Some(Format::Png),
        "jpg" | "jpeg" => Some(Format::Jpeg),
        "gif" => Some(Format::Gif),
        "webp" => Some(Format::Webp),
        _ => None,
    }
}

/// Confere os primeiros bytes contra a assinatura do formato.
/// PNG, GIF e WebP: research R8 (W3C PNG 3, GIF89a, Google WebP RIFF).
/// JPEG: `FF D8 FF` (observado em arquivo JFIF real; Exif sem fonte oficial lida, research R9.5).
fn signature_matches(format: Format, head: &[u8]) -> bool {
    match format {
        Format::Png => head.starts_with(&[0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]),
        Format::Jpeg => head.starts_with(&[0xFF, 0xD8, 0xFF]),
        Format::Gif => head.starts_with(b"GIF87a") || head.starts_with(b"GIF89a"),
        Format::Webp => head.len() >= 12 && &head[0..4] == b"RIFF" && &head[8..12] == b"WEBP",
    }
}

pub fn read_image(root: &Path, document_path: &str, src: &str) -> AppResult<Vec<u8>> {
    read_image_with_limit(root, document_path, src, MAX_IMAGE_BYTES)
}

pub fn read_image_with_limit(
    root: &Path,
    document_path: &str,
    src: &str,
    max_bytes: u64,
) -> AppResult<Vec<u8>> {
    // Defesa em profundidade: a interface já bloqueia esquemas (FR-016), mas o núcleo não confia.
    if has_scheme_or_authority(src) {
        return Err(AppError::new(
            ErrorCode::OutsideFolder,
            "referência com esquema",
        ));
    }

    // 1 e 2: confinamento e existência (nome exato).
    let base_dir = parent_dir_of(document_path)?;
    let resolved = resolve(root, &base_dir, src)?;
    let meta = fs::metadata(&resolved.absolute)?;
    if !meta.is_file() {
        return Err(AppError::new(ErrorCode::NotFound, "não é um arquivo"));
    }

    // 3: extensão.
    let name = resolved.relative.rsplit('/').next().unwrap_or("");
    let format = format_from_extension(name).ok_or_else(|| {
        AppError::new(
            ErrorCode::UnsupportedFormat,
            "extensão fora de png/jpg/jpeg/gif/webp",
        )
    })?;

    // 4: assinatura (só os primeiros bytes).
    let mut head = Vec::with_capacity(16);
    File::open(&resolved.absolute)?
        .take(16)
        .read_to_end(&mut head)?;
    if !signature_matches(format, &head) {
        return Err(AppError::new(
            ErrorCode::UnsupportedFormat,
            "assinatura incompatível com a extensão",
        ));
    }

    // 5: tamanho (metadados e leitura limitada).
    if meta.len() > max_bytes {
        return Err(AppError::new(
            ErrorCode::TooLarge,
            "metadados acima do limite",
        ));
    }
    read_limited(File::open(&resolved.absolute)?, max_bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::paths::canonical_root;

    const PNG: [u8; 8] = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A];

    fn png_bytes(extra: usize) -> Vec<u8> {
        let mut v = PNG.to_vec();
        v.extend(std::iter::repeat_n(0u8, extra));
        v
    }

    fn setup() -> (tempfile::TempDir, std::path::PathBuf) {
        let tmp = tempfile::tempdir().unwrap();
        let root = canonical_root(tmp.path()).unwrap();
        fs::create_dir_all(root.join("Artigos/imagens")).unwrap();
        fs::write(root.join("Artigos/artigo.md"), "x").unwrap();
        fs::write(root.join("Artigos/imagens/figura1.png"), png_bytes(10)).unwrap();
        (tmp, root)
    }

    #[test]
    fn reads_relative_png() {
        let (_t, root) = setup();
        let bytes = read_image(&root, "Artigos/artigo.md", "imagens/figura1.png").unwrap();
        assert_eq!(&bytes[..8], &PNG);
    }

    #[test]
    fn dotdot_leaving_root_is_outside_even_if_missing() {
        let (_t, root) = setup();
        let e = read_image(&root, "Artigos/artigo.md", "../../segredo.png").unwrap_err();
        assert_eq!(e.code, ErrorCode::OutsideFolder);
        let e = read_image(&root, "Artigos/artigo.md", "../../x.png").unwrap_err();
        assert_eq!(e.code, ErrorCode::OutsideFolder);
    }

    #[test]
    fn absolute_and_scheme_are_outside() {
        let (_t, root) = setup();
        for src in [
            "/etc/passwd.png",
            "https://example.org/a.png",
            "//cdn/a.png",
            "file:///a.png",
        ] {
            let e = read_image(&root, "Artigos/artigo.md", src).unwrap_err();
            assert_eq!(e.code, ErrorCode::OutsideFolder, "{src}");
        }
    }

    #[test]
    fn missing_is_not_found() {
        let (_t, root) = setup();
        let e = read_image(&root, "Artigos/artigo.md", "imagens/naoexiste.png").unwrap_err();
        assert_eq!(e.code, ErrorCode::NotFound);
    }

    #[test]
    fn svg_and_unknown_extensions_are_unsupported() {
        let (_t, root) = setup();
        fs::write(root.join("Artigos/imagens/desenho.svg"), "<svg/>").unwrap();
        fs::write(root.join("Artigos/imagens/a.bmp"), "BM").unwrap();
        for src in ["imagens/desenho.svg", "imagens/a.bmp"] {
            let e = read_image(&root, "Artigos/artigo.md", src).unwrap_err();
            assert_eq!(e.code, ErrorCode::UnsupportedFormat, "{src}");
        }
    }

    #[test]
    fn signature_must_match_extension() {
        let (_t, root) = setup();
        fs::write(root.join("Artigos/imagens/falso.png"), b"<html>nao sou png").unwrap();
        fs::write(root.join("Artigos/imagens/png-como-gif.gif"), png_bytes(4)).unwrap();
        for src in ["imagens/falso.png", "imagens/png-como-gif.gif"] {
            let e = read_image(&root, "Artigos/artigo.md", src).unwrap_err();
            assert_eq!(e.code, ErrorCode::UnsupportedFormat, "{src}");
        }
    }

    #[test]
    fn accepts_each_supported_format() {
        let (_t, root) = setup();
        let dir = root.join("Artigos/imagens");
        fs::write(dir.join("a.jpg"), [0xFF, 0xD8, 0xFF, 0xE0, 0, 0x10]).unwrap();
        fs::write(dir.join("b.JPEG"), [0xFF, 0xD8, 0xFF, 0xE1, 0, 0x10]).unwrap();
        fs::write(dir.join("c.gif"), b"GIF89a\x01\x00").unwrap();
        fs::write(dir.join("d.gif"), b"GIF87a\x01\x00").unwrap();
        let mut webp = b"RIFF".to_vec();
        webp.extend([0x10, 0, 0, 0]);
        webp.extend(b"WEBPVP8 ");
        fs::write(dir.join("e.webp"), webp).unwrap();
        for src in [
            "imagens/a.jpg",
            "imagens/b.JPEG",
            "imagens/c.gif",
            "imagens/d.gif",
            "imagens/e.webp",
        ] {
            assert!(read_image(&root, "Artigos/artigo.md", src).is_ok(), "{src}");
        }
    }

    #[test]
    fn too_large_above_limit() {
        let (_t, root) = setup();
        fs::write(root.join("Artigos/imagens/grande.png"), png_bytes(100)).unwrap();
        let e = read_image_with_limit(&root, "Artigos/artigo.md", "imagens/grande.png", 50)
            .unwrap_err();
        assert_eq!(e.code, ErrorCode::TooLarge);
        assert!(
            read_image_with_limit(&root, "Artigos/artigo.md", "imagens/grande.png", 108).is_ok()
        );
        assert_eq!(MAX_IMAGE_BYTES, 20_000_000);
    }

    #[test]
    fn accented_name_resolves_only_exact() {
        let (_t, root) = setup();
        let nfc = "figura-\u{e7}\u{e3}o.png";
        let nfd = "figura-c\u{327}a\u{303}o.png";
        fs::write(root.join("Artigos").join(nfc), png_bytes(1)).unwrap();
        assert!(read_image(&root, "Artigos/artigo.md", nfc).is_ok());
        assert_eq!(
            read_image(&root, "Artigos/artigo.md", nfd)
                .unwrap_err()
                .code,
            ErrorCode::NotFound
        );
    }

    #[test]
    fn symlink_pointing_outside_is_outside() {
        let (_t, root) = setup();
        let outside = tempfile::tempdir().unwrap();
        fs::write(outside.path().join("fora.png"), png_bytes(1)).unwrap();
        std::os::unix::fs::symlink(
            outside.path().join("fora.png"),
            root.join("Artigos/atalho.png"),
        )
        .unwrap();
        let e = read_image(&root, "Artigos/artigo.md", "atalho.png").unwrap_err();
        assert_eq!(e.code, ErrorCode::OutsideFolder);
    }
}
