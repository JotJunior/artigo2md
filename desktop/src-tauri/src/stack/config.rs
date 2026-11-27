//! Lógica dos comandos de configuração e de seleção de pastas (contracts/tauri-commands.md:
//! `get_stack_settings`, `save_stack_settings`, `pick_repo_folder`, `pick_data_dir`,
//! `save_guide_progress`; plan.md M1, M3, M14).
//!
//! Os comandos Tauri (`commands.rs`) só abrem o seletor nativo e chamam estas funções, que ficam
//! testáveis sem janela. `repoPath` e `dataDir` só mudam por `apply_picked_repo` e
//! `apply_picked_data_dir` (chamados com o caminho devolvido pelo diálogo aberto pelo Rust);
//! `save_stack_settings` recusa qualquer valor diferente dos gravados (M1).

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::error::{AppError, AppResult, ErrorCode};
use crate::stack::data_dir::{self, DataDirStatus};
use crate::stack::env_file::{
    self, EnvEdits, EnvFile, EnvIssue, KEY_API_PORT, KEY_POSTGRES_PORT, KEY_RABBITMQ_PORT,
};
use crate::stack::instance_lock::InstanceGate;
use crate::stack::locate::{locate_all, Located};
use crate::stack::repo;
use crate::stack::stack_settings::{GuideProgress, StackSettings, StackSettingsStore};

/// Estado da feature de infraestrutura, gerenciado pelo Tauri ao lado do `AppState`.
pub struct StackContext {
    pub settings: StackSettingsStore,
    pub gate: InstanceGate,
    pub home: PathBuf,
    located: OnceLock<Located>,
}

impl StackContext {
    pub fn new(config_dir: PathBuf, home: PathBuf, gate: InstanceGate) -> Self {
        Self {
            settings: StackSettingsStore::new(config_dir, home.clone()),
            gate,
            home,
            located: OnceLock::new(),
        }
    }

    /// Como `new`, com os executáveis já resolvidos (testes com `docker` e `uv` falsos).
    pub fn with_located(
        config_dir: PathBuf,
        home: PathBuf,
        gate: InstanceGate,
        located: Located,
    ) -> Self {
        let ctx = Self::new(config_dir, home, gate);
        let _ = ctx.located.set(located);
        ctx
    }

    /// `docker`, `uv` e `colima` resolvidos (uma vez por execução do app; resolver pode levar
    /// até 5 s por executável, por isso só sob demanda e fora da thread de interface).
    pub fn located(&self) -> &Located {
        self.located.get_or_init(|| locate_all(&self.home))
    }
}

/// Portas editáveis (`ports` dos contratos). O painel de gestão não aparece: o app não o publica.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Ports {
    pub postgres: u16,
    pub rabbitmq: u16,
    pub api: u16,
}

