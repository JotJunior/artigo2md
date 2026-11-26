//! Listagem de um nível da árvore (`list_dir`; FR-005, FR-008, FR-028 a FR-030, SC-002).

use std::fs;
use std::path::Path;

use crate::error::{AppError, AppResult, ErrorCode};
use crate::model::{Access, EntryKind, SymlinkTarget, TreeEntry};
use crate::paths::{has_extension, resolve};

/// Lista um nível de `relative_path` (`""` = a raiz), já filtrado e ordenado:
/// ocultos (nome iniciado por `.`) omitidos; pastas antes de arquivos e atalhos; dentro de cada
/// grupo, por nome sem caixa (`to_lowercase`) e, no empate, pelo nome exato.
/// Atalhos nunca são expandidos (Decision 7). Nomes que não são UTF-8 válido são omitidos,
/// pois não cabem no payload JSON sem alterar o nome.
pub fn list_dir(root: &Path, relative_path: &str) -> AppResult<Vec<TreeEntry>> {
    let resolved = resolve(root, "", relative_path)?;
    if !fs::metadata(&resolved.absolute)?.is_dir() {
        return Err(AppError::new(ErrorCode::NotFound, "não é uma pasta"));
    }

    let mut entries: Vec<TreeEntry> = Vec::new();
    for item in fs::read_dir(&resolved.absolute)? {
        let item = item?;
        let Ok(name) = item.file_name().into_string() else {
            continue;
        };
        if name.starts_with('.') {
            continue;
        }
        let Ok(file_type) = item.file_type() else {
            continue;
        };
        let relative = if resolved.relative.is_empty() {
            name.clone()
        } else {
            format!("{}/{}", resolved.relative, name)
        };

        let entry = if file_type.is_symlink() {
            let target = classify_symlink(root, &item.path());
            let is_markdown = target == SymlinkTarget::InsideFile && has_extension(&name, "md");
            TreeEntry {
                name,
                relative_path: relative,
                kind: EntryKind::Symlink,
                access: Access::Ok,
                is_markdown,
                symlink_target: Some(target),
            }
        } else if file_type.is_dir() {
            let access = if fs::read_dir(item.path()).is_ok() {
                Access::Ok
            } else {
                Access::Denied
            };
            TreeEntry {
                name,
                relative_path: relative,
                kind: EntryKind::Directory,
                access,
                is_markdown: false,
                symlink_target: None,
            }
        } else {
            let is_markdown = has_extension(&name, "md");
            TreeEntry {
                name,
                relative_path: relative,
                kind: EntryKind::File,
                access: Access::Ok,
                is_markdown,
                symlink_target: None,
            }
        };
        entries.push(entry);
    }

    entries.sort_by_cached_key(|e| {
        let group = u8::from(e.kind != EntryKind::Directory);
        (group, e.name.to_lowercase(), e.name.clone())
    });
    Ok(entries)
}

