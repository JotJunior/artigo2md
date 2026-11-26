//! Configurações persistidas em `settings.json` (Decision 4; FR-019 a FR-024).
//!
//! Escrita atômica (arquivo temporário na mesma pasta + rename). Todas as gravações
//! (`save_settings`, `reset_settings` e `lastFolder` de `open_folder`/`get_startup_state`)
//! passam pelo mesmo `Mutex`, sem corrida entre elas.

use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard};

use crate::error::{AppError, AppResult, ErrorCode};
use crate::model::{AppSettings, UserSettings};

const FILE_NAME: &str = "settings.json";
const TEMP_NAME: &str = "settings.json.tmp";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoadOutcome {
    pub settings: AppSettings,
    /// `true` quando o arquivo existia mas estava ilegível ou inválido (aviso `settingsReset`).
    pub reset: bool,
}

pub struct SettingsStore {
    dir: PathBuf,
    lock: Mutex<()>,
}

impl SettingsStore {
    pub fn new(dir: PathBuf) -> Self {
        Self {
            dir,
            lock: Mutex::new(()),
        }
    }

    fn guard(&self) -> MutexGuard<'_, ()> {
        self.lock
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn file(&self) -> PathBuf {
        self.dir.join(FILE_NAME)
    }

    fn load_unlocked(&self) -> LoadOutcome {
        let bytes = match fs::read(self.file()) {
            Ok(b) => b,
            Err(e) if e.kind() == io::ErrorKind::NotFound => {
                return LoadOutcome {
                    settings: AppSettings::default(),
                    reset: false,
                }
            }
            Err(_) => return reset_outcome(),
        };
        match serde_json::from_slice::<AppSettings>(&bytes) {
            Ok(s) if s.is_valid() => LoadOutcome {
                settings: s,
                reset: false,
            },
            _ => reset_outcome(),
        }
    }

    /// Lê as configurações: ausente → padrões sem aviso; ilegível/inválido → padrões com aviso.
    /// O arquivo inválido não é sobrescrito até a próxima gravação.
    pub fn load(&self) -> LoadOutcome {
        let _g = self.guard();
        self.load_unlocked()
    }

    /// `save_settings`: valida o JSON recebido, preserva `lastFolder` e grava.
    pub fn save_user(&self, value: &serde_json::Value) -> AppResult<AppSettings> {
        let user: UserSettings = serde_json::from_value(value.clone()).map_err(|e| {
            AppError::new(ErrorCode::InvalidSettings, format!("esquema inválido: {e}"))
        })?;
        if !user.is_valid() {
            return Err(AppError::new(
                ErrorCode::InvalidSettings,
                "valor fora do domínio",
            ));
        }
        let _g = self.guard();
        let current = self.load_unlocked().settings;
        let merged = AppSettings {
            schema_version: user.schema_version,
            appearance: user.appearance,
            viewer_font_size: user.viewer_font_size,
            last_folder: current.last_folder,
            sidebar_collapsed: user.sidebar_collapsed,
            sidebar_width: user.sidebar_width,
        };
        write_atomic(&self.dir, &merged)?;
        Ok(merged)
    }

    /// `reset_settings`: grava os padrões preservando `lastFolder` (FR-024).
    pub fn reset(&self) -> AppResult<AppSettings> {
        let _g = self.guard();
        let current = self.load_unlocked().settings;
        let defaults = AppSettings {
            last_folder: current.last_folder,
            ..AppSettings::default()
        };
        write_atomic(&self.dir, &defaults)?;
        Ok(defaults)
    }

    /// Grava `lastFolder` (só o Rust chama; a interface nunca envia esse campo).
    pub fn set_last_folder(&self, folder: Option<String>) -> AppResult<AppSettings> {
        let _g = self.guard();
        let mut current = self.load_unlocked().settings;
        current.last_folder = folder;
        write_atomic(&self.dir, &current)?;
        Ok(current)
    }
}

