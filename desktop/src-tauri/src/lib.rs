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
pub mod state;

use tauri::webview::NewWindowResponse;
use tauri::{Manager, Url, WebviewWindowBuilder};

/// A janela só navega para a origem do próprio app: o esquema `tauri` em produção e
/// `http://localhost` somente em desenvolvimento (research R9.3, tarefa 2.3.4).
pub fn is_allowed_navigation(url: &Url, dev: bool) -> bool {
    url.scheme() == "tauri"
        || (dev && url.scheme() == "http" && url.host_str() == Some("localhost"))
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let config_dir = app.path().app_config_dir()?;
            let log_dir = app.path().app_log_dir()?;
            app.manage(state::AppState::new(config_dir, log_dir));

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
        ])
        .run(tauri::generate_context!())
        .expect("erro ao iniciar o aplicativo");
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
