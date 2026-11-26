// Estado reativo das configurações: efeito imediato na interface (FR-021), gravação via
// `save_settings` (FR-022) e "Restaurar padrões" (FR-024). `lastFolder` nunca chega à interface.

import * as api from "./api";
import { errorMessage } from "./messages";
import {
  SIDEBAR_WIDTH_DEFAULT,
  SIDEBAR_WIDTH_MAX,
  SIDEBAR_WIDTH_MIN,
  type AppError,
  type FontSize,
  type UserSettings,
} from "./types";

/** Tamanho do texto do visualizador (data-model.md §2.2.1; valores de projeto). */
export const FONT_PX: Record<FontSize, string> = { small: "14px", medium: "16px", large: "19px" };

export const DEFAULT_SETTINGS: UserSettings = {
  schemaVersion: 1,
  appearance: "system",
  viewerFontSize: "medium",
  sidebarCollapsed: false,
  sidebarWidth: SIDEBAR_WIDTH_DEFAULT,
};

export function clampSidebarWidth(width: number): number {
  return Math.min(SIDEBAR_WIDTH_MAX, Math.max(SIDEBAR_WIDTH_MIN, Math.round(width)));
}

export interface SettingsApi {
  saveSettings(settings: UserSettings): Promise<UserSettings>;
  resetSettings(): Promise<UserSettings>;
}

/** Aplica aparência e tamanho do texto ao documento (CSSOM, sem atributo `style` inline). */
export function applySettings(settings: UserSettings, prefersDark: boolean, doc: Document = document): void {
  const resolved = settings.appearance === "system" ? (prefersDark ? "dark" : "light") : settings.appearance;
  const root = doc.documentElement;
  root.dataset.theme = resolved;
  root.style.setProperty("--viewer-font-size", FONT_PX[settings.viewerFontSize]);
}

function systemPrefersDark(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)").matches
    : false;
}

export class SettingsStore {
  current = $state<UserSettings>({ ...DEFAULT_SETTINGS });
  saveError = $state<string | null>(null);
  private chain: Promise<unknown> = Promise.resolve();
  private mediaQuery: MediaQueryList | null = null;

  constructor(private readonly client: SettingsApi = api) {}

  /** Carrega as configurações iniciais (de `get_startup_state`) e passa a seguir o sistema, se pedido. */
  init(initial: UserSettings): void {
    this.current = { ...initial };
    this.apply();
    if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
      this.mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
      this.mediaQuery.addEventListener("change", () => this.apply());
    }
  }

  private apply(): void {
    applySettings(this.current, this.mediaQuery?.matches ?? systemPrefersDark());
  }

  /** Efeito imediato; se a gravação falhar, volta ao valor anterior e mostra o aviso. */
  update(patch: Partial<UserSettings>): Promise<void> {
    const task = this.chain.then(async () => {
      const previous = { ...this.current };
      const next: UserSettings = { ...previous, ...patch };
      if (patch.sidebarWidth !== undefined) next.sidebarWidth = clampSidebarWidth(patch.sidebarWidth);
      this.current = next;
      this.apply();
      try {
        await this.client.saveSettings(next);
        this.saveError = null;
      } catch (error) {
        this.current = previous;
        this.apply();
        this.saveError = errorMessage((error as AppError).code ?? "io", "settings");
      }
    });
    this.chain = task;
    return task;
  }

  restoreDefaults(): Promise<void> {
    const task = this.chain.then(async () => {
      try {
        this.current = { ...(await this.client.resetSettings()) };
        this.apply();
        this.saveError = null;
      } catch (error) {
        this.saveError = errorMessage((error as AppError).code ?? "io", "settings");
      }
    });
    this.chain = task;
    return task;
  }
}

export const settings = new SettingsStore();
