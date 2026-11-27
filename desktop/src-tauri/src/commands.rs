//! Comandos Tauri (contracts/commands.md). Os comandos são `async` e finos: a lógica testável
//! fica nas funções puras (`startup_state`, `open_folder_path`) e nos módulos de domínio.

use std::path::Path;

use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

use crate::error::{AppError, AppResult, ErrorCode};
use crate::model::{
    ListDirResponse, MarkdownDocument, OpenFolderInfo, OpenFolderResponse, ResolveLinkResponse,
    StartupState, UserSettings, Warning,
};
use crate::paths::canonical_root;
use crate::stack::config::{self, StackContext, StackSettingsResponse};
use crate::stack::first_run::{Confirmer, FirstRunStep};
use crate::stack::lifecycle::LifecycleOutcome;
use crate::stack::ops::{self, PreviousPoolAction};
use crate::stack::pool::WorkerPool;
use crate::stack::secret::Masked;
use crate::stack::stack_settings::StackSettings;
use crate::stack::{self, InfraSnapshot, InfraState};
use crate::state::AppState;
use crate::{document, fs_tree, image, link};

fn folder_info(root: &Path) -> OpenFolderInfo {
    let display_name = root
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| root.display().to_string());
    OpenFolderInfo { display_name }
}

/// `get_startup_state`: configurações (ou padrões), avisos e a pasta reaberta a partir de
/// `lastFolder` (FR-007, FR-023, FR-028).
pub fn startup_state(state: &AppState) -> AppResult<StartupState> {
    let outcome = state.settings.load();
    let mut warnings = Vec::new();
    if outcome.reset {
        warnings.push(Warning::SettingsReset);
    }
    let settings = outcome.settings;
    let mut folder = None;

    state.set_root(None);
    if let Some(last) = settings.last_folder.clone() {
        match canonical_root(Path::new(&last)) {
            Ok(root) => {
                folder = Some(folder_info(&root));
                state.set_root(Some(root));
            }
            Err(e) => {
                state
                    .log
                    .error(e.code.as_str(), &Masked::non_secret(e.detail()));
                warnings.push(Warning::LastFolderUnavailable);
                // Só regrava quando o arquivo estava válido: um arquivo inválido não é
                // sobrescrito até a próxima gravação pedida pela pessoa (data-model.md).
                if !outcome.reset {
                    if let Err(w) = state.settings.set_last_folder(None) {
                        state
                            .log
                            .error(w.code.as_str(), &Masked::non_secret(w.detail()));
                    }
                }
            }
        }
    }
    // A interface recebe as configurações sem `lastFolder` (plan.md, "Fronteira de confiança").
    Ok(StartupState {
        settings: settings.into(),
        warnings,
        folder,
    })
}

/// Efeito de `open_folder` depois que o diálogo devolveu um caminho: guarda a raiz canonizada
/// no estado e grava `lastFolder` (FR-007, FR-022).
pub fn open_folder_path(state: &AppState, picked: &Path) -> AppResult<OpenFolderResponse> {
    let root = canonical_root(picked)?;
    let as_text = root
        .to_str()
        .ok_or_else(|| AppError::new(ErrorCode::Io, "caminho da pasta não é UTF-8"))?
        .to_string();
    let info = folder_info(&root);
    state.set_root(Some(root));
    if let Err(e) = state.settings.set_last_folder(Some(as_text)) {
        // A pasta continua aberta nesta sessão; só a lembrança para a próxima falhou.
        state
            .log
            .error(e.code.as_str(), &Masked::non_secret(e.detail()));
    }
    Ok(OpenFolderResponse { folder: Some(info) })
}

async fn run_blocking<T, F>(f: F) -> AppResult<T>
where
    T: Send + 'static,
    F: FnOnce() -> AppResult<T> + Send + 'static,
{
    tauri::async_runtime::spawn_blocking(f)
        .await
        .map_err(|e| AppError::new(ErrorCode::Io, e.to_string()))?
}

#[tauri::command]
pub async fn get_startup_state(state: State<'_, AppState>) -> Result<StartupState, AppError> {
    state.logged(startup_state(&state))
}

