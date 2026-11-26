// Espelho dos contratos de docs/specs/desktop-shell/contracts/commands.md e data-model.md.
// Nomes de campo em camelCase, exatamente como o Rust serializa (serde rename_all).

export const ERROR_CODES = [
  "noFolderOpen",
  "folderUnavailable",
  "permissionDenied",
  "notFound",
  "outsideFolder",
  "notMarkdown",
  "notText",
  "unsupportedFormat",
  "tooLarge",
  "invalidSettings",
  "io",
] as const;
export type ErrorCode = (typeof ERROR_CODES)[number];

/** Erro devolvido pela rejeição do `invoke`; `detail` é sempre `null` na interface. */
export interface AppError {
  code: ErrorCode;
  detail: null;
}

export const APPEARANCES = ["system", "light", "dark"] as const;
export type Appearance = (typeof APPEARANCES)[number];

export const FONT_SIZES = ["small", "medium", "large"] as const;
export type FontSize = (typeof FONT_SIZES)[number];

export const SIDEBAR_WIDTH_MIN = 200;
export const SIDEBAR_WIDTH_MAX = 480;
export const SIDEBAR_WIDTH_DEFAULT = 260;

/**
 * Configurações trocadas com o Rust: entrada de `save_settings` e retorno de `get_startup_state`,
 * `save_settings` e `reset_settings`. Não há `lastFolder`: o caminho absoluto fica só no Rust
 * (plan.md, "Fronteira de confiança").
 */
export interface UserSettings {
  schemaVersion: 1;
  appearance: Appearance;
  viewerFontSize: FontSize;
  sidebarCollapsed: boolean;
  sidebarWidth: number;
}

export type Warning = "settingsReset" | "lastFolderUnavailable";

export interface OpenFolderInfo {
  displayName: string;
}

export interface StartupState {
  settings: UserSettings;
  warnings: Warning[];
  folder: OpenFolderInfo | null;
}

export interface OpenFolderResult {
  folder: OpenFolderInfo | null;
}

export type EntryKind = "directory" | "file" | "symlink";
export type Access = "ok" | "denied";
export type SymlinkTarget = "insideFile" | "insideDirectory" | "outside" | "broken";

export interface TreeEntry {
  name: string;
  relativePath: string;
  kind: EntryKind;
  access: Access;
  isMarkdown: boolean;
  symlinkTarget: SymlinkTarget | null;
}

export interface ListDirResult {
  entries: TreeEntry[];
}

export interface MarkdownDocument {
  relativePath: string;
  content: string;
  sizeBytes: number;
  isLarge: boolean;
}

export interface ResolveLinkResult {
  relativePath: string;
}

export type ImageStatus =
  | "loading"
  | "loaded"
  | "remoteBlocked"
  | "notFound"
  | "outsideFolder"
  | "unsupportedFormat"
  | "tooLarge"
  | "readError";

/** Telas da barra lateral. */
export type View = "files" | "settings";
