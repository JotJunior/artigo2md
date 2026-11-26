import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  applySettings,
  clampSidebarWidth,
  DEFAULT_SETTINGS,
  FONT_PX,
  SettingsStore,
  type SettingsApi,
} from "../src/lib/settings.svelte";
import type { AppError, UserSettings } from "../src/lib/types";

function fakeApi(overrides: Partial<SettingsApi> = {}): SettingsApi & {
  saved: UserSettings[];
} {
  const saved: UserSettings[] = [];
  return {
    saved,
    saveSettings: vi.fn(async (s: UserSettings) => {
      saved.push(s);
      return { ...s };
    }),
    resetSettings: vi.fn(async () => ({ ...DEFAULT_SETTINGS })),
    ...overrides,
  };
}

beforeEach(() => {
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.style.removeProperty("--viewer-font-size");
});

describe("applySettings (FR-020, FR-021)", () => {
  it("claro e escuro viram data-theme; sistema segue a preferência do sistema", () => {
    applySettings({ ...DEFAULT_SETTINGS, appearance: "light" }, true);
    expect(document.documentElement.dataset.theme).toBe("light");
    applySettings({ ...DEFAULT_SETTINGS, appearance: "dark" }, false);
    expect(document.documentElement.dataset.theme).toBe("dark");
    applySettings({ ...DEFAULT_SETTINGS, appearance: "system" }, true);
    expect(document.documentElement.dataset.theme).toBe("dark");
    applySettings({ ...DEFAULT_SETTINGS, appearance: "system" }, false);
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("três tamanhos de texto: 14, 16 e 19 px", () => {
    for (const [size, px] of [["small", "14px"], ["medium", "16px"], ["large", "19px"]] as const) {
      applySettings({ ...DEFAULT_SETTINGS, viewerFontSize: size }, false);
      expect(document.documentElement.style.getPropertyValue("--viewer-font-size")).toBe(px);
      expect(FONT_PX[size]).toBe(px);
    }
  });
});

describe("SettingsStore", () => {
  it("efeito imediato e gravação sem lastFolder (FR-021, FR-022, H1)", async () => {
    const api = fakeApi();
    const store = new SettingsStore(api);
    store.init({ ...DEFAULT_SETTINGS });
    const pending = store.update({ appearance: "dark", viewerFontSize: "large" });
    // O efeito é visível antes da gravação terminar.
    await Promise.resolve();
    await pending;
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(store.current.appearance).toBe("dark");
    expect(store.current.viewerFontSize).toBe("large");
    expect(api.saved).toHaveLength(1);
    expect(api.saved[0]).not.toHaveProperty("lastFolder");
    expect(api.saved[0]).toMatchObject({ schemaVersion: 1, appearance: "dark", viewerFontSize: "large" });
    expect(store.current).not.toHaveProperty("lastFolder");
    expect(store.saveError).toBeNull();
  });

  it("gravação recusada volta ao valor anterior e mostra invalidSettings", async () => {
    const error: AppError = { code: "invalidSettings", detail: null };
    const api = fakeApi({ saveSettings: vi.fn().mockRejectedValue(error) });
    const store = new SettingsStore(api);
    store.init({ ...DEFAULT_SETTINGS });
    await store.update({ appearance: "dark" });
    expect(store.current.appearance).toBe("system");
    expect(document.documentElement.dataset.theme).not.toBe("dark");
    expect(store.saveError).toBe("Configuração inválida; nenhuma alteração foi salva");
  });

  it("alterações em sequência são gravadas em ordem", async () => {
    const api = fakeApi();
    const store = new SettingsStore(api);
    store.init({ ...DEFAULT_SETTINGS });
    void store.update({ sidebarCollapsed: true });
    await store.update({ sidebarWidth: 300 });
    expect(api.saved.map((s) => [s.sidebarCollapsed, s.sidebarWidth])).toEqual([
      [true, 260],
      [true, 300],
    ]);
  });

  it("a largura é limitada a 200–480 px", async () => {
    expect(clampSidebarWidth(50)).toBe(200);
    expect(clampSidebarWidth(9999)).toBe(480);
    expect(clampSidebarWidth(300.4)).toBe(300);
    const api = fakeApi();
    const store = new SettingsStore(api);
    store.init({ ...DEFAULT_SETTINGS });
    await store.update({ sidebarWidth: 1000 });
    expect(store.current.sidebarWidth).toBe(480);
  });

  it("Restaurar padrões devolve os valores iniciais (FR-024)", async () => {
    const api = fakeApi();
    const store = new SettingsStore(api);
    store.init({ ...DEFAULT_SETTINGS, appearance: "dark", viewerFontSize: "small", sidebarCollapsed: true, sidebarWidth: 400 });
    await store.restoreDefaults();
    expect(store.current).toEqual(DEFAULT_SETTINGS);
    expect(document.documentElement.style.getPropertyValue("--viewer-font-size")).toBe("16px");
    expect(api.resetSettings).toHaveBeenCalledTimes(1);
  });

  it("falha ao restaurar mostra o aviso e mantém o estado", async () => {
    const api = fakeApi({ resetSettings: vi.fn().mockRejectedValue({ code: "io", detail: null }) });
    const store = new SettingsStore(api);
    store.init({ ...DEFAULT_SETTINGS, appearance: "dark" });
    await store.restoreDefaults();
    expect(store.current.appearance).toBe("dark");
    expect(store.saveError).toBe("Não foi possível concluir a operação por um erro de leitura ou gravação");
  });

  it("os valores padrão não têm lastFolder", () => {
    expect(DEFAULT_SETTINGS).not.toHaveProperty("lastFolder");
  });
});
