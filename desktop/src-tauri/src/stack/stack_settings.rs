//! `stack-settings.json`: configurações desta feature (spec FR-019; research Decision 16;
//! data-model.md StackSettings e GuideProgress; plan.md M1, M18).
//!
//! Arquivo próprio em `app_config_dir`, ao lado do `settings.json` do desktop-shell, com o mesmo
//! padrão dele: `camelCase`, `deny_unknown_fields`, `schemaVersion`, escrita atômica (temporário na
//! mesma pasta + rename, aqui com modo `0600`) e um `Mutex` para todas as gravações.
//!
//! `repoPath` e `dataDir` só mudam pelos métodos `set_repo_path`/`set_data_dir`, chamados pelos
//! comandos que abrem o seletor nativo de pasta pelo Rust (M1). `save_from_ui` recusa qualquer
//! valor diferente dos gravados nesses dois campos.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard};

use serde::{Deserialize, Deserializer, Serialize};

use crate::error::{AppError, AppResult, ErrorCode};
use crate::stack::data_dir::default_data_dir;
use crate::stack::instance_lock::ensure_private_dir;

pub const FILE_NAME: &str = "stack-settings.json";
const TEMP_NAME: &str = "stack-settings.json.tmp";
pub const SCHEMA_VERSION: u32 = 1;

/// Padrões do worker, medidos em `docs/specs/conversion-queue/ram-measurement.md`; devem ser
/// iguais a `DEFAULT_WORKERS` e `DEFAULT_MIN_FREE_MEMORY_MB` de
/// `src/artigo2md/jobqueue/settings.py` (um teste confere; não os altere sem refazer a medição).
pub const DEFAULT_WORKERS: u32 = 4;
pub const DEFAULT_MIN_FREE_MEMORY_MB: u32 = 4822;

/// Maior número de passos entre os três runtimes do guia de instalação (colima: 4). O conteúdo
/// vive em `desktop/src/lib/guide.ts`; `desktop/tests/guide.test.ts` lê esta constante e confere que
/// ela é igual à maior contagem real, então acrescentar um passo ao guia obriga a atualizá-la aqui.
pub const MAX_GUIDE_STEPS: u32 = 4;

/// Campo obrigatório que aceita `null`: campo ausente continua sendo erro.
fn required_nullable<'de, D, T>(d: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(d)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum GuideRuntime {
    DockerDesktop,
    Orbstack,
    Colima,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct GuideProgress {
    /// `null` = ainda na comparação dos runtimes.
    #[serde(deserialize_with = "required_nullable")]
    pub runtime: Option<GuideRuntime>,
    pub step: u32,
}

impl GuideProgress {
    fn is_valid(&self) -> bool {
        self.step < MAX_GUIDE_STEPS
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct StackSettings {
    pub schema_version: u32,
    #[serde(deserialize_with = "required_nullable")]
    pub repo_path: Option<String>,
    pub workers: u32,
    pub min_free_memory_mb: u32,
    pub data_dir: String,
    #[serde(deserialize_with = "required_nullable")]
    pub guide: Option<GuideProgress>,
}

impl StackSettings {
    /// Padrões: `workers` 4, `minFreeMemoryMb` 4822 e `dataDir` = `~/artigo2md-dados` expandido.
    pub fn defaults(home: &Path) -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            repo_path: None,
            workers: DEFAULT_WORKERS,
            min_free_memory_mb: DEFAULT_MIN_FREE_MEMORY_MB,
            data_dir: default_data_dir(home).to_string_lossy().into_owned(),
            guide: None,
        }
    }

