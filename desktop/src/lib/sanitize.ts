// Sanitização do HTML renderizado, no thread principal (onde há DOM). Lista explícita de tags e
// atributos (plan.md, Endurecimento de segurança); falha fechada: se o DOMPurify não puder
// sanitizar, o chamador mostra o documento como texto puro.

import DOMPurify, { type Config } from "dompurify";

const HTML_TAGS = [
  "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "b", "i", "del", "s",
  "ul", "ol", "li", "blockquote", "pre", "code", "span", "div", "sup", "sub",
  "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
];

// MathML e SVG que o KaTeX gera (https://katex.org/docs/security: o HTML do KaTeX deve ser sanitizado).
const MATHML_TAGS = [
  "math", "semantics", "annotation", "mrow", "mi", "mo", "mn", "ms", "mtext", "mspace",
  "msup", "msub", "msubsup", "mfrac", "msqrt", "mroot", "mstyle", "mpadded", "mphantom",
  "menclose", "mtable", "mtr", "mtd", "munder", "mover", "munderover", "mmultiscripts",
  "mprescripts", "none",
];
const SVG_TAGS = ["svg", "path", "line", "g"];

const MATHML_ATTRS = [
  "encoding", "mathvariant", "display", "xmlns", "displaystyle", "scriptlevel", "columnalign",
  "columnspacing", "rowspacing", "columnlines", "rowlines", "fence", "stretchy", "separator",
  "lspace", "rspace", "minsize", "maxsize", "mathsize", "voffset", "linethickness", "notation",
  "accent", "accentunder", "movablelimits", "symmetric", "largeop", "form", "mathcolor",
  "mathbackground", "scriptsizemultiplier", "equalrows", "equalcolumns", "frame",
  "framespacing", "side",
];
const SVG_ATTRS = [
  "viewBox", "preserveAspectRatio", "d", "x1", "x2", "y1", "y2", "fill", "stroke",
  "stroke-width", "focusable",
];
const GENERIC_ATTRS = [
  "class", "style", "title", "role", "tabindex", "width", "height", "colspan", "rowspan",
  "align", "aria-hidden", "aria-label",
  "data-md-link", "data-md-image", "data-kind", "data-src", "data-alt",
];

/** Atributos e tags que nunca passam, mesmo que uma lista futura os inclua (plan.md). */
const FORBID_ATTR = ["href", "src", "srcset", "xlink:href", "action", "formaction"];
const FORBID_TAGS = ["form", "iframe", "object", "embed", "use", "script", "style", "link", "meta", "base", "img"];

const CONFIG: Config = {
  ALLOWED_TAGS: [...HTML_TAGS, ...MATHML_TAGS, ...SVG_TAGS],
  ALLOWED_ATTR: [...GENERIC_ATTRS, ...MATHML_ATTRS, ...SVG_ATTRS],
  ALLOW_DATA_ATTR: false,
  ALLOW_ARIA_ATTR: false,
  FORBID_ATTR,
  FORBID_TAGS,
  KEEP_CONTENT: true,
  RETURN_DOM: false,
  RETURN_DOM_FRAGMENT: false,
};

/** `style` só com declarações simples de layout; nada de `url()`, `@import`, `expression`, barras invertidas. */
const SAFE_STYLE = /^[\w\s.,:;%#()+\-/!*]*$/;
const FORBIDDEN_STYLE_TOKENS = /url\s*\(|image-set|image\s*\(|src\s*\(|expression|@import|javascript:|\/\*|var\s*\(/i;

export function isSafeStyleValue(value: string): boolean {
  return SAFE_STYLE.test(value) && !FORBIDDEN_STYLE_TOKENS.test(value);
}

let hooked = false;
function installHooks(): void {
  if (hooked) return;
  hooked = true;
  DOMPurify.addHook("uponSanitizeAttribute", (_node, data) => {
    if (data.attrName === "style" && !isSafeStyleValue(data.attrValue)) {
      data.keepAttr = false;
    }
  });
}

/** Resultado da sanitização; `detail` é o motivo técnico da falha (tarefa 9.1.2). */
export type SanitizeResult = { ok: true; html: string } | { ok: false; detail: string };

/** Sanitiza e, na falha (falha fechada), diz por quê. */
export function sanitizeHtmlChecked(html: string): SanitizeResult {
  try {
    if (!DOMPurify.isSupported) return { ok: false, detail: "DOMPurify.isSupported é false" };
    installHooks();
    return { ok: true, html: String(DOMPurify.sanitize(html, CONFIG)) };
  } catch (error) {
    return { ok: false, detail: error instanceof Error ? `${error.name}: ${error.message}` : String(error) };
  }
}

/** Devolve HTML sanitizado, ou `null` se o DOMPurify não puder sanitizar (falha fechada). */
export function sanitizeHtml(html: string): string | null {
  const result = sanitizeHtmlChecked(html);
  return result.ok ? result.html : null;
}

/**
 * Converte HTML já sanitizado em fragmento de DOM. Os atributos `style` (KaTeX e alinhamento de
 * tabela) são aplicados pelo CSSOM e removidos do atributo, para que a CSP `style-src 'self'` não
 * precise de `'unsafe-inline'` (tarefa 2.4.3; confirmação no webview real em 7.1.5).
 */
export function toFragment(cleanHtml: string): DocumentFragment {
  const template = document.createElement("template");
  template.innerHTML = cleanHtml;
  const fragment = template.content;
  for (const element of Array.from(fragment.querySelectorAll<HTMLElement | SVGElement>("[style]"))) {
    const declarations = (element.getAttribute("style") ?? "").split(";");
    element.removeAttribute("style");
    for (const declaration of declarations) {
      const separator = declaration.indexOf(":");
      if (separator <= 0) continue;
      const property = declaration.slice(0, separator).trim();
      const value = declaration.slice(separator + 1).trim();
      if (property && value) element.style.setProperty(property, value);
    }
  }
  return fragment;
}
