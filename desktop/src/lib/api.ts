// Único ponto de `invoke()` da interface (contracts/commands.md). Nenhum outro módulo chama o
// núcleo Rust diretamente. Nenhum caminho absoluto sai da interface: só caminhos relativos à
// pasta aberta.

import { invoke } from "@tauri-apps/api/core";
import {
  ERROR_CODES,
  type AppError,
  type ErrorCode,
  type ListDirResult,
  type MarkdownDocument,
  type OpenFolderResult,
  type ResolveLinkResult,
  type StartupState,
  type TreeEntry,
  type UserSettings,
} from "./types";

/** Converte qualquer rejeição do `invoke` em `AppError` (código desconhecido vira `io`). */
export function toAppError(raw: unknown): AppError {
  if (typeof raw === "object" && raw !== null && "code" in raw) {
    const code = (raw as { code: unknown }).code;
    if (typeof code === "string" && (ERROR_CODES as readonly string[]).includes(code)) {
      return { code: code as ErrorCode, detail: null };
    }
  }
  return { code: "io", detail: null };
}

async function call<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  try {
    return await invoke<T>(command, args);
  } catch (raw) {
    throw toAppError(raw);
  }
}

export function getStartupState(): Promise<StartupState> {
  return call<StartupState>("get_startup_state");
}

export async function openFolder(): Promise<OpenFolderResult["folder"]> {
  const result = await call<OpenFolderResult>("open_folder");
  return result.folder;
}

export async function listDir(relativePath: string): Promise<TreeEntry[]> {
  const result = await call<ListDirResult>("list_dir", { relativePath });
  return result.entries;
}

export function readMarkdown(relativePath: string): Promise<MarkdownDocument> {
  return call<MarkdownDocument>("read_markdown", { relativePath });
}

/** Bytes da imagem. O `Response` do Tauri chega ao JS como `ArrayBuffer` (research 2.4.4). */
export async function readImage(documentPath: string, src: string): Promise<ArrayBuffer> {
  const raw = await call<ArrayBuffer | number[]>("read_image", { documentPath, src });
  // Plano B da Decision 5: se vier como array de números, converte.
  return raw instanceof ArrayBuffer ? raw : new Uint8Array(raw).buffer;
}

export async function resolveLink(documentPath: string, href: string): Promise<string> {
  const result = await call<ResolveLinkResult>("resolve_link", { documentPath, href });
  return result.relativePath;
}

export function saveSettings(settings: UserSettings): Promise<UserSettings> {
  return call<UserSettings>("save_settings", { settings });
}

export function resetSettings(): Promise<UserSettings> {
  return call<UserSettings>("reset_settings");
}
