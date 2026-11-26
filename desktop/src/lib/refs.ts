// Classificação das referências (`src` de imagem e `href` de link) do Markdown. Sem dependências:
// pode ser importada no thread principal sem arrastar o markdown-it nem o KaTeX.

export type ImageKind = "local" | "remoteBlocked" | "outsideFolder";

/** Decodifica a codificação percentual que o markdown-it aplica a `src`/`href` (tarefa 2.4.5). */
export function decodeReference(ref: string): string {
  try {
    return decodeURIComponent(ref);
  } catch {
    return ref;
  }
}

const SCHEME = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;

/** `src` com esquema (`http:`, `data:`, `file:`...) ou `//` é remoto; caminho absoluto sai da pasta. */
export function classifyImageSrc(rawSrc: string): ImageKind {
  const src = decodeReference(rawSrc).trim();
  if (SCHEME.test(src) || src.startsWith("//") || src.startsWith("\\\\")) {
    return "remoteBlocked";
  }
  if (src.startsWith("/") || src.startsWith("\\")) {
    return "outsideFolder";
  }
  return "local";
}

export type LinkKind = "markdown" | "inactive";

/** Link relativo para `.md` (sem esquema, sem `//`, sem barra inicial) abre no visualizador. */
export function classifyLink(rawHref: string): LinkKind {
  const href = decodeReference(rawHref).trim();
  if (href === "" || href.startsWith("#") || SCHEME.test(href)) return "inactive";
  if (href.startsWith("/") || href.startsWith("\\")) return "inactive";
  const path = href.split(/[?#]/, 1)[0] ?? "";
  return path.toLowerCase().endsWith(".md") ? "markdown" : "inactive";
}