    /// Regras de `data-model.md`: `schemaVersion` 1, `workers` ≥ 1, caminhos absolutos
    /// (`minFreeMemoryMb` ≥ 0 já é garantido pelo tipo sem sinal).
    pub fn is_valid(&self) -> bool {
        self.schema_version == SCHEMA_VERSION
            && self.workers >= 1
            && Path::new(&self.data_dir).is_absolute()
            && self
                .repo_path
                .as_deref()
                .is_none_or(|p| Path::new(p).is_absolute())
            && self.guide.as_ref().is_none_or(GuideProgress::is_valid)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoadOutcome {
    pub settings: StackSettings,
    /// `true` quando o arquivo existia mas estava ilegível ou inválido (aviso `reset`).
    pub reset: bool,
}

pub struct StackSettingsStore {
    dir: PathBuf,
    home: PathBuf,
    lock: Mutex<()>,
}

impl StackSettingsStore {
    pub fn new(dir: PathBuf, home: PathBuf) -> Self {
        Self {
            dir,
            home,
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

    fn defaults(&self) -> StackSettings {
        StackSettings::defaults(&self.home)
    }

    fn load_unlocked(&self) -> LoadOutcome {
        let bytes = match fs::read(self.file()) {
            Ok(b) => b,
            Err(e) if e.kind() == io::ErrorKind::NotFound => {
                return LoadOutcome {
                    settings: self.defaults(),
                    reset: false,
                }
            }
            Err(_) => return self.reset_outcome(),
        };
        match serde_json::from_slice::<StackSettings>(&bytes) {
            Ok(s) if s.is_valid() => LoadOutcome {
                settings: s,
                reset: false,
            },
            _ => self.reset_outcome(),
        }
    }

    fn reset_outcome(&self) -> LoadOutcome {
        LoadOutcome {
            settings: self.defaults(),
            reset: true,
        }
    }

    /// Lê as configurações: ausente → padrões sem aviso; ilegível/inválido → padrões com aviso.
    /// O arquivo inválido não é sobrescrito até a próxima gravação.
    pub fn load(&self) -> LoadOutcome {
        let _g = self.guard();
        self.load_unlocked()
    }

    /// `save_stack_settings`: valida o JSON recebido da interface e recusa `repoPath`/`dataDir`
    /// diferentes dos gravados (plan.md M1) antes de gravar o resto (`workers`,
    /// `minFreeMemoryMb`, `guide`).
    pub fn save_from_ui(&self, value: &serde_json::Value) -> AppResult<StackSettings> {
        let _g = self.guard();
        let incoming = self.check_unlocked(value)?;
        write_atomic(&self.dir, &incoming)?;
        Ok(incoming)
    }

    /// Mesmas validações de `save_from_ui`, sem gravar: permite validar tudo antes de tocar em
    /// outro arquivo (o `.env`) e só então gravar.
    pub fn check_from_ui(&self, value: &serde_json::Value) -> AppResult<StackSettings> {
        let _g = self.guard();
        self.check_unlocked(value)
    }

    fn check_unlocked(&self, value: &serde_json::Value) -> AppResult<StackSettings> {
        let incoming: StackSettings = serde_json::from_value(value.clone()).map_err(|e| {
            AppError::new(
                ErrorCode::InvalidStackSettings,
                format!("esquema inválido: {e}"),
            )
        })?;
        if !incoming.is_valid() {
            return Err(AppError::new(
                ErrorCode::InvalidStackSettings,
                "valor fora das regras de StackSettings",
            ));
        }
        let current = self.load_unlocked().settings;
        if incoming.repo_path != current.repo_path || incoming.data_dir != current.data_dir {
            return Err(AppError::new(
                ErrorCode::InvalidStackSettings,
                "repoPath e dataDir só mudam pelo seletor nativo de pasta",
            ));
        }
        Ok(incoming)
    }

    /// Caminho do repositório escolhido pelo seletor nativo (único caminho que muda `repoPath`).
    pub fn set_repo_path(&self, repo_path: Option<String>) -> AppResult<StackSettings> {
        self.update(|s| s.repo_path = repo_path)
    }

    /// Pasta de dados escolhida pelo seletor nativo (único caminho que muda `dataDir`).
    pub fn set_data_dir(&self, data_dir: String) -> AppResult<StackSettings> {
        self.update(|s| s.data_dir = data_dir)
    }

    /// `save_guide_progress`: grava só o progresso do guia (`step` dentro do limite).
    pub fn set_guide(&self, guide: Option<GuideProgress>) -> AppResult<StackSettings> {
        if guide.as_ref().is_some_and(|g| !g.is_valid()) {
            return Err(AppError::new(
                ErrorCode::InvalidStackSettings,
                "passo do guia fora do número de passos do runtime",
            ));
        }
        self.update(|s| s.guide = guide)
    }

    /// Espelho de leitura de `ARTIGO2MD_DATA_DIR` do `.env` (research Decision 5): corrige
    /// `dataDir` a partir do `.env`, sem passar pela interface.
    pub fn mirror_data_dir(&self, data_dir: String) -> AppResult<StackSettings> {
        self.set_data_dir(data_dir)
    }

    fn update(&self, change: impl FnOnce(&mut StackSettings)) -> AppResult<StackSettings> {
        let _g = self.guard();
        let mut current = self.load_unlocked().settings;
        change(&mut current);
        if !current.is_valid() {
            return Err(AppError::new(
                ErrorCode::InvalidStackSettings,
                "valor fora das regras de StackSettings",
            ));
        }
        write_atomic(&self.dir, &current)?;
        Ok(current)
    }
}

/// Escrita atômica com modo `0600`: temporário na mesma pasta + `fsync` + rename (plan.md M18).
fn write_atomic(dir: &Path, settings: &StackSettings) -> AppResult<()> {
    ensure_private_dir(dir)?;
    let json = serde_json::to_vec_pretty(settings)
        .map_err(|e| AppError::new(ErrorCode::Io, e.to_string()))?;
    let temp = dir.join(TEMP_NAME);
    {
        let mut f: File = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(&temp)?;
        f.write_all(&json)?;
        f.sync_all()?;
    }
    fs::rename(&temp, dir.join(FILE_NAME))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::Arc;

    fn store(tmp: &tempfile::TempDir) -> StackSettingsStore {
        StackSettingsStore::new(tmp.path().join("config"), tmp.path().join("home"))
    }

    fn json_of(s: &StackSettings) -> serde_json::Value {
        serde_json::to_value(s).unwrap()
    }

    #[test]
    fn defaults_without_file_have_no_warning() {
        let tmp = tempfile::tempdir().unwrap();
        let out = store(&tmp).load();
        assert!(!out.reset);
        let s = out.settings;
        assert_eq!(s.schema_version, 1);
        assert_eq!(s.repo_path, None);
        assert_eq!(s.workers, 4);
        assert_eq!(s.min_free_memory_mb, 4822);
        assert_eq!(
            s.data_dir,
            tmp.path().join("home/artigo2md-dados").to_string_lossy()
        );
        assert_eq!(s.guide, None);
    }

    /// 6.1.3: os padrões do Rust são os de `jobqueue/settings.py`, sem alterá-los.
    #[test]
    fn defaults_match_the_python_worker_defaults() {
        let py = include_str!("../../../../src/artigo2md/jobqueue/settings.py");
        let read = |name: &str| -> u32 {
            let line = py
                .lines()
                .find(|l| l.starts_with(&format!("{name} = ")))
                .unwrap_or_else(|| panic!("{name} não encontrado"));
            line.split('=').nth(1).unwrap().trim().parse().unwrap()
        };
        assert_eq!(read("DEFAULT_WORKERS"), DEFAULT_WORKERS);
        assert_eq!(
            read("DEFAULT_MIN_FREE_MEMORY_MB"),
            DEFAULT_MIN_FREE_MEMORY_MB
        );
    }

    #[test]
    fn serializes_in_camel_case_with_required_null_fields() {
        let tmp = tempfile::tempdir().unwrap();
        let v = json_of(&store(&tmp).load().settings);
        assert_eq!(v["schemaVersion"], 1);
        assert!(v["repoPath"].is_null());
        assert!(v["guide"].is_null());
        assert_eq!(v["minFreeMemoryMb"], 4822);
        assert_eq!(v.as_object().unwrap().len(), 6);
    }

    #[test]
    fn invalid_values_are_refused() {
        let tmp = tempfile::tempdir().unwrap();
        let st = store(&tmp);
        let base = json_of(&st.load().settings);
        let cases = [
            ("workers", json!(0)),
            ("minFreeMemoryMb", json!(-1)),
            ("schemaVersion", json!(2)),
            ("dataDir", json!("relativo/pasta")),
            ("repoPath", json!("relativo")),
            (
                "guide",
                json!({"runtime": "colima", "step": MAX_GUIDE_STEPS}),
            ),
            ("guide", json!({"runtime": "podman", "step": 0})),
        ];
        for (field, value) in cases {
            let mut v = base.clone();
            v[field] = value;
            let err = st.save_from_ui(&v).unwrap_err();
            assert_eq!(err.code, ErrorCode::InvalidStackSettings, "{field}");
        }
        // Nada foi gravado por tentativas recusadas.
        assert!(!st.file().exists());
    }

    #[test]
    fn unknown_field_is_refused_on_save_and_resets_on_load() {
        let tmp = tempfile::tempdir().unwrap();
        let st = store(&tmp);
        let mut v = json_of(&st.load().settings);
        v["extra"] = json!(1);
        assert_eq!(
            st.save_from_ui(&v).unwrap_err().code,
            ErrorCode::InvalidStackSettings
        );

        fs::create_dir_all(&st.dir).unwrap();
        fs::write(st.file(), serde_json::to_vec(&v).unwrap()).unwrap();
        let out = st.load();
        assert!(out.reset);
        assert_eq!(out.settings, st.defaults());
    }

    #[test]
    fn invalid_file_resets_to_defaults_with_warning_and_garbage_too() {
        let tmp = tempfile::tempdir().unwrap();
        let st = store(&tmp);
        fs::create_dir_all(&st.dir).unwrap();
        fs::write(st.file(), b"{ nao e json").unwrap();
        let out = st.load();
        assert!(out.reset);
        assert_eq!(out.settings.workers, 4);
        // Ausente de novo: sem aviso.
        fs::remove_file(st.file()).unwrap();
        assert!(!st.load().reset);
    }

    #[test]
    fn save_persists_between_reopenings_with_mode_0600() {
        let tmp = tempfile::tempdir().unwrap();
        let st = store(&tmp);
        let mut v = json_of(&st.load().settings);
        v["workers"] = json!(2);
        v["minFreeMemoryMb"] = json!(0);
        v["guide"] = json!({"runtime": "orbstack", "step": 3});
        let saved = st.save_from_ui(&v).unwrap();
        assert_eq!(saved.workers, 2);

        let reopened = store(&tmp).load();
        assert!(!reopened.reset);
        assert_eq!(reopened.settings, saved);
        assert_eq!(
            reopened.settings.guide,
            Some(GuideProgress {
                runtime: Some(GuideRuntime::Orbstack),
                step: 3
            })
        );
        let mode = fs::metadata(st.file()).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
        assert!(!st.dir.join(TEMP_NAME).exists());
    }

    #[test]
    fn repo_and_data_dir_change_only_through_the_setters() {
        let tmp = tempfile::tempdir().unwrap();
        let st = store(&tmp);
        let base = json_of(&st.load().settings);

        let mut v = base.clone();
        v["repoPath"] = json!("/Users/atacante/repo");
        assert_eq!(
            st.save_from_ui(&v).unwrap_err().code,
            ErrorCode::InvalidStackSettings
        );
        let mut v = base.clone();
        v["dataDir"] = json!("/tmp/outra");
        assert_eq!(
            st.save_from_ui(&v).unwrap_err().code,
            ErrorCode::InvalidStackSettings
        );

        st.set_repo_path(Some("/abs/repo".into())).unwrap();
        st.set_data_dir("/abs/dados".into()).unwrap();
        let now = st.load().settings;
        assert_eq!(now.repo_path.as_deref(), Some("/abs/repo"));
        assert_eq!(now.data_dir, "/abs/dados");
        // Com os valores gravados, o save da interface passa.
        let mut v = json_of(&now);
        v["workers"] = json!(3);
        assert_eq!(st.save_from_ui(&v).unwrap().workers, 3);
        assert_eq!(
            st.set_repo_path(Some("relativo".into())).unwrap_err().code,
            ErrorCode::InvalidStackSettings
        );
    }

    #[test]
    fn guide_step_is_bounded() {
        let tmp = tempfile::tempdir().unwrap();
        let st = store(&tmp);
        let ok = GuideProgress {
            runtime: None,
            step: 0,
        };
        assert_eq!(st.set_guide(Some(ok)).unwrap().guide, Some(ok));
        let bad = GuideProgress {
            runtime: Some(GuideRuntime::Colima),
            step: MAX_GUIDE_STEPS,
        };
        assert_eq!(
            st.set_guide(Some(bad)).unwrap_err().code,
            ErrorCode::InvalidStackSettings
        );
        assert_eq!(st.set_guide(None).unwrap().guide, None);
    }

    #[test]
    fn concurrent_updates_do_not_lose_writes() {
        let tmp = tempfile::tempdir().unwrap();
        let st = Arc::new(store(&tmp));
        let handles: Vec<_> = (0..8u32)
            .map(|i| {
                let st = Arc::clone(&st);
                std::thread::spawn(move || {
                    st.set_guide(Some(GuideProgress {
                        runtime: None,
                        step: i % MAX_GUIDE_STEPS,
                    }))
                    .unwrap();
                })
            })
            .collect();
        for h in handles {
            h.join().unwrap();
        }
        let out = st.load();
        assert!(!out.reset);
        assert!(out.settings.guide.is_some());
    }
}
