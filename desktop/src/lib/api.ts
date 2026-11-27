// Único ponto de `invoke()` da interface (contracts/commands.md). Nenhum outro módulo chama o
// núcleo Rust diretamente. Nenhum caminho absoluto sai da interface: só caminhos relativos à
// pasta aberta.

import { invoke } from "@tauri-apps/api/core";
import {
  ERROR_CODES,
  type AppError,
  type ErrorCode,
  type FirstRunStep,
  type GuideProgress,
  type InfraSnapshot,
  type LifecycleOutcome,
  type ListDirResult,
  type MarkdownDocument,
  type OpenFolderResult,
  type OperationLog,
  type PreviousPoolAction,
  type ResolveLinkResult,
  type StackPorts,
  type StackSettings,
  type StackSettingsResponse,
  type StartupState,
  type TreeEntry,
  type UserSettings,
  type WorkerPool,
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

// --- Infraestrutura (contracts/tauri-commands.md). Nenhum comando recebe nem devolve credencial. ---

export function getInfraSnapshot(): Promise<InfraSnapshot> {
  return call<InfraSnapshot>("get_infra_snapshot");
}

export function refreshInfra(): Promise<InfraSnapshot> {
  return call<InfraSnapshot>("refresh_infra");
}

export function startStack(): Promise<LifecycleOutcome> {
  return call<LifecycleOutcome>("start_stack");
}

export function stopStack(): Promise<LifecycleOutcome> {
  return call<LifecycleOutcome>("stop_stack");
}

export function startWorkers(): Promise<LifecycleOutcome> {
  return call<LifecycleOutcome>("start_workers");
}

export function stopWorkers(): Promise<LifecycleOutcome> {
  return call<LifecycleOutcome>("stop_workers");
}

export function resolvePreviousPool(action: PreviousPoolAction): Promise<WorkerPool> {
  return call<WorkerPool>("resolve_previous_pool", { action });
}

/** `confirmed` é só UX: o controle real do download é o diálogo nativo aberto pelo Rust (M16). */
export function prepareImages(confirmed: boolean): Promise<FirstRunStep> {
  return call<FirstRunStep>("prepare_images", { confirmed });
}

export function prepareModels(confirmed: boolean): Promise<FirstRunStep> {
  return call<FirstRunStep>("prepare_models", { confirmed });
}

export function getStackSettings(): Promise<StackSettingsResponse> {
  return call<StackSettingsResponse>("get_stack_settings");
}

/** `repoPath` e `dataDir` precisam ser iguais aos gravados: só mudam pelos seletores nativos. */
export function saveStackSettings(settings: StackSettings, ports: StackPorts | null): Promise<StackSettingsResponse> {
  return call<StackSettingsResponse>("save_stack_settings", { settings, ports });
}

export function pickRepoFolder(): Promise<StackSettings> {
  return call<StackSettings>("pick_repo_folder");
}

export function pickDataDir(): Promise<StackSettings> {
  return call<StackSettings>("pick_data_dir");
}

export function saveGuideProgress(guide: GuideProgress | null): Promise<StackSettings> {
  return call<StackSettings>("save_guide_progress", { guide });
}

export function getOperationLog(): Promise<OperationLog> {
  return call<OperationLog>("get_operation_log");
}

/** Avisa o núcleo que o guia abriu ou fechou: com ele aberto o runtime é verificado a cada 5 s. */
export async function setGuideOpen(open: boolean): Promise<void> {
  await call<null>("set_guide_open", { open });
}

/** Versão do macOS (ex.: "14.5.1"), ou `null` se não foi possível ler. Só alimenta o guia. */
export function getMacosVersion(): Promise<string | null> {
  return call<string | null>("get_macos_version");
}
