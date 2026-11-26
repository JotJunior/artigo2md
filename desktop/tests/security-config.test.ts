// Testes da configuração de segurança do Tauri (tarefas 5.1.1 a 5.1.5, SC-004): a CSP não pode
// ter origem remota e as capabilities não podem dar ao webview acesso a disco nem a diálogo.

import { describe, expect, it } from "vitest";
import capability from "../src-tauri/capabilities/default.json";
import pkg from "../package.json";
import config from "../src-tauri/tauri.conf.json";

const security = config.app.security as { csp?: string; devCsp?: string };

function parse(csp: string): Map<string, string[]> {
  const directives = new Map<string, string[]>();
  for (const part of csp.split(";")) {
    const tokens = part.trim().split(/\s+/).filter(Boolean);
    if (tokens.length > 0) directives.set(tokens[0]!, tokens.slice(1));
  }
  return directives;
}

/** Fontes permitidas na CSP de produção: só o próprio app, `blob:` e o IPC do Tauri. */
const PRODUCTION_SOURCES = new Set(["'self'", "'none'", "blob:", "ipc:", "http://ipc.localhost"]);
/** Em desenvolvimento entram também o servidor do Vite (localhost) e `'unsafe-inline'` só para estilos. */
const DEVELOPMENT_SOURCES = new Set([...PRODUCTION_SOURCES, "'unsafe-inline'", "http://localhost:1420", "ws://localhost:1420"]);

const REQUIRED: Record<string, string[]> = {
  "default-src": ["'self'"],
  "img-src": ["'self'", "blob:"],
  "font-src": ["'self'"],
  "script-src": ["'self'"],
  "worker-src": ["'self'"],
  "base-uri": ["'none'"],
  "form-action": ["'none'"],
  "object-src": ["'none'"],
  "frame-src": ["'none'"],
};

describe("CSP (FR-015, SC-004, plan.md §Rede)", () => {
  it("está definida para produção e desenvolvimento", () => {
    expect(typeof security.csp).toBe("string");
    expect(typeof security.devCsp).toBe("string");
  });

  it("não tem nenhuma origem remota", () => {
    for (const [name, csp, allowed] of [
      ["csp", security.csp!, PRODUCTION_SOURCES],
      ["devCsp", security.devCsp!, DEVELOPMENT_SOURCES],
    ] as const) {
      for (const [directive, sources] of parse(csp)) {
        for (const source of sources) {
          expect(allowed.has(source), `${name}: ${directive} ${source}`).toBe(true);
        }
        expect(sources.join(" "), `${name}: ${directive}`).not.toMatch(/\*|https?:\/\/(?!ipc\.localhost|localhost:1420)|data:|unsafe-eval/);
      }
    }
  });

  it("a CSP de produção não usa 'unsafe-inline' em lugar nenhum", () => {
    expect(security.csp).not.toContain("unsafe-inline");
  });

  it("tem as diretivas obrigatórias (inclui base-uri, form-action, object-src e frame-src 'none')", () => {
    for (const csp of [security.csp!, security.devCsp!]) {
      const directives = parse(csp);
      for (const [name, required] of Object.entries(REQUIRED)) {
        for (const source of required) {
          expect(directives.get(name), name).toContain(source);
        }
      }
      expect(directives.get("connect-src")).toEqual(expect.arrayContaining(["ipc:", "http://ipc.localhost"]));
    }
  });

  it("a CSP de produção libera estilos só de 'self' e fontes só de 'self' (KaTeX local)", () => {
    const directives = parse(security.csp!);
    expect(directives.get("style-src")).toEqual(["'self'"]);
    expect(directives.get("font-src")).toEqual(["'self'"]);
  });
});

describe("capabilities (FR-006, tarefa 5.1.2)", () => {
  it("só para a janela principal", () => {
    expect(capability.windows).toEqual(["main"]);
    expect(config.app.windows.map((w) => w.label)).toEqual(["main"]);
  });

  it("sem diálogo, sem arquivos e sem conjunto padrão inteiro no webview", () => {
    const permissions = capability.permissions.map((p) => (typeof p === "string" ? p : (p as { identifier: string }).identifier));
    expect(permissions).not.toContain("dialog:allow-open");
    for (const permission of permissions) {
      expect(permission, permission).not.toMatch(/^(fs|dialog|shell|http|opener|process)[:-]/);
      expect(permission, permission).not.toMatch(/:default$/);
    }
  });

  it("não é remota", () => {
    expect(capability).not.toHaveProperty("remote");
  });
});

describe("tauri.conf.json (FR-025, FR-026, FR-042)", () => {
  it("minimumSystemVersion 11.0 e só o alvo app", () => {
    expect(config.bundle.macOS.minimumSystemVersion).toBe("11.0");
    expect(config.bundle.targets).toEqual(["app"]);
  });

  it("assinatura ad hoc do bundle inteiro, sem identidade de desenvolvedor (FR-026, tarefa 10.1.1)", () => {
    const macOS = config.bundle.macOS as Record<string, unknown>;
    // "-" é a identidade ad hoc do codesign: sela o Info.plist e os recursos (sem ela o macOS recusa
    // o .app em quarentena como "danificado"); não é conta de desenvolvedor nem notarização.
    expect(macOS.signingIdentity).toBe("-");
    expect(macOS).not.toHaveProperty("providerShortName");
    expect(macOS).not.toHaveProperty("entitlements");
  });

  it("o build do .app verifica a assinatura com codesign --verify --deep --strict (tarefa 10.2.1)", () => {
    const scripts = pkg.scripts as Record<string, string>;
    expect(scripts["verify:app"]).toContain("codesign --verify --deep --strict");
    expect(scripts["verify:app"]).toContain("artigo2md.app");
    expect(scripts["build:app"]).toContain("tauri build --bundles app --target aarch64-apple-darwin");
    expect(scripts["build:app"]).toContain("npm run verify:app");
  });

  it("a janela é criada pelo Rust (para restringir a navegação)", () => {
    expect((config.app.windows[0] as { create?: boolean }).create).toBe(false);
  });
});