fn reset_outcome() -> LoadOutcome {
    LoadOutcome {
        settings: AppSettings::default(),
        reset: true,
    }
}

fn write_atomic(dir: &Path, settings: &AppSettings) -> AppResult<()> {
    fs::create_dir_all(dir)?;
    let json = serde_json::to_vec_pretty(settings)
        .map_err(|e| AppError::new(ErrorCode::Io, e.to_string()))?;
    let temp = dir.join(TEMP_NAME);
    {
        let mut f = File::create(&temp)?;
        f.write_all(&json)?;
        f.sync_all()?;
    }
    fs::rename(&temp, dir.join(FILE_NAME))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Appearance, FontSize};
    use serde_json::json;
    use std::sync::Arc;

    fn user_json(width: u32) -> serde_json::Value {
        json!({
            "schemaVersion": 1,
            "appearance": "dark",
            "viewerFontSize": "large",
            "sidebarCollapsed": true,
            "sidebarWidth": width
        })
    }

    fn store(tmp: &tempfile::TempDir) -> SettingsStore {
        SettingsStore::new(tmp.path().join("config"))
    }

    #[test]
    fn defaults_when_file_is_missing() {
        let tmp = tempfile::tempdir().unwrap();
        let out = store(&tmp).load();
        assert_eq!(out.settings, AppSettings::default());
        assert!(!out.reset);
        assert_eq!(out.settings.appearance, Appearance::System);
        assert_eq!(out.settings.viewer_font_size, FontSize::Medium);
        assert_eq!(out.settings.sidebar_width, 260);
        assert!(!out.settings.sidebar_collapsed);
        assert_eq!(out.settings.last_folder, None);
    }

    #[test]
    fn round_trip_after_save() {
        let tmp = tempfile::tempdir().unwrap();
        let s = store(&tmp);
        let saved = s.save_user(&user_json(300)).unwrap();
        assert_eq!(saved.appearance, Appearance::Dark);
        let again = SettingsStore::new(tmp.path().join("config")).load();
        assert_eq!(again.settings, saved);
        assert!(!again.reset);
        assert_eq!(again.settings.sidebar_width, 300);
        assert!(again.settings.sidebar_collapsed);
    }

    #[test]
    fn unreadable_json_gives_defaults_with_warning_and_is_not_overwritten() {
        let tmp = tempfile::tempdir().unwrap();
        let s = store(&tmp);
        fs::create_dir_all(tmp.path().join("config")).unwrap();
        fs::write(tmp.path().join("config/settings.json"), "{").unwrap();
        let out = s.load();
        assert!(out.reset);
        assert_eq!(out.settings, AppSettings::default());
        // Ler não sobrescreve o arquivo inválido.
        assert_eq!(
            fs::read_to_string(tmp.path().join("config/settings.json")).unwrap(),
            "{"
        );
        // A próxima gravação o substitui por um arquivo válido.
        s.save_user(&user_json(300)).unwrap();
        assert!(!s.load().reset);
    }

    #[test]
    fn invalid_values_in_file_reset_to_defaults() {
        let tmp = tempfile::tempdir().unwrap();
        let s = store(&tmp);
        fs::create_dir_all(tmp.path().join("config")).unwrap();
        let cases = [
            r#"{"schemaVersion":2,"appearance":"dark","viewerFontSize":"small","lastFolder":null,"sidebarCollapsed":false,"sidebarWidth":300}"#,
            r#"{"schemaVersion":1,"appearance":"roxo","viewerFontSize":"small","lastFolder":null,"sidebarCollapsed":false,"sidebarWidth":300}"#,
            r#"{"schemaVersion":1,"appearance":"dark","viewerFontSize":"small","lastFolder":null,"sidebarCollapsed":false,"sidebarWidth":100}"#,
            r#"{"schemaVersion":1,"appearance":"dark","viewerFontSize":"small","lastFolder":null,"sidebarCollapsed":"nao","sidebarWidth":300}"#,
            r#"{"schemaVersion":1,"appearance":"dark","viewerFontSize":"small","sidebarCollapsed":false,"sidebarWidth":300}"#,
        ];
        for text in cases {
            fs::write(tmp.path().join("config/settings.json"), text).unwrap();
            let out = s.load();
            assert!(out.reset, "{text}");
            assert_eq!(out.settings, AppSettings::default());
        }
    }

    #[test]
    fn save_rejects_out_of_domain_values() {
        let tmp = tempfile::tempdir().unwrap();
        let s = store(&tmp);
        for bad in [
            user_json(199),
            user_json(481),
            json!({"schemaVersion":1,"appearance":"roxo","viewerFontSize":"large","sidebarCollapsed":true,"sidebarWidth":300}),
            json!({"schemaVersion":9,"appearance":"dark","viewerFontSize":"large","sidebarCollapsed":true,"sidebarWidth":300}),
            json!("texto"),
        ] {
            let e = s.save_user(&bad).unwrap_err();
            assert_eq!(e.code, ErrorCode::InvalidSettings);
        }
        // Nada foi gravado.
        assert!(!tmp.path().join("config/settings.json").exists());
    }

    #[test]
    fn save_rejects_last_folder_from_the_interface() {
        let tmp = tempfile::tempdir().unwrap();
        let s = store(&tmp);
        let mut v = user_json(300);
        v["lastFolder"] = json!("/etc");
        assert_eq!(
            s.save_user(&v).unwrap_err().code,
            ErrorCode::InvalidSettings
        );
    }

    #[test]
    fn save_settings_does_not_change_last_folder() {
        let tmp = tempfile::tempdir().unwrap();
        let s = store(&tmp);
        s.set_last_folder(Some("/Users/x/pasta".into())).unwrap();
        let saved = s.save_user(&user_json(310)).unwrap();
        assert_eq!(saved.last_folder.as_deref(), Some("/Users/x/pasta"));
        assert_eq!(
            s.load().settings.last_folder.as_deref(),
            Some("/Users/x/pasta")
        );
    }

    #[test]
    fn reset_restores_defaults_and_preserves_last_folder() {
        let tmp = tempfile::tempdir().unwrap();
        let s = store(&tmp);
        s.set_last_folder(Some("/Users/x/pasta".into())).unwrap();
        s.save_user(&user_json(400)).unwrap();
        let reset = s.reset().unwrap();
        assert_eq!(reset.appearance, Appearance::System);
        assert_eq!(reset.viewer_font_size, FontSize::Medium);
        assert_eq!(reset.sidebar_width, 260);
        assert!(!reset.sidebar_collapsed);
        assert_eq!(reset.last_folder.as_deref(), Some("/Users/x/pasta"));
        assert_eq!(s.load().settings, reset);
    }

    #[test]
    fn no_temp_file_left_after_write() {
        let tmp = tempfile::tempdir().unwrap();
        let s = store(&tmp);
        s.save_user(&user_json(300)).unwrap();
        assert!(!tmp.path().join("config/settings.json.tmp").exists());
    }

    #[test]
    fn concurrent_writes_keep_a_valid_file() {
        let tmp = tempfile::tempdir().unwrap();
        let s = Arc::new(store(&tmp));
        let handles: Vec<_> = (0..8)
            .map(|i| {
                let s = Arc::clone(&s);
                std::thread::spawn(move || {
                    for j in 0..10 {
                        match (i + j) % 3 {
                            0 => {
                                s.save_user(&user_json(200 + (i * 10 + j) as u32)).unwrap();
                            }
                            1 => {
                                s.set_last_folder(Some(format!("/pasta/{i}/{j}"))).unwrap();
                            }
                            _ => {
                                s.reset().unwrap();
                            }
                        }
                    }
                })
            })
            .collect();
        for h in handles {
            h.join().unwrap();
        }
        let out = s.load();
        assert!(
            !out.reset,
            "arquivo ficou inválido após gravações concorrentes"
        );
    }
}
