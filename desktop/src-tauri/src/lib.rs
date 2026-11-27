// Núcleo Rust do app desktop (plan.md). Todo acesso ao disco e todo confinamento de caminho
// ficam aqui; a interface só recebe dados já validados.

pub mod commands;
pub mod document;
pub mod error;
pub mod fs_tree;
pub mod image;
pub mod limits;
pub mod link;
pub mod log;
pub mod model;
pub mod paths;
pub mod settings;
pub mod stack;
pub mod state;

use std::sync::atomic::{AtomicBool, Ordering};

use tauri::webview::NewWindowResponse;
use tauri::{Manager, RunEvent, Url, WebviewWindowBuilder, WindowEvent};

/// A janela só navega para a origem do próprio app: o esquema `tauri` em produção e
/// `http://localhost` somente em desenvolvimento (research R9.3, tarefa 2.3.4).
pub fn is_allowed_navigation(url: &Url, dev: bool) -> bool {
    url.scheme() == "tauri"
        || (dev && url.scheme() == "http" && url.host_str() == Some("localhost"))
}

/// O encerramento do pool já começou (evita iniciar dois).
static CLOSING: AtomicBool = AtomicBool::new(false);
/// O encerramento terminou: o próximo pedido de saída passa direto.
static CLOSE_DONE: AtomicBool = AtomicBool::new(false);

/// Fechar o app (FR-024; plan.md M21): enquanto houver pool ou operação em andamento, o pedido de
/// saída é interceptado, o pool para com a parada calma (a interface mostra "Encerrando workers…" pelo
/// estado `stopping` do retrato) e as operações recebem `SIGTERM` e depois `SIGKILL`, tudo em até 60 s.
/// A stack nunca é derrubada. Devolve `true` se interceptou a saída.
fn intercept_close(app: &tauri::AppHandle) -> bool {
    if CLOSE_DONE.load(Ordering::SeqCst) {
        return false;
    }
    let ctx = app.state::<stack::config::StackContext>();
    let infra = app.state::<stack::InfraState>();
    if !stack::ops::close_work_pending(&ctx, &infra) {
        return false;
    }
    if !CLOSING.swap(true, Ordering::SeqCst) {
        let handle = app.clone();
        std::thread::spawn(move || {
            let infra = handle.state::<stack::InfraState>();
            let report = stack::ops::shutdown_for_close(
                &infra,
                stack::ops::CLOSE_CEILING,
                stack::ops::OPERATION_TERM_GRACE,
            );
            if report.timed_out {
                let text = format!(
                    "fechamento atingiu o teto de {} s; processos remanescentes (PID): {:?}",
                    stack::ops::CLOSE_CEILING.as_secs(),
                    report.remaining
                );
                handle
                    .state::<state::AppState>()
                    .log
                    .error("closeCeiling", &stack::secret::Masked::non_secret(&text));
            }
            CLOSE_DONE.store(true, Ordering::SeqCst);
            handle.exit(0);
        });
    }
    true
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let config_dir = app.path().app_config_dir()?;
            let log_dir = app.path().app_log_dir()?;
            let data_dir = app.path().app_data_dir()?;
            app.manage(state::AppState::new(config_dir.clone(), log_dir));
            let home = app.path().home_dir()?;

            // `app_config_dir` e `app_data_dir` ficam com modo 0700 (plan.md M17). Uma falha aqui
            // vai para o log local e não impede o visualizador de abrir.
            let app_state = app.state::<state::AppState>();
            for dir in [&config_dir, &data_dir] {
                if let Err(e) = stack::instance_lock::ensure_private_dir(dir) {
                    app_state.log.error(
                        e.code.as_str(),
                        &stack::secret::Masked::non_secret(e.detail()),
                    );
                }
            }

            // `lifecycle.lock` (plan.md M17): sem ele (outra instância, ou falha ao abrir) o app
            // fica em modo somente leitura e recusa operações com `anotherInstance`.
            let outcome = match stack::instance_lock::acquire(&data_dir) {
                Ok(outcome) => outcome,
                Err(e) => {
                    app_state.log.error(
                        e.code.as_str(),
                        &stack::secret::Masked::non_secret(e.detail()),
                    );
                    stack::instance_lock::LockOutcome::Busy
                }
            };
            app.manage(stack::config::StackContext::new(
                config_dir,
                home,
                stack::instance_lock::InstanceGate::new(outcome),
            ));
            // Retrato da infraestrutura: o temporizador do núcleo o atualiza a cada 2 s.
            app.manage(stack::InfraState::with_record_dir(data_dir.clone()));
            stack::spawn_timer(app.handle().clone());
            // Pool deixado pela sessão anterior (FR-021): resolver o `uv` pode levar segundos, então
            // a conferência roda fora da thread de interface.
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let ctx = handle.state::<stack::config::StackContext>();
                let infra = handle.state::<stack::InfraState>();
                let _ = stack::ops::detect_previous_pool(&ctx, &infra);
            });

            // A janela principal é criada aqui (e não pela config) para poder restringir a
            // navegação e bloquear novas janelas (FR-015).
            let window_config = app
                .config()
                .app
                .windows
                .first()
                .cloned()
                .ok_or("tauri.conf.json sem janela principal")?;
            WebviewWindowBuilder::from_config(app, &window_config)?
                .on_navigation(|url| is_allowed_navigation(url, cfg!(dev)))
                .on_new_window(|_url, _features| NewWindowResponse::Deny)
                .build()?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_startup_state,
            commands::open_folder,
            commands::list_dir,
            commands::read_markdown,
            commands::read_image,
            commands::resolve_link,
            commands::save_settings,
            commands::reset_settings,
            commands::get_stack_settings,
            commands::save_stack_settings,
            commands::pick_repo_folder,
            commands::pick_data_dir,
            commands::save_guide_progress,
            commands::get_infra_snapshot,
            commands::refresh_infra,
            commands::get_operation_log,
            commands::set_guide_open,
            commands::get_macos_version,
            commands::start_stack,
            commands::stop_stack,
            commands::start_workers,
            commands::stop_workers,
            commands::resolve_previous_pool,
            commands::prepare_images,
            commands::prepare_models,
        ])
        .build(tauri::generate_context!())
        .expect("erro ao iniciar o aplicativo");

    app.run(|handle, event| match event {
        RunEvent::WindowEvent {
            event: WindowEvent::CloseRequested { api, .. },
            ..
        } => {
            if intercept_close(handle) {
                api.prevent_close();
            }
        }
        RunEvent::ExitRequested { api, code, .. } => {
            // Saída pedida pela pessoa (Cmd+Q): `code` é `None`. A saída programática de
            // `intercept_close` (`Some`) já vem depois do encerramento e passa direto.
            if code.is_none() && intercept_close(handle) {
                api.prevent_exit();
            }
        }
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn url(s: &str) -> Url {
        Url::parse(s).unwrap()
    }

    #[test]
    fn production_allows_only_the_app_origin() {
        assert!(is_allowed_navigation(
            &url("tauri://localhost/index.html"),
            false
        ));
        assert!(!is_allowed_navigation(&url("https://example.org/"), false));
        assert!(!is_allowed_navigation(
            &url("http://localhost:1420/"),
            false
        ));
        assert!(!is_allowed_navigation(&url("file:///etc/passwd"), false));
    }

    #[test]
    fn development_also_allows_localhost() {
        assert!(is_allowed_navigation(&url("http://localhost:1420/"), true));
        assert!(!is_allowed_navigation(&url("http://evil.example/"), true));
        assert!(!is_allowed_navigation(&url("https://localhost/"), true));
    }
}
