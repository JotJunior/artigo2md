// @vitest-environment node
// Forma do Worker de renderização em desenvolvimento e em produção (tarefas 9.2.1 e 9.2.2,
// research R9.12). No `tauri dev` o Vite serve o Worker sem empacotar, com `import` ESM: se o
// Worker for clássico, o WebKit falha com SyntaxError e todo documento cai no aviso de falha de
// renderização. No build de produção o Worker tem de continuar clássico (IIFE, piso macOS 11),
// servido de 'self', sem `blob:`/`data:`, para caber na CSP de produção sem afrouxá-la.

import { build, createServer, type Rollup } from "vite";
import { afterEach, describe, expect, it, vi } from "vitest";
import config from "../src-tauri/tauri.conf.json";
import { defaultWorkerFactory } from "../src/lib/render";

// Sem @types/node no projeto: caminho do disco a partir da URL `file:` do próprio teste.
const root = decodeURIComponent(new URL("..", import.meta.url).pathname);
const configFile = decodeURIComponent(new URL("../vite.config.ts", import.meta.url).pathname);

/** Primeira declaração `import` (estática) no topo de uma linha: inválida em Worker clássico. */
const ESM_IMPORT = /^\s*import\s*(?:[\w*{"'])/m;

/** Opções com que `defaultWorkerFactory` cria o Worker (com `Worker` global simulado). */
function requestedWorkerType(): string {
  let options: WorkerOptions | undefined;
  vi.stubGlobal(
    "Worker",
    class {
      constructor(_url: URL | string, opts?: WorkerOptions) {
        options = opts;
      }
    },
  );
  defaultWorkerFactory();
  return options?.type ?? "classic";
}

function directive(csp: string, name: string): string[] {
  const part = csp.split(";").map((p) => p.trim().split(/\s+/)).find((tokens) => tokens[0] === name);
  return part ? part.slice(1) : [];
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Worker de renderização em desenvolvimento (tauri dev)", () => {
  it("o tipo pedido casa com o código que o Vite serve: clássico nunca recebe `import` ESM", async () => {
    // O Vitest também serve sem empacotar (comando `serve`), como o `vite` do `tauri dev`.
    const type = requestedWorkerType();

    const server = await createServer({
      root,
      configFile,
      logLevel: "silent",
      server: { middlewareMode: true, hmr: false, ws: false },
    });
    try {
      const main = await server.transformRequest("/src/lib/render.ts");
      expect(main?.code).toContain(`render.worker.ts?worker_file&type=${type}`);
      const worker = await server.transformRequest(`/src/lib/render.worker.ts?worker_file&type=${type}`);
      expect(worker).not.toBeNull();
      const code = worker!.code;
      // O Worker importa o renderizador; em desenvolvimento o import chega sem empacotar.
      expect(code).toContain("/src/lib/markdown.ts");
      if (type === "classic") {
        // Com o bug: `import { renderMarkdown } from "/src/lib/markdown.ts"` num Worker clássico.
        expect(code).not.toMatch(ESM_IMPORT);
      } else {
        expect(type).toBe("module");
      }
    } finally {
      await server.close();
    }
  }, 60_000);
});

describe("Worker de renderização no build de produção", () => {
  it("é clássico, IIFE, servido de 'self' e cabe na CSP de produção sem afrouxá-la", async () => {
    // Como no `npm run build`: sem NODE_ENV (o Vitest define "test"), o Vite usa "production".
    vi.stubEnv("NODE_ENV", undefined);
    let result: Awaited<ReturnType<typeof build>>;
    try {
      result = await build({ root, configFile, logLevel: "silent", build: { write: false, emptyOutDir: false } });
    } finally {
      vi.unstubAllEnvs();
    }
    const outputs = (Array.isArray(result) ? result : [result]) as Rollup.RollupOutput[];
    const files = outputs.flatMap((o) => o.output);
    const source = (f: (typeof files)[number]): string =>
      f.type === "chunk" ? f.code : typeof f.source === "string" ? f.source : new TextDecoder().decode(f.source);

    const entry = files.find((f) => f.type === "chunk" && f.isEntry);
    expect(entry).toBeDefined();
    const entryCode = source(entry!);
    const instantiations = entryCode.match(/new Worker\([^;]*?import\.meta\.url\)\s*(?:,[^)]*)?\)/g) ?? [];
    expect(instantiations).toHaveLength(1);
    // Sem segundo argumento: Worker clássico (o mesmo verificado no .app, research R9.12).
    expect(instantiations[0]).toMatch(/^new Worker\(new URL\(`\/assets\/render\.worker-[\w-]+\.js`,``\+import\.meta\.url\)\)$/);
    expect(entryCode).not.toMatch(/new Worker\([^)]*(blob:|data:)/);

    const workerFile = files.find((f) => /^assets\/render\.worker-[\w-]+\.js$/.test(f.fileName));
    expect(workerFile).toBeDefined();
    const workerCode = source(workerFile!);
    expect(workerCode.startsWith("(function(){")).toBe(true);
    expect(workerCode).not.toMatch(ESM_IMPORT);
    expect(workerCode).not.toMatch(/^\s*export\s/m);
    expect(workerCode).not.toContain("importScripts");

    // A CSP de produção libera Workers só de 'self' (nada de blob:, data: ou 'unsafe-eval').
    const csp = config.app.security.csp;
    expect(directive(csp, "worker-src")).toEqual(["'self'"]);
    expect(csp).not.toContain("unsafe-eval");
    expect(csp).not.toContain("unsafe-inline");
  }, 120_000);
});