#[tauri::command]
pub async fn open_folder(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<OpenFolderResponse, AppError> {
    // O diálogo nativo é aberto pelo Rust; a interface nunca informa o caminho (gate H1).
    let picked = run_blocking(move || Ok(app.dialog().file().blocking_pick_folder())).await;
    let result = match picked {
        Err(e) => Err(e),
        Ok(None) => Ok(OpenFolderResponse { folder: None }),
        Ok(Some(file_path)) => file_path
            .into_path()
            .map_err(|e| AppError::new(ErrorCode::Io, e.to_string()))
            .and_then(|path| open_folder_path(&state, &path)),
    };
    state.logged(result)
}

#[tauri::command]
pub async fn list_dir(
    state: State<'_, AppState>,
    relative_path: String,
) -> Result<ListDirResponse, AppError> {
    let result = match state.current_root() {
        Ok(root) => run_blocking(move || fs_tree::list_dir(&root, &relative_path))
            .await
            .map(|entries| ListDirResponse { entries }),
        Err(e) => Err(e),
    };
    state.logged(result)
}

#[tauri::command]
pub async fn read_markdown(
    state: State<'_, AppState>,
    relative_path: String,
) -> Result<MarkdownDocument, AppError> {
    let result = match state.current_root() {
        Ok(root) => run_blocking(move || document::read_markdown(&root, &relative_path)).await,
        Err(e) => Err(e),
    };
    state.logged(result)
}

#[tauri::command]
pub async fn read_image(
    state: State<'_, AppState>,
    document_path: String,
    src: String,
) -> Result<tauri::ipc::Response, AppError> {
    let result = match state.current_root() {
        Ok(root) => run_blocking(move || image::read_image(&root, &document_path, &src)).await,
        Err(e) => Err(e),
    };
    state.logged(result).map(tauri::ipc::Response::new)
}

#[tauri::command]
pub async fn resolve_link(
    state: State<'_, AppState>,
    document_path: String,
    href: String,
) -> Result<ResolveLinkResponse, AppError> {
    let result = match state.current_root() {
        Ok(root) => run_blocking(move || link::resolve_link(&root, &document_path, &href))
            .await
            .map(|relative_path| ResolveLinkResponse { relative_path }),
        Err(e) => Err(e),
    };
    state.logged(result)
}

/// Recebe o JSON cru para devolver `invalidSettings` (e não um erro genérico de deserialização
/// do Tauri) quando o valor foge do esquema.
#[tauri::command]
pub async fn save_settings(
    state: State<'_, AppState>,
    settings: serde_json::Value,
) -> Result<UserSettings, AppError> {
    state.logged(state.settings.save_user(&settings).map(UserSettings::from))
}

#[tauri::command]
pub async fn reset_settings(state: State<'_, AppState>) -> Result<UserSettings, AppError> {
    state.logged(state.settings.reset().map(UserSettings::from))
}

/// Abre o seletor nativo de pasta pelo Rust (a webview nunca informa o caminho, plan.md M1).
async fn pick_folder_native(app: AppHandle) -> AppResult<Option<std::path::PathBuf>> {
    let picked = run_blocking(move || Ok(app.dialog().file().blocking_pick_folder())).await?;
    match picked {
        None => Ok(None),
        Some(file_path) => file_path
            .into_path()
            .map(Some)
            .map_err(|e| AppError::new(ErrorCode::Io, e.to_string())),
    }
}

#[tauri::command]
pub async fn get_stack_settings(
    state: State<'_, AppState>,
    stack: State<'_, StackContext>,
) -> Result<StackSettingsResponse, AppError> {
    state.logged(config::get_stack_settings(&stack))
}

/// `settings` e `ports` chegam como `Value` e são conferidos à mão (erro de esquema vira
/// `invalidStackSettings`, não erro de desserialização do Tauri).
#[tauri::command]
pub async fn save_stack_settings(
    state: State<'_, AppState>,
    stack: State<'_, StackContext>,
    settings: serde_json::Value,
    ports: serde_json::Value,
) -> Result<StackSettingsResponse, AppError> {
    state.logged(config::save_stack_settings(&stack, &settings, &ports))
}

#[tauri::command]
pub async fn pick_repo_folder(
    app: AppHandle,
    state: State<'_, AppState>,
    stack: State<'_, StackContext>,
) -> Result<StackSettings, AppError> {
    // Recusa antes de abrir o diálogo; `apply_picked_repo` confere de novo depois dele.
    if let Err(e) = stack.gate.ensure_idle() {
        return state.logged(Err(e));
    }
    let result = match pick_folder_native(app).await {
        Ok(picked) => config::apply_picked_repo(&stack, picked.as_deref()),
        Err(e) => Err(e),
    };
    state.logged(result)
}

#[tauri::command]
pub async fn pick_data_dir(
    app: AppHandle,
    state: State<'_, AppState>,
    stack: State<'_, StackContext>,
) -> Result<StackSettings, AppError> {
    if let Err(e) = stack.gate.ensure_idle() {
        return state.logged(Err(e));
    }
    let result = match pick_folder_native(app).await {
        Ok(picked) => config::apply_picked_data_dir(&stack, picked.as_deref()),
        Err(e) => Err(e),
    };
    state.logged(result)
}

#[tauri::command]
pub async fn save_guide_progress(
    state: State<'_, AppState>,
    stack: State<'_, StackContext>,
    guide: serde_json::Value,
) -> Result<StackSettings, AppError> {
    state.logged(config::save_guide_progress(&stack, &guide))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn product_version_is_read_from_the_plist_text() {
        let plist = "<dict>\n\t<key>ProductName</key>\n\t<string>macOS</string>\n\t<key>ProductVersion</key>\n\t<string>14.5.1</string>\n</dict>";
        assert_eq!(parse_product_version(plist), Some("14.5.1".to_string()));
        assert_eq!(parse_product_version("<dict></dict>"), None);
        assert_eq!(
            parse_product_version("<key>ProductVersion</key><string>x; rm</string>"),
            None
        );
        assert_eq!(
            parse_product_version("<key>ProductVersion</key><string></string>"),
            None
        );
    }

    use crate::model::AppSettings;
    use serde_json::{json, Value};
    use std::fs;
    use std::os::unix::fs::{symlink, PermissionsExt};

    fn new_state(tmp: &tempfile::TempDir) -> AppState {
        AppState::new(tmp.path().join("config"), tmp.path().join("logs"))
    }

    #[test]
    fn startup_without_settings_has_defaults_and_no_folder() {
        let tmp = tempfile::tempdir().unwrap();
        let state = new_state(&tmp);
        let s = startup_state(&state).unwrap();
        assert_eq!(s.settings, UserSettings::from(AppSettings::default()));
        assert!(s.warnings.is_empty());
        assert!(s.folder.is_none());
        assert!(state.current_root().is_err());
    }

    #[test]
    fn open_folder_remembers_last_folder_and_startup_reopens_it() {
        let tmp = tempfile::tempdir().unwrap();
        let sample = tmp.path().join("sample");
        fs::create_dir(&sample).unwrap();
        let state = new_state(&tmp);
        let res = open_folder_path(&state, &sample).unwrap();
        assert_eq!(res.folder.unwrap().display_name, "sample");
        assert!(state.current_root().is_ok());

        // Novo "processo": estado novo sobre o mesmo diretório de configuração.
        let state2 = new_state(&tmp);
        let s = startup_state(&state2).unwrap();
        assert_eq!(s.folder.unwrap().display_name, "sample");
        assert!(s.warnings.is_empty());
        assert!(state2.current_root().is_ok());
    }

    #[test]
    fn removed_last_folder_warns_and_clears_last_folder() {
        let tmp = tempfile::tempdir().unwrap();
        let sample = tmp.path().join("sample");
        fs::create_dir(&sample).unwrap();
        open_folder_path(&new_state(&tmp), &sample).unwrap();
        fs::remove_dir(&sample).unwrap();

        let state = new_state(&tmp);
        let s = startup_state(&state).unwrap();
        assert_eq!(s.warnings, vec![Warning::LastFolderUnavailable]);
        assert!(s.folder.is_none());
        assert_eq!(state.settings.load().settings.last_folder, None);
        assert!(state.current_root().is_err());
        // O aviso não se repete na próxima abertura.
        let s2 = startup_state(&new_state(&tmp)).unwrap();
        assert!(s2.warnings.is_empty());
    }

    #[test]
    fn invalid_settings_file_warns_and_keeps_file_until_next_write() {
        let tmp = tempfile::tempdir().unwrap();
        fs::create_dir_all(tmp.path().join("config")).unwrap();
        fs::write(tmp.path().join("config/settings.json"), "{").unwrap();
        let s = startup_state(&new_state(&tmp)).unwrap();
        assert_eq!(s.warnings, vec![Warning::SettingsReset]);
        assert_eq!(s.settings, UserSettings::from(AppSettings::default()));
        assert_eq!(
            fs::read_to_string(tmp.path().join("config/settings.json")).unwrap(),
            "{"
        );
    }

    #[test]
    fn opening_a_missing_or_denied_folder_fails_without_changing_state() {
        let tmp = tempfile::tempdir().unwrap();
        let state = new_state(&tmp);
        let e = open_folder_path(&state, &tmp.path().join("nao-existe")).unwrap_err();
        assert_eq!(e.code, ErrorCode::FolderUnavailable);

        let locked = tmp.path().join("trancada");
        fs::create_dir(&locked).unwrap();
        fs::set_permissions(&locked, fs::Permissions::from_mode(0o000)).unwrap();
        let privileged = fs::read_dir(&locked).is_ok();
        let result = open_folder_path(&state, &locked);
        fs::set_permissions(&locked, fs::Permissions::from_mode(0o755)).unwrap();
        if !privileged {
            assert_eq!(result.unwrap_err().code, ErrorCode::PermissionDenied);
        }
        assert!(state.current_root().is_err());
    }

    #[test]
    fn reading_without_open_folder_is_no_folder_open() {
        let tmp = tempfile::tempdir().unwrap();
        let state = new_state(&tmp);
        assert_eq!(
            state.current_root().unwrap_err().code,
            ErrorCode::NoFolderOpen
        );
    }

    #[test]
    fn logged_errors_keep_technical_text_only_in_the_log() {
        let tmp = tempfile::tempdir().unwrap();
        let state = new_state(&tmp);
        let r: AppResult<()> = Err(AppError::new(ErrorCode::NotFound, "/Users/x/segredo.md"));
        let err = state.logged(r).unwrap_err();
        let wire = serde_json::to_string(&err).unwrap();
        assert!(!wire.contains("segredo"));
        let log = fs::read_to_string(tmp.path().join("logs/artigo2md.log")).unwrap();
        assert!(log.contains("notFound /Users/x/segredo.md"));
    }

    /// Gera os JSON de exemplo de cada retorno para o teste de contrato do front
    /// (`desktop/tests/contract.test.ts`; quickstart, cenário 9).
    #[test]
    fn write_contract_samples() {
        let tmp = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let sample = tmp.path().join("sample");
        fs::create_dir_all(sample.join("Artigos/imagens")).unwrap();
        fs::write(sample.join("Artigos/artigo.md"), "# Olá\n").unwrap();
        fs::write(sample.join("Artigos/links.md"), "[outro](artigo.md)").unwrap();
        fs::write(sample.join("binario.md"), [0xff, 0xfe, 0xc3, 0x28]).unwrap();
        fs::write(sample.join("notas.txt"), "x").unwrap();
        fs::write(sample.join(".env"), "x").unwrap();
        symlink(outside.path(), sample.join("atalho-fora")).unwrap();
        fs::create_dir(sample.join("trancada")).unwrap();

        let state = new_state(&tmp);
        let startup_before = serde_json::to_value(startup_state(&state).unwrap()).unwrap();
        let open = serde_json::to_value(open_folder_path(&state, &sample).unwrap()).unwrap();
        let root = state.current_root().unwrap();
        let list = serde_json::to_value(ListDirResponse {
            entries: fs_tree::list_dir(&root, "").unwrap(),
        })
        .unwrap();
        let doc =
            serde_json::to_value(document::read_markdown(&root, "Artigos/artigo.md").unwrap())
                .unwrap();
        let link = serde_json::to_value(ResolveLinkResponse {
            relative_path: link::resolve_link(&root, "Artigos/links.md", "artigo.md").unwrap(),
        })
        .unwrap();
        let saved = serde_json::to_value(
            state
                .settings
                .save_user(&json!({
                    "schemaVersion": 1, "appearance": "dark", "viewerFontSize": "large",
                    "sidebarCollapsed": true, "sidebarWidth": 300
                }))
                .map(UserSettings::from)
                .unwrap(),
        )
        .unwrap();
        let reset =
            serde_json::to_value(state.settings.reset().map(UserSettings::from).unwrap()).unwrap();
        let startup_after = serde_json::to_value(startup_state(&new_state(&tmp)).unwrap()).unwrap();

        let mut errors = serde_json::Map::new();
        let err = |e: AppError| serde_json::to_value(e).unwrap();
        errors.insert(
            "outsideFolder".into(),
            err(document::read_markdown(&root, "../../x.md").unwrap_err()),
        );
        errors.insert(
            "notMarkdown".into(),
            err(document::read_markdown(&root, "notas.txt").unwrap_err()),
        );
        errors.insert(
            "notText".into(),
            err(document::read_markdown(&root, "binario.md").unwrap_err()),
        );
        errors.insert(
            "notFound".into(),
            err(document::read_markdown(&root, "nao.md").unwrap_err()),
        );
        errors.insert(
            "noFolderOpen".into(),
            err(new_state(&tmp).current_root().unwrap_err()),
        );
        errors.insert(
            "invalidSettings".into(),
            err(state
                .settings
                .save_user(&json!({"appearance": "roxo"}))
                .unwrap_err()),
        );

        let samples = json!({
            "getStartupState": startup_before,
            "getStartupStateWithFolder": startup_after,
            "openFolder": open,
            "listDir": list,
            "readMarkdown": doc,
            "resolveLink": link,
            "saveSettings": saved,
            "resetSettings": reset,
            "errors": Value::Object(errors),
        });

        // Nenhuma resposta ao webview carrega `lastFolder` nem caminho absoluto da pasta aberta.
        let text = serde_json::to_string_pretty(&samples).unwrap();
        assert!(!text.contains("lastFolder"));
        assert!(!text.contains(tmp.path().to_str().unwrap()));
        // Erros nunca carregam caminho absoluto da máquina para a interface.
        let errors_text = serde_json::to_string(&samples["errors"]).unwrap();
        assert!(!errors_text.contains("/var/") && !errors_text.contains("/private/"));
        let target = Path::new(env!("CARGO_MANIFEST_DIR")).join("target");
        fs::create_dir_all(&target).unwrap();
        fs::write(target.join("contract-samples.json"), text).unwrap();
    }
}

/// `get_infra_snapshot`: só lê o último retrato do temporizador; nunca executa verificação nem
/// pega o `Mutex` de operação (contracts/tauri-commands.md).
#[tauri::command]
pub async fn get_infra_snapshot(
    stack: State<'_, StackContext>,
    infra: State<'_, InfraState>,
) -> Result<InfraSnapshot, AppError> {
    Ok(infra
        .snapshot()
        .unwrap_or_else(|| stack::initial_snapshot(&stack)))
}

/// `refresh_infra`: nova verificação completa ("Verificar novamente", FR-003).
#[tauri::command]
pub async fn refresh_infra(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<InfraSnapshot, AppError> {
    let result = run_blocking(move || {
        let stack = app.state::<StackContext>();
        let infra = app.state::<InfraState>();
        Ok(stack::refresh_infra(&stack, &infra))
    })
    .await;
    state.logged(result)
}

/// Resposta de `get_operation_log`.
#[derive(Debug, serde::Serialize)]
pub struct OperationLog {
    pub lines: Vec<String>,
}

/// `get_operation_log`: saída técnica mascarada da última operação (FR-014, sob demanda).
#[tauri::command]
pub async fn get_operation_log(infra: State<'_, InfraState>) -> Result<OperationLog, AppError> {
    Ok(OperationLog {
        lines: infra.operation_log(),
    })
}

/// `set_guide_open`: avisa o núcleo que o guia de instalação abriu ou fechou, para o runtime ser
/// verificado a cada 5 s enquanto ele está aberto (FR-003, FR-006; research Decision 10).
#[tauri::command]
pub async fn set_guide_open(infra: State<'_, InfraState>, open: bool) -> Result<(), AppError> {
    infra.set_guide_open(open);
    Ok(())
}

/// Arquivo do sistema com a versão do macOS (conferido nesta máquina em 2026-11-27).
const SYSTEM_VERSION_PLIST: &str = "/System/Library/CoreServices/SystemVersion.plist";

/// Extrai `ProductVersion` do texto do plist (`<key>ProductVersion</key>` seguido de `<string>`).
/// Só aceita dígitos e pontos: qualquer outra coisa vira `None`.
fn parse_product_version(plist: &str) -> Option<String> {
    let after_key = plist.split("<key>ProductVersion</key>").nth(1)?;
    let value = after_key
        .split("<string>")
        .nth(1)?
        .split("</string>")
        .next()?
        .trim();
    let valid = !value.is_empty()
        && value.len() <= 16
        && value.chars().all(|c| c.is_ascii_digit() || c == '.')
        && value.chars().next().is_some_and(|c| c.is_ascii_digit());
    valid.then(|| value.to_string())
}

/// `get_macos_version`: versão do macOS para o guia marcar como indisponível o runtime que exige
/// uma versão mais nova (FR-005, research Decision 15). `null` quando não foi possível ler.
#[tauri::command]
pub async fn get_macos_version() -> Result<Option<String>, AppError> {
    Ok(std::fs::read_to_string(SYSTEM_VERSION_PLIST)
        .ok()
        .and_then(|text| parse_product_version(&text)))
}

/// Diálogo nativo de confirmação (plan.md M16): o controle real do download. O `confirmed` da
/// interface é só UX; este diálogo aparece sempre, aberto pelo Rust.
struct NativeConfirmer {
    app: AppHandle,
}

impl Confirmer for NativeConfirmer {
    fn confirm(&self, title: &str, message: &str) -> bool {
        // Chamado dentro de `spawn_blocking`, nunca na thread principal.
        self.app
            .dialog()
            .message(message)
            .title(title)
            .kind(MessageDialogKind::Info)
            .buttons(MessageDialogButtons::OkCancelCustom(
                "Continuar".to_string(),
                "Cancelar".to_string(),
            ))
            .blocking_show()
    }
}

/// `start_stack`: a operação roda em segundo plano e o progresso aparece em `InfraSnapshot.operations`.
#[tauri::command]
pub async fn start_stack(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<LifecycleOutcome, AppError> {
    let result = run_blocking(move || {
        let stack = app.state::<StackContext>();
        let infra = app.state::<InfraState>();
        ops::start_stack(&stack, &infra)
    })
    .await;
    state.logged(result)
}

#[tauri::command]
pub async fn stop_stack(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<LifecycleOutcome, AppError> {
    let result = run_blocking(move || {
        let stack = app.state::<StackContext>();
        let infra = app.state::<InfraState>();
        ops::stop_stack(&stack, &infra)
    })
    .await;
    state.logged(result)
}

#[tauri::command]
pub async fn start_workers(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<LifecycleOutcome, AppError> {
    let result = run_blocking(move || {
        let stack = app.state::<StackContext>();
        let infra = app.state::<InfraState>();
        ops::start_workers(&stack, &infra)
    })
    .await;
    state.logged(result)
}

#[tauri::command]
pub async fn stop_workers(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<LifecycleOutcome, AppError> {
    let result = run_blocking(move || {
        let stack = app.state::<StackContext>();
        let infra = app.state::<InfraState>();
        ops::stop_workers(&stack, &infra)
    })
    .await;
    state.logged(result)
}

/// `action` chega como texto (`"stop"` ou `"keep"`) e é conferido à mão: valor desconhecido é
/// `invalidParams`, não erro de desserialização do Tauri.
#[tauri::command]
pub async fn resolve_previous_pool(
    app: AppHandle,
    state: State<'_, AppState>,
    action: String,
) -> Result<WorkerPool, AppError> {
    let result = run_blocking(move || {
        let action = PreviousPoolAction::parse(&action)?;
        let stack = app.state::<StackContext>();
        let infra = app.state::<InfraState>();
        ops::resolve_previous_pool(&stack, &infra, action)
    })
    .await;
    state.logged(result)
}

/// `confirmed` chega como `Value` e é conferido à mão (ausente, não booleano ou `false` =
/// `confirmationRequired`); mesmo com `true`, o diálogo nativo é mostrado.
#[tauri::command]
pub async fn prepare_images(
    app: AppHandle,
    state: State<'_, AppState>,
    confirmed: Option<serde_json::Value>,
) -> Result<FirstRunStep, AppError> {
    if let Err(e) = ops::require_confirmed(confirmed.as_ref()) {
        return state.logged(Err(e));
    }
    let result = run_blocking(move || {
        let stack = app.state::<StackContext>();
        let infra = app.state::<InfraState>();
        ops::prepare_images(&stack, &infra, &NativeConfirmer { app: app.clone() })
    })
    .await;
    state.logged(result)
}

#[tauri::command]
pub async fn prepare_models(
    app: AppHandle,
    state: State<'_, AppState>,
    confirmed: Option<serde_json::Value>,
) -> Result<FirstRunStep, AppError> {
    if let Err(e) = ops::require_confirmed(confirmed.as_ref()) {
        return state.logged(Err(e));
    }
    let result = run_blocking(move || {
        let stack = app.state::<StackContext>();
        let infra = app.state::<InfraState>();
        ops::prepare_models(&stack, &infra, &NativeConfirmer { app: app.clone() })
    })
    .await;
    state.logged(result)
}