impl Ports {
    fn is_valid(&self) -> bool {
        self.postgres >= 1 && self.rabbitmq >= 1 && self.api >= 1
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StackSettingsResponse {
    pub settings: StackSettings,
    /// Vem do `.env`; `null` se ele não existe (a interface mostra os padrões).
    pub ports: Option<Ports>,
}

fn invalid_settings(why: &str) -> AppError {
    AppError::new(ErrorCode::InvalidStackSettings, why)
}

fn repo_path_of(settings: &StackSettings) -> Option<PathBuf> {
    settings.repo_path.as_deref().map(PathBuf::from)
}

fn read_env(settings: &StackSettings) -> AppResult<Option<EnvFile>> {
    match repo_path_of(settings) {
        Some(repo) => env_file::load(&env_file::env_path(&repo)),
        None => Ok(None),
    }
}

fn ports_of(env: &EnvFile) -> AppResult<Ports> {
    let get = |key: &str| {
        env.port(key)
            .ok_or_else(|| EnvIssue::BadPort(key.to_string()).into_error())
    };
    Ok(Ports {
        postgres: get(KEY_POSTGRES_PORT)?,
        rabbitmq: get(KEY_RABBITMQ_PORT)?,
        api: get(KEY_API_PORT)?,
    })
}

/// `get_stack_settings`: as configurações e as portas lidas do `.env`. O `.env` é a fonte da
/// pasta de dados depois de existir: o espelho `dataDir` é atualizado a partir dele.
pub fn get_stack_settings(ctx: &StackContext) -> AppResult<StackSettingsResponse> {
    let mut settings = ctx.settings.load().settings;
    let env = read_env(&settings)?;
    let Some(env) = env else {
        return Ok(StackSettingsResponse {
            settings,
            ports: None,
        });
    };
    if let Some(dir) = env.data_dir() {
        if dir != settings.data_dir && Path::new(dir).is_absolute() {
            settings = ctx.settings.mirror_data_dir(dir.to_string())?;
        }
    }
    let ports = ports_of(&env)?;
    Ok(StackSettingsResponse {
        settings,
        ports: Some(ports),
    })
}

/// `save_stack_settings`: valida tudo antes de gravar; recusa `repoPath`/`dataDir` diferentes
/// dos gravados (`invalidStackSettings`); reescreve só as chaves de porta do `.env` que mudaram.
/// Recusado durante operação em andamento. Com `ports` e sem `.env`, as portas não têm onde ser
/// gravadas (não há cópia delas, Decision 5) e são ignoradas.
pub fn save_stack_settings(
    ctx: &StackContext,
    settings: &Value,
    ports: &Value,
) -> AppResult<StackSettingsResponse> {
    ctx.gate.ensure_idle()?;
    let ports: Option<Ports> = serde_json::from_value(ports.clone())
        .map_err(|e| invalid_settings(&format!("portas inválidas: {e}")))?;
    if ports.is_some_and(|p| !p.is_valid()) {
        return Err(invalid_settings("porta fora de 1 a 65535"));
    }
    let incoming = ctx.settings.check_from_ui(settings)?;

    if let Some(new_ports) = ports {
        if let Some(env) = read_env(&incoming)? {
            let current = ports_of(&env)?;
            if current != new_ports {
                let edits = EnvEdits {
                    postgres_port: (current.postgres != new_ports.postgres)
                        .then_some(new_ports.postgres),
                    rabbitmq_port: (current.rabbitmq != new_ports.rabbitmq)
                        .then_some(new_ports.rabbitmq),
                    api_port: (current.api != new_ports.api).then_some(new_ports.api),
                    data_dir: None,
                };
                let repo = repo_path_of(&incoming)
                    .ok_or_else(|| invalid_settings("repositório não indicado"))?;
                env_file::edit(&env_file::env_path(&repo), &edits)?;
            }
        }
    }
    let saved = ctx.settings.save_from_ui(settings)?;
    let ports = match read_env(&saved)? {
        Some(env) => Some(ports_of(&env)?),
        None => None,
    };
    Ok(StackSettingsResponse {
        settings: saved,
        ports,
    })
}

/// Efeito de `pick_repo_folder` depois que o seletor nativo devolveu `picked`: grava o caminho
/// canônico em `repoPath` (único caminho que o muda). `None` = pessoa cancelou: nada muda.
pub fn apply_picked_repo(ctx: &StackContext, picked: Option<&Path>) -> AppResult<StackSettings> {
    ctx.gate.ensure_idle()?;
    let Some(picked) = picked else {
        return Ok(ctx.settings.load().settings);
    };
    let canonical = repo::canonical_repo(picked)
        .ok_or_else(|| AppError::new(ErrorCode::RepoInvalid, "a pasta escolhida não existe"))?;
    let text = canonical
        .to_str()
        .ok_or_else(|| AppError::new(ErrorCode::RepoInvalid, "caminho da pasta não é UTF-8"))?;
    ctx.settings.set_repo_path(Some(text.to_string()))
}

/// Efeito de `pick_data_dir`: a pasta escolhida precisa passar na verificação (existe, é pasta,
/// gravável, canônica e não é ampla demais, M14). Grava primeiro no `.env` (se existir) e depois
/// no espelho `dataDir` (Decision 5). `None` = cancelou.
pub fn apply_picked_data_dir(
    ctx: &StackContext,
    picked: Option<&Path>,
) -> AppResult<StackSettings> {
    ctx.gate.ensure_idle()?;
    let Some(picked) = picked else {
        return Ok(ctx.settings.load().settings);
    };
    let canonical = std::fs::canonicalize(picked)
        .map_err(|_| AppError::new(ErrorCode::DataDirInvalid, "a pasta escolhida não existe"))?;
    let current = ctx.settings.load().settings;
    let repo = repo_path_of(&current);
    let check = data_dir::check(&canonical, &ctx.home, repo.as_deref(), false);
    if check.status != DataDirStatus::Ok {
        return Err(AppError::new(
            ErrorCode::DataDirInvalid,
            format!("pasta de dados recusada: {:?}", check.status),
        ));
    }
    let text = canonical
        .to_str()
        .ok_or_else(|| AppError::new(ErrorCode::DataDirInvalid, "caminho da pasta não é UTF-8"))?
        .to_string();
    if let Some(repo) = &repo {
        let env = env_file::env_path(repo);
        if std::fs::symlink_metadata(&env).is_ok() {
            env_file::edit(
                &env,
                &EnvEdits {
                    data_dir: Some(text.clone()),
                    ..EnvEdits::default()
                },
            )?;
        }
    }
    ctx.settings.set_data_dir(text)
}

/// `save_guide_progress`: grava só o progresso do guia (`step` dentro do limite).
pub fn save_guide_progress(ctx: &StackContext, guide: &Value) -> AppResult<StackSettings> {
    let guide: Option<GuideProgress> = serde_json::from_value(guide.clone())
        .map_err(|e| invalid_settings(&format!("guia inválido: {e}")))?;
    ctx.settings.set_guide(guide)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stack::instance_lock::{self, LockOutcome, OperationKind};
    use serde_json::json;
    use std::fs;
    use std::os::unix::fs::{symlink, PermissionsExt};

    struct Fx {
        _tmp: tempfile::TempDir,
        root: PathBuf,
        ctx: StackContext,
        repo: PathBuf,
        data: PathBuf,
    }

    fn fixture() -> Fx {
        let tmp = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(tmp.path()).unwrap();
        let home = root.join("home");
        let config = root.join("config");
        let appdata = root.join("appdata");
        fs::create_dir_all(&home).unwrap();
        let lock = match instance_lock::acquire(&appdata).unwrap() {
            LockOutcome::Acquired(l) => l,
            LockOutcome::Busy => panic!("lock ocupado"),
        };
        let ctx = StackContext::new(
            config,
            home.clone(),
            InstanceGate::new(LockOutcome::Acquired(lock)),
        );
        let repo = home.join("repo");
        fs::create_dir_all(repo.join("stack")).unwrap();
        let data = home.join("dados");
        fs::create_dir(&data).unwrap();
        Fx {
            _tmp: tmp,
            root,
            ctx,
            repo,
            data,
        }
    }

    fn env_text(data: &Path) -> String {
        format!(
            "# nota\nARTIGO2MD_DATA_DIR={}\nARTIGO2MD_POSTGRES_PORT=5432\nARTIGO2MD_RABBITMQ_PORT=5672\n\
             ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT=15672\nARTIGO2MD_API_PORT=8778\nPOSTGRES_USER=artigo2md\n\
             POSTGRES_PASSWORD=pg-Segredo-Muito-Longo-123\nPOSTGRES_DB=artigo2md\nRABBITMQ_DEFAULT_USER=artigo2md\n\
             RABBITMQ_DEFAULT_PASS=mq-Segredo-Muito-Longo-456\n",
            data.display()
        )
    }

    fn write_env(f: &Fx, text: &str) {
        let p = f.repo.join("stack/.env");
        fs::write(&p, text).unwrap();
        fs::set_permissions(&p, fs::Permissions::from_mode(0o600)).unwrap();
    }

    fn pick_repo(f: &Fx) {
        apply_picked_repo(&f.ctx, Some(&f.repo)).unwrap();
    }

    fn ui_settings(f: &Fx) -> Value {
        serde_json::to_value(f.ctx.settings.load().settings).unwrap()
    }

    #[test]
    fn get_without_repo_or_env_has_defaults_and_null_ports() {
        let f = fixture();
        let r = get_stack_settings(&f.ctx).unwrap();
        assert_eq!(r.ports, None);
        assert_eq!(r.settings.repo_path, None);
        let v = serde_json::to_value(&r).unwrap();
        assert!(v["ports"].is_null());
        assert!(v["settings"]["repoPath"].is_null());

        pick_repo(&f);
        assert_eq!(get_stack_settings(&f.ctx).unwrap().ports, None);
    }

    #[test]
    fn get_reads_ports_and_mirrors_the_data_dir_from_the_env() {
        let f = fixture();
        pick_repo(&f);
        write_env(&f, &env_text(&f.data));
        let r = get_stack_settings(&f.ctx).unwrap();
        assert_eq!(
            r.ports,
            Some(Ports {
                postgres: 5432,
                rabbitmq: 5672,
                api: 8778
            })
        );
        assert_eq!(r.settings.data_dir, f.data.to_str().unwrap());
        // O espelho foi persistido.
        assert_eq!(
            f.ctx.settings.load().settings.data_dir,
            f.data.to_str().unwrap()
        );
        // A resposta não carrega a porta do painel nem credencial.
        let text = serde_json::to_string(&r).unwrap();
        assert!(!text.contains("15672") && !text.contains("Segredo"));
    }

    #[test]
    fn pick_repo_canonicalizes_and_is_the_only_way_to_set_it() {
        let f = fixture();
        let link = f.root.join("atalho");
        symlink(&f.repo, &link).unwrap();
        let s = apply_picked_repo(&f.ctx, Some(&link)).unwrap();
        assert_eq!(s.repo_path.as_deref(), f.repo.to_str());
        assert_eq!(
            f.ctx.settings.load().settings.repo_path.as_deref(),
            f.repo.to_str()
        );

        // Cancelar devolve o valor anterior.
        let same = apply_picked_repo(&f.ctx, None).unwrap();
        assert_eq!(same.repo_path.as_deref(), f.repo.to_str());
        // Pasta inexistente.
        let err = apply_picked_repo(&f.ctx, Some(&f.root.join("nao-existe"))).unwrap_err();
        assert_eq!(err.code, ErrorCode::RepoInvalid);
        assert_eq!(
            f.ctx.settings.load().settings.repo_path.as_deref(),
            f.repo.to_str()
        );
    }

    #[test]
    fn webview_cannot_point_repo_or_data_dir_through_save() {
        let f = fixture();
        pick_repo(&f);
        let before = f.ctx.settings.load().settings;
        for (field, value) in [
            ("repoPath", json!("/Users/atacante/repo-malicioso")),
            ("repoPath", Value::Null),
            ("dataDir", json!("/tmp/atacante")),
        ] {
            let mut s = ui_settings(&f);
            s[field] = value;
            let err = save_stack_settings(&f.ctx, &s, &Value::Null).unwrap_err();
            assert_eq!(err.code, ErrorCode::InvalidStackSettings, "{field}");
        }
        assert_eq!(f.ctx.settings.load().settings, before);
    }

    #[test]
    fn save_rewrites_only_the_port_keys_that_changed() {
        let f = fixture();
        pick_repo(&f);
        let original = env_text(&f.data);
        write_env(&f, &original);
        let mut s = ui_settings(&f);
        s["workers"] = json!(2);
        let r = save_stack_settings(
            &f.ctx,
            &s,
            &json!({"postgres": 5432, "rabbitmq": 5673, "api": 8778}),
        )
        .unwrap();
        assert_eq!(r.settings.workers, 2);
        assert_eq!(r.ports.unwrap().rabbitmq, 5673);
        let after = fs::read_to_string(f.repo.join("stack/.env")).unwrap();
        assert_eq!(
            after,
            original.replace(
                "ARTIGO2MD_RABBITMQ_PORT=5672",
                "ARTIGO2MD_RABBITMQ_PORT=5673"
            )
        );
        assert_eq!(f.ctx.settings.load().settings.workers, 2);
    }

    #[test]
    fn save_refuses_ports_outside_1_to_65535_without_writing_anything() {
        let f = fixture();
        pick_repo(&f);
        let original = env_text(&f.data);
        write_env(&f, &original);
        let mut s = ui_settings(&f);
        s["workers"] = json!(3);
        for bad in [
            json!({"postgres": 0, "rabbitmq": 5672, "api": 8778}),
            json!({"postgres": 70000, "rabbitmq": 5672, "api": 8778}),
            json!({"postgres": -1, "rabbitmq": 5672, "api": 8778}),
            json!({"postgres": 1, "rabbitmq": 5672}),
            json!({"postgres": 1, "rabbitmq": 5672, "api": 1, "management": 15672}),
            json!("5432"),
        ] {
            let err = save_stack_settings(&f.ctx, &s, &bad).unwrap_err();
            assert_eq!(err.code, ErrorCode::InvalidStackSettings, "{bad}");
        }
        assert_eq!(
            fs::read_to_string(f.repo.join("stack/.env")).unwrap(),
            original
        );
        assert_eq!(f.ctx.settings.load().settings.workers, 4);
    }

    #[test]
    fn invalid_settings_are_validated_before_the_env_is_touched() {
        let f = fixture();
        pick_repo(&f);
        let original = env_text(&f.data);
        write_env(&f, &original);
        let mut s = ui_settings(&f);
        s["workers"] = json!(0);
        let err = save_stack_settings(
            &f.ctx,
            &s,
            &json!({"postgres": 6000, "rabbitmq": 5672, "api": 8778}),
        )
        .unwrap_err();
        assert_eq!(err.code, ErrorCode::InvalidStackSettings);
        assert_eq!(
            fs::read_to_string(f.repo.join("stack/.env")).unwrap(),
            original
        );
    }

    #[test]
    fn save_without_env_keeps_settings_and_ignores_ports() {
        let f = fixture();
        pick_repo(&f);
        let mut s = ui_settings(&f);
        s["minFreeMemoryMb"] = json!(1000);
        let r = save_stack_settings(
            &f.ctx,
            &s,
            &json!({"postgres": 6000, "rabbitmq": 5672, "api": 8778}),
        )
        .unwrap();
        assert_eq!(r.settings.min_free_memory_mb, 1000);
        assert_eq!(r.ports, None);
        assert!(!f.repo.join("stack/.env").exists());
    }

    #[test]
    fn pick_data_dir_writes_env_first_then_the_mirror() {
        let f = fixture();
        pick_repo(&f);
        write_env(&f, &env_text(&f.data));
        let novo = f.ctx.home.join("novos-dados");
        fs::create_dir(&novo).unwrap();
        let s = apply_picked_data_dir(&f.ctx, Some(&novo)).unwrap();
        assert_eq!(s.data_dir, novo.to_str().unwrap());
        let env = fs::read_to_string(f.repo.join("stack/.env")).unwrap();
        assert!(env.contains(&format!("ARTIGO2MD_DATA_DIR={}\n", novo.display())));
        assert!(!env.contains(&f.data.display().to_string()));
        assert_eq!(
            f.ctx.settings.load().settings.data_dir,
            novo.to_str().unwrap()
        );

        // Cancelar mantém.
        assert_eq!(
            apply_picked_data_dir(&f.ctx, None).unwrap().data_dir,
            novo.to_str().unwrap()
        );
    }

    #[test]
    fn pick_data_dir_refuses_broad_unwritable_and_missing_folders() {
        let f = fixture();
        pick_repo(&f);
        let before = f.ctx.settings.load().settings.data_dir;
        let ro = f.ctx.home.join("ro");
        fs::create_dir(&ro).unwrap();
        fs::set_permissions(&ro, fs::Permissions::from_mode(0o500)).unwrap();
        let mut cases = vec![
            f.ctx.home.clone(),
            f.repo.clone(),
            f.root.clone(),
            PathBuf::from("/"),
            f.root.join("nao-existe"),
        ];
        if crate::stack::locate::current_uid() != 0 {
            cases.push(ro.clone());
        }
        for dir in cases {
            let err = apply_picked_data_dir(&f.ctx, Some(&dir)).unwrap_err();
            assert_eq!(err.code, ErrorCode::DataDirInvalid, "{}", dir.display());
        }
        fs::set_permissions(&ro, fs::Permissions::from_mode(0o700)).unwrap();
        assert_eq!(f.ctx.settings.load().settings.data_dir, before);
    }

    #[test]
    fn pickers_and_save_are_refused_during_an_operation_or_in_another_instance() {
        let f = fixture();
        pick_repo(&f);
        let s = ui_settings(&f);
        let guard = f.ctx.gate.begin(OperationKind::StartStack).unwrap();
        assert_eq!(
            apply_picked_repo(&f.ctx, Some(&f.repo)).unwrap_err().code,
            ErrorCode::OperationInProgress
        );
        assert_eq!(
            apply_picked_data_dir(&f.ctx, Some(&f.data))
                .unwrap_err()
                .code,
            ErrorCode::OperationInProgress
        );
        assert_eq!(
            save_stack_settings(&f.ctx, &s, &Value::Null)
                .unwrap_err()
                .code,
            ErrorCode::OperationInProgress
        );
        drop(guard);
        assert!(apply_picked_repo(&f.ctx, Some(&f.repo)).is_ok());

        // Outra instância: somente leitura.
        let busy = StackContext::new(
            f.root.join("config2"),
            f.ctx.home.clone(),
            InstanceGate::new(LockOutcome::Busy),
        );
        assert_eq!(
            apply_picked_repo(&busy, Some(&f.repo)).unwrap_err().code,
            ErrorCode::AnotherInstance
        );
        assert_eq!(
            save_stack_settings(&busy, &s, &Value::Null)
                .unwrap_err()
                .code,
            ErrorCode::AnotherInstance
        );
    }

    #[test]
    fn guide_progress_is_validated_and_persisted() {
        let f = fixture();
        let s = save_guide_progress(&f.ctx, &json!({"runtime": "colima", "step": 2})).unwrap();
        assert_eq!(s.guide.unwrap().step, 2);
        assert_eq!(
            save_guide_progress(&f.ctx, &json!({"runtime": "colima", "step": 9999}))
                .unwrap_err()
                .code,
            ErrorCode::InvalidStackSettings
        );
        assert_eq!(
            save_guide_progress(&f.ctx, &json!({"runtime": "x", "step": 0}))
                .unwrap_err()
                .code,
            ErrorCode::InvalidStackSettings
        );
        assert!(save_guide_progress(&f.ctx, &Value::Null)
            .unwrap()
            .guide
            .is_none());
    }
}
