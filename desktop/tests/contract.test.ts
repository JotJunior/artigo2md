// Teste de contrato (tarefa 4.1.4, cenário 9 do quickstart): confere a serialização real do Rust,
// gravada por `cargo test` em desktop/src-tauri/target/contract-samples.json, contra os tipos de
// `types.ts` e os enums do contrato. O arquivo é um artefato de build (ignorado pelo git): rode
// `cargo test` antes de `npm test`. Sem ele, os testes que dependem dele são pulados com aviso;
// o restante do arquivo (guardas de formato) roda sempre.

import { describe, expect, it } from "vitest";
import {
  APPEARANCES,
  ERROR_CODES,
  FONT_SIZES,
  type StartupState,
  type TreeEntry,
  type UserSettings,
} from "../src/lib/types";

// `import.meta.glob` devolve `{}` se o arquivo não existir (cargo test ainda não rodou).
const found = import.meta.glob("../src-tauri/target/contract-samples.json", { eager: true, import: "default" });
const samples = (Object.values(found)[0] ?? {}) as Record<string, any>;
const available = Object.keys(found).length > 0;

const ENTRY_KEYS = ["access", "isMarkdown", "kind", "name", "relativePath", "symlinkTarget"];
const SETTINGS_KEYS = ["appearance", "schemaVersion", "sidebarCollapsed", "sidebarWidth", "viewerFontSize"];

function assertSettings(value: UserSettings): void {
  expect(Object.keys(value).sort()).toEqual(SETTINGS_KEYS);
  expect(value.schemaVersion).toBe(1);
  expect(APPEARANCES).toContain(value.appearance);
  expect(FONT_SIZES).toContain(value.viewerFontSize);
  expect(typeof value.sidebarCollapsed).toBe("boolean");
  expect(Number.isInteger(value.sidebarWidth)).toBe(true);
}

function assertEntry(entry: TreeEntry): void {
  expect(Object.keys(entry).sort()).toEqual(ENTRY_KEYS);
  expect(["directory", "file", "symlink"]).toContain(entry.kind);
  expect(["ok", "denied"]).toContain(entry.access);
  expect(typeof entry.isMarkdown).toBe("boolean");
  expect([null, "insideFile", "insideDirectory", "outside", "broken"]).toContain(entry.symlinkTarget);
  expect(entry.relativePath).not.toMatch(/^\//);
}

describe.skipIf(!available)("contrato Rust x types.ts (gerado por cargo test)", () => {
  it("get_startup_state", () => {
    for (const key of ["getStartupState", "getStartupStateWithFolder"]) {
      const value = samples[key] as StartupState;
      expect(Object.keys(value).sort()).toEqual(["folder", "settings", "warnings"]);
      assertSettings(value.settings);
      for (const w of value.warnings) expect(["settingsReset", "lastFolderUnavailable"]).toContain(w);
    }
    expect(samples.getStartupState.folder).toBeNull();
    expect(Object.keys(samples.getStartupStateWithFolder.folder)).toEqual(["displayName"]);
  });

  it("open_folder", () => {
    expect(Object.keys(samples.openFolder)).toEqual(["folder"]);
    expect(Object.keys(samples.openFolder.folder)).toEqual(["displayName"]);
  });

  it("list_dir", () => {
    expect(Object.keys(samples.listDir)).toEqual(["entries"]);
    expect(samples.listDir.entries.length).toBeGreaterThan(0);
    for (const entry of samples.listDir.entries) assertEntry(entry);
  });

  it("read_markdown", () => {
    expect(Object.keys(samples.readMarkdown).sort()).toEqual(["content", "isLarge", "relativePath", "sizeBytes"]);
    expect(typeof samples.readMarkdown.content).toBe("string");
    expect(typeof samples.readMarkdown.isLarge).toBe("boolean");
    expect(Number.isInteger(samples.readMarkdown.sizeBytes)).toBe(true);
  });

  it("resolve_link, save_settings e reset_settings", () => {
    expect(Object.keys(samples.resolveLink)).toEqual(["relativePath"]);
    assertSettings(samples.saveSettings);
    assertSettings(samples.resetSettings);
  });

  it("nenhuma resposta ao webview carrega lastFolder (plan.md, Fronteira de confiança)", () => {
    expect(JSON.stringify(samples)).not.toContain('"lastFolder"');
  });

  it("erros: code do catálogo e detail sempre null", () => {
    const errors = Object.entries(samples.errors) as Array<[string, { code: string; detail: unknown }]>;
    expect(errors.length).toBeGreaterThan(0);
    for (const [name, error] of errors) {
      expect(Object.keys(error).sort()).toEqual(["code", "detail"]);
      expect(error.code).toBe(name);
      expect(ERROR_CODES as readonly string[]).toContain(error.code);
      expect(error.detail).toBeNull();
    }
  });
});

describe("contrato: guardas de formato (sem depender do cargo test)", () => {
  it("os códigos de erro do contrato estão todos em types.ts", () => {
    expect([...ERROR_CODES].sort()).toEqual(
      [
        "folderUnavailable",
        "invalidSettings",
        "io",
        "noFolderOpen",
        "notFound",
        "notMarkdown",
        "notText",
        "outsideFolder",
        "permissionDenied",
        "tooLarge",
        "unsupportedFormat",
      ].sort(),
    );
  });
});