fn classify_symlink(root: &Path, link: &Path) -> SymlinkTarget {
    match fs::canonicalize(link) {
        Err(_) => SymlinkTarget::Broken,
        Ok(target) if !target.starts_with(root) => SymlinkTarget::Outside,
        Ok(target) => match fs::metadata(&target) {
            Ok(m) if m.is_dir() => SymlinkTarget::InsideDirectory,
            Ok(_) => SymlinkTarget::InsideFile,
            Err(_) => SymlinkTarget::Broken,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::paths::canonical_root;
    use std::os::unix::fs::{symlink, PermissionsExt};
    use std::time::Instant;

    fn names(entries: &[TreeEntry]) -> Vec<&str> {
        entries.iter().map(|e| e.name.as_str()).collect()
    }

    fn root_of(tmp: &tempfile::TempDir) -> std::path::PathBuf {
        canonical_root(tmp.path()).unwrap()
    }

    #[test]
    fn orders_folders_first_then_case_insensitive_and_omits_hidden() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        fs::create_dir(root.join("Zeta")).unwrap();
        fs::create_dir(root.join("alfa")).unwrap();
        fs::create_dir(root.join(".oculto")).unwrap();
        fs::write(root.join(".env"), "x").unwrap();
        fs::write(root.join("b.md"), "x").unwrap();
        fs::write(root.join("A.md"), "x").unwrap();
        fs::write(root.join("notas.txt"), "x").unwrap();
        let entries = list_dir(&root, "").unwrap();
        assert_eq!(
            names(&entries),
            ["alfa", "Zeta", "A.md", "b.md", "notas.txt"]
        );
        assert!(
            entries
                .iter()
                .find(|e| e.name == "A.md")
                .unwrap()
                .is_markdown
        );
        assert!(
            !entries
                .iter()
                .find(|e| e.name == "notas.txt")
                .unwrap()
                .is_markdown
        );
    }

    #[test]
    fn lists_only_requested_level_with_relative_paths() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        fs::create_dir_all(root.join("Artigos/sub")).unwrap();
        fs::write(root.join("Artigos/artigo.md"), "x").unwrap();
        let entries = list_dir(&root, "Artigos").unwrap();
        assert_eq!(names(&entries), ["sub", "artigo.md"]);
        assert_eq!(entries[1].relative_path, "Artigos/artigo.md");
    }

    #[test]
    fn marks_symlinks_without_expanding() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        let outside = tempfile::tempdir().unwrap();
        fs::create_dir(root.join("pasta")).unwrap();
        fs::write(root.join("alvo.md"), "x").unwrap();
        symlink(outside.path(), root.join("atalho-fora")).unwrap();
        symlink(root.join("pasta"), root.join("atalho-pasta")).unwrap();
        symlink(root.join("alvo.md"), root.join("atalho-md.md")).unwrap();
        symlink(root.join("nao-existe"), root.join("quebrado")).unwrap();
        let entries = list_dir(&root, "").unwrap();
        let get = |n: &str| entries.iter().find(|e| e.name == n).unwrap().clone();
        assert_eq!(
            get("atalho-fora").symlink_target,
            Some(SymlinkTarget::Outside)
        );
        assert_eq!(get("atalho-fora").kind, EntryKind::Symlink);
        assert_eq!(
            get("atalho-pasta").symlink_target,
            Some(SymlinkTarget::InsideDirectory)
        );
        assert_eq!(
            get("atalho-md.md").symlink_target,
            Some(SymlinkTarget::InsideFile)
        );
        assert!(get("atalho-md.md").is_markdown);
        assert!(!get("atalho-pasta").is_markdown);
        assert_eq!(get("quebrado").symlink_target, Some(SymlinkTarget::Broken));
        // Pasta real vem antes dos atalhos.
        assert_eq!(entries[0].name, "pasta");
    }

    #[test]
    fn symlink_cycle_does_not_hang_and_is_not_expanded() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        fs::create_dir(root.join("a")).unwrap();
        symlink(&root, root.join("a/volta")).unwrap();
        let entries = list_dir(&root, "a").unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].kind, EntryKind::Symlink);
        // Navegar "através" do atalho de ciclo continua confinado.
        let again = list_dir(&root, "a/volta").unwrap();
        assert_eq!(names(&again), ["a"]);
    }

    #[test]
    fn symlinked_dir_pointing_outside_cannot_be_listed() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        let outside = tempfile::tempdir().unwrap();
        fs::write(outside.path().join("x.md"), "x").unwrap();
        symlink(outside.path(), root.join("fora")).unwrap();
        let e = list_dir(&root, "fora").unwrap_err();
        assert_eq!(e.code, ErrorCode::OutsideFolder);
    }

    #[test]
    fn unreadable_subfolder_is_marked_denied() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        let locked = root.join("trancada");
        fs::create_dir(&locked).unwrap();
        fs::set_permissions(&locked, fs::Permissions::from_mode(0o000)).unwrap();
        // Processo privilegiado (root) lê qualquer pasta: nesse caso não há o que verificar.
        let privileged = fs::read_dir(&locked).is_ok();
        let entries = list_dir(&root, "").unwrap();
        fs::set_permissions(&locked, fs::Permissions::from_mode(0o755)).unwrap();
        if !privileged {
            let entry = entries.iter().find(|e| e.name == "trancada").unwrap();
            assert_eq!(entry.access, Access::Denied);
        }
    }

    #[test]
    fn listing_a_denied_folder_returns_permission_denied() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        let locked = root.join("trancada");
        fs::create_dir(&locked).unwrap();
        fs::set_permissions(&locked, fs::Permissions::from_mode(0o000)).unwrap();
        let privileged = fs::read_dir(&locked).is_ok();
        let result = list_dir(&root, "trancada");
        fs::set_permissions(&locked, fs::Permissions::from_mode(0o755)).unwrap();
        if !privileged {
            assert_eq!(result.unwrap_err().code, ErrorCode::PermissionDenied);
        }
    }

    #[test]
    fn missing_dir_is_not_found_and_outside_is_outside() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        assert_eq!(
            list_dir(&root, "nada").unwrap_err().code,
            ErrorCode::NotFound
        );
        assert_eq!(
            list_dir(&root, "../x").unwrap_err().code,
            ErrorCode::OutsideFolder
        );
    }

    #[test]
    fn five_thousand_items_first_level_within_two_seconds() {
        let tmp = tempfile::tempdir().unwrap();
        let root = root_of(&tmp);
        for i in 0..2_500 {
            fs::write(root.join(format!("arquivo-{i:05}.md")), "x").unwrap();
        }
        for i in 0..2_500 {
            fs::create_dir(root.join(format!("pasta-{i:05}"))).unwrap();
        }
        let start = Instant::now();
        let entries = list_dir(&root, "").unwrap();
        let elapsed = start.elapsed();
        assert_eq!(entries.len(), 5_000);
        assert_eq!(entries[0].kind, EntryKind::Directory);
        assert!(
            elapsed.as_secs_f64() <= 2.0,
            "listagem levou {elapsed:?}, acima dos 2 s do SC-002"
        );
    }
}
