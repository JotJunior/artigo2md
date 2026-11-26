// Configuração do markdown-it + KaTeX (plan.md, Decision 8). Roda no Web Worker: só produz HTML
// (string); a sanitização acontece depois, no thread principal (`sanitize.ts`).

import katex from "katex";
import MarkdownIt from "markdown-it";
import katexPluginModule from "@vscode/markdown-it-katex";
import { messages } from "./messages";
import { classifyImageSrc, classifyLink } from "./refs";

/** Limites do KaTeX fixados na tarefa 2.3.1 (research R9.4; fonte: https://katex.org/docs/options). */
export const KATEX_OPTIONS = {
  throwOnError: false,
  trust: false,
  maxExpand: 1000,
  maxSize: 100,
} as const;

type MarkdownItInstance = InstanceType<typeof MarkdownIt>;
type KatexPlugin = (md: MarkdownItInstance, options?: Record<string, unknown>) => void;

/** O pacote é CommonJS com `exports.default`; conforme o carregador, chega como função ou como módulo. */
function resolveKatexPlugin(mod: unknown): KatexPlugin {
  let current: unknown = mod;
  for (let i = 0; i < 3 && typeof current !== "function"; i += 1) {
    current = (current as { default?: unknown } | undefined)?.default;
  }
  if (typeof current !== "function") {
    throw new Error("Plugin de KaTeX indisponível");
  }
  return current as KatexPlugin;
}

export { classifyImageSrc, classifyLink, decodeReference } from "./refs";
export type { ImageKind, LinkKind } from "./refs";

export function createMarkdown(): MarkdownItInstance {
  const md = new MarkdownIt({
    html: false, // FR-017: HTML bruto é exibido como texto
    linkify: false, // Princípio I: nada vira link por conta própria
    typographer: false,
    breaks: false,
  });

  resolveKatexPlugin(katexPluginModule)(md, { ...KATEX_OPTIONS, katex });

  // Imagem: marcador `data-*`, nunca `<img src>` antes da resolução (plan.md, Regras próprias).
  md.renderer.rules.image = (tokens, idx, options, env, self) => {
    const token = tokens[idx]!;
    const src = String(token.attrGet("src") ?? "");
    const alt = self.renderInlineAsText(token.children ?? [], options, env);
    const e = md.utils.escapeHtml;
    // A classificação é feita aqui: só `src` local vai no marcador. `src` remoto ou absoluto nunca sai
    // do renderer (e o DOMPurify descartaria de qualquer forma um `data-src` com esquema como `data:`).
    const kind = classifyImageSrc(src);
    const srcAttribute = kind === "local" ? ` data-src="${e(src)}"` : "";
    return `<span class="md-image" data-md-image="" data-kind="${kind}"${srcAttribute} data-alt="${e(alt)}">${e(alt)}</span>`;
  };

  // Link: `.md` relativo vira elemento clicável por `data-md-link`; os demais ficam inativos, sem `href`.
  md.renderer.rules.link_open = (tokens, idx) => {
    const href = String(tokens[idx]!.attrGet("href") ?? "");
    if (classifyLink(href) === "markdown") {
      return `<span class="link-md" role="link" tabindex="0" data-md-link="${md.utils.escapeHtml(href)}">`;
    }
    return `<span class="link-inactive" title="${md.utils.escapeHtml(messages.externalLinkInactive)}">`;
  };
  md.renderer.rules.link_close = () => "</span>";

  return md;
}

let shared: MarkdownItInstance | null = null;

/** Converte Markdown em HTML ainda NÃO sanitizado. */
export function renderMarkdown(source: string): string {
  shared ??= createMarkdown();
  return shared.render(source);
}
