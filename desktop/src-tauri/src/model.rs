//! Modelos do payload IPC e do `settings.json` (data-model.md). Campos snake_case no Rust,
//! camelCase na serialização.

use serde::{Deserialize, Deserializer, Serialize};

pub const SCHEMA_VERSION: u32 = 1;
pub const SIDEBAR_WIDTH_MIN: u32 = 200;
pub const SIDEBAR_WIDTH_MAX: u32 = 480;
pub const SIDEBAR_WIDTH_DEFAULT: u32 = 260;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Appearance {
    System,
    Light,
    Dark,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum FontSize {
    Small,
    Medium,
    Large,
}

/// Campo obrigatório que aceita `null`: campo ausente continua sendo erro (FR-023).
fn required_nullable<'de, D, T>(d: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(d)
}

/// Conteúdo de `settings.json` e retorno dos comandos de configuração.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AppSettings {
    pub schema_version: u32,
    pub appearance: Appearance,
    pub viewer_font_size: FontSize,
    #[serde(deserialize_with = "required_nullable")]
    pub last_folder: Option<String>,
    pub sidebar_collapsed: bool,
    pub sidebar_width: u32,
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            appearance: Appearance::System,
            viewer_font_size: FontSize::Medium,
            last_folder: None,
            sidebar_collapsed: false,
            sidebar_width: SIDEBAR_WIDTH_DEFAULT,
        }
    }
}

impl AppSettings {
    /// Regras de valor que o serde não cobre (versão e faixa da largura).
    pub fn is_valid(&self) -> bool {
        self.schema_version == SCHEMA_VERSION
            && (SIDEBAR_WIDTH_MIN..=SIDEBAR_WIDTH_MAX).contains(&self.sidebar_width)
    }
}

/// Configurações trocadas com a interface: `AppSettings` sem `lastFolder`. É a entrada de
/// `save_settings` e o retorno de `get_startup_state`, `save_settings` e `reset_settings`;
/// nenhum caminho absoluto chega ao webview (plan.md, "Fronteira de confiança").
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct UserSettings {
    pub schema_version: u32,
    pub appearance: Appearance,
    pub viewer_font_size: FontSize,
    pub sidebar_collapsed: bool,
    pub sidebar_width: u32,
}

impl From<AppSettings> for UserSettings {
    /// Descarta `lastFolder`: só o Rust o conhece.
    fn from(s: AppSettings) -> Self {
        Self {
            schema_version: s.schema_version,
            appearance: s.appearance,
            viewer_font_size: s.viewer_font_size,
            sidebar_collapsed: s.sidebar_collapsed,
            sidebar_width: s.sidebar_width,
        }
    }
}

impl UserSettings {
    pub fn is_valid(&self) -> bool {
        self.schema_version == SCHEMA_VERSION
            && (SIDEBAR_WIDTH_MIN..=SIDEBAR_WIDTH_MAX).contains(&self.sidebar_width)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum EntryKind {
    Directory,
    File,
    Symlink,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum Access {
    Ok,
    Denied,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum SymlinkTarget {
    InsideFile,
    InsideDirectory,
    Outside,
    Broken,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TreeEntry {
    pub name: String,
    pub relative_path: String,
    pub kind: EntryKind,
    pub access: Access,
    pub is_markdown: bool,
    pub symlink_target: Option<SymlinkTarget>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MarkdownDocument {
    pub relative_path: String,
    pub content: String,
    pub size_bytes: u64,
    pub is_large: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OpenFolderInfo {
    pub display_name: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum Warning {
    SettingsReset,
    LastFolderUnavailable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StartupState {
    pub settings: UserSettings,
    pub warnings: Vec<Warning>,
    pub folder: Option<OpenFolderInfo>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OpenFolderResponse {
    pub folder: Option<OpenFolderInfo>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ListDirResponse {
    pub entries: Vec<TreeEntry>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ResolveLinkResponse {
    pub relative_path: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app_settings_serialize_camel_case() {
        let json = serde_json::to_value(AppSettings::default()).unwrap();
        assert_eq!(
            json,
            serde_json::json!({
                "schemaVersion": 1,
                "appearance": "system",
                "viewerFontSize": "medium",
                "lastFolder": null,
                "sidebarCollapsed": false,
                "sidebarWidth": 260
            })
        );
    }

    #[test]
    fn missing_last_folder_is_invalid() {
        let text = r#"{"schemaVersion":1,"appearance":"dark","viewerFontSize":"small","sidebarCollapsed":false,"sidebarWidth":300}"#;
        assert!(serde_json::from_str::<AppSettings>(text).is_err());
    }

    #[test]
    fn user_settings_reject_last_folder() {
        let text = r#"{"schemaVersion":1,"appearance":"dark","viewerFontSize":"small","sidebarCollapsed":false,"sidebarWidth":300,"lastFolder":"/x"}"#;
        assert!(serde_json::from_str::<UserSettings>(text).is_err());
    }

    #[test]
    fn user_settings_from_app_settings_never_expose_last_folder() {
        let app = AppSettings {
            last_folder: Some("/Users/x/pasta".into()),
            ..AppSettings::default()
        };
        let json = serde_json::to_value(UserSettings::from(app)).unwrap();
        assert!(json.get("lastFolder").is_none());
        assert!(!json.to_string().contains("/Users/x"));
    }

    #[test]
    fn width_range_is_enforced() {
        let mut s = AppSettings::default();
        assert!(s.is_valid());
        s.sidebar_width = 199;
        assert!(!s.is_valid());
        s.sidebar_width = 481;
        assert!(!s.is_valid());
        s.sidebar_width = 480;
        assert!(s.is_valid());
    }
}
