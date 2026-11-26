import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// A porta 1420 casa com "devUrl" em src-tauri/tauri.conf.json.
export default defineConfig(({ command, mode }) => ({
  plugins: [svelte()],
  clearScreen: false,
  // Worker de renderização de módulo só quando o Vite serve os arquivos sem empacotar (`vite` do
  // `tauri dev`): ali o Worker chega com `import` ESM e um Worker clássico falha com SyntaxError
  // (research R9.12). No build ele continua clássico (IIFE). Depende do comando, não do NODE_ENV.
  define: { __RENDER_WORKER_AS_MODULE__: JSON.stringify(command === "serve") },
  // Nunca embutir ativos como `data:` (a CSP não permite `data:` em `font-src`): as fontes do KaTeX
  // saem como arquivos e são servidas de 'self' (tarefa 2.4.6).
  // `target: "safari14"`: o piso do app é o macOS 11 (Decision 2), cujo WebKit na época do Safari 14 (R1);
  // o padrão do Vite (Safari 16.4+) deixaria sintaxe nova demais para esse piso.
  build: { assetsInlineLimit: 0, target: "safari14" },
  // Componentes Svelte nos testes: usar a build de navegador do Svelte (jsdom), não a de servidor.
  // `dedupe`: o plugin de KaTeX traz uma cópia aninhada de `katex` (0.16.x, GHSA-238p-pmpm-9mq7);
  // forçar a resolução a partir da raiz deixa só o `katex` direto do projeto no pacote (research R9.8).
  resolve: { dedupe: ["katex"], ...(mode === "test" ? { conditions: ["browser"] } : {}) },
  server: {
    port: 1420,
    strictPort: true,
    host: "localhost",
  },
  // Os testes de lógica rodam em jsdom (sem webview real).
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts"],
  },
}));
