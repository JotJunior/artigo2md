import { describe, expect, it } from "vitest";
import {
  classifyImageSrc,
  classifyLink,
  createMarkdown,
  decodeReference,
  KATEX_OPTIONS,
  renderMarkdown,
} from "../src/lib/markdown";

function dom(html: string): HTMLElement {
  const root = document.createElement("div");
  root.innerHTML = html;
  return root;
}

describe("regras do renderer do markdown-it (tarefa 2.4.5)", () => {
  it("as regras image, link_open e link_close existem e foram substituídas", () => {
    const md = createMarkdown();
    const rules = md.renderer.rules as Record<string, unknown>;
    expect(typeof rules.image).toBe("function");
    expect(typeof rules.link_open).toBe("function");
    expect(typeof rules.link_close).toBe("function");
  });

  it("html, linkify e tipografia estão desligados", () => {
    const md = createMarkdown();
    expect(md.options.html).toBe(false);
    expect(md.options.linkify).toBe(false);
    expect(md.options.typographer).toBe(false);
  });

  it("src e href vêm com codificação percentual e decodeReference os devolve", () => {
    const html = renderMarkdown("![fig](imagens/figura%20é.png)\n\n[l](Pasta/á%20b.md)\n\n![a](<imagens/fígura 1.png>)");
    const root = dom(html);
    const images = root.querySelectorAll<HTMLElement>("[data-md-image]");
    expect(images[1]!.dataset.src).toBe("imagens/f%C3%ADgura%201.png");
    expect(decodeReference(images[1]!.dataset.src!)).toBe("imagens/fígura 1.png");
    const link = root.querySelector<HTMLElement>("[data-md-link]")!;
    expect(decodeReference(link.dataset.mdLink!)).toBe("Pasta/á b.md");
  });

  it("decodeReference não falha com codificação inválida", () => {
    expect(decodeReference("a%zz.md")).toBe("a%zz.md");
  });
});

describe("renderização (FR-010 a FR-012, FR-033)", () => {
  it("renderiza elementos básicos", () => {
    const root = dom(
      renderMarkdown("# Título\n\ntexto *ênfase* **forte** `código`\n\n- a\n- b\n\n1. x\n\n> citação\n\n---\n\n```\nbloco\n```"),
    );
    expect(root.querySelector("h1")?.textContent).toBe("Título");
    expect(root.querySelector("em")?.textContent).toBe("ênfase");
    expect(root.querySelector("strong")?.textContent).toBe("forte");
    expect(root.querySelectorAll("ul li")).toHaveLength(2);
    expect(root.querySelectorAll("ol li")).toHaveLength(1);
    expect(root.querySelector("blockquote")).not.toBeNull();
    expect(root.querySelector("hr")).not.toBeNull();
    expect(root.querySelector("pre code")?.textContent).toContain("bloco");
  });

  it("tabela GFM por padrão, com cabeçalho e alinhamento de coluna", () => {
    const root = dom(renderMarkdown("| a | b | c |\n|:--|:-:|--:|\n| 1 | 2 | 3 |\n"));
    expect(root.querySelector("table thead th")).not.toBeNull();
    const cells = root.querySelectorAll("tbody td");
    expect(cells).toHaveLength(3);
    expect(cells[0]!.getAttribute("style")).toContain("text-align:left");
    expect(cells[1]!.getAttribute("style")).toContain("text-align:center");
    expect(cells[2]!.getAttribute("style")).toContain("text-align:right");
  });

  it("fórmula em linha e em bloco", () => {
    const root = dom(renderMarkdown("Inline $a^2$ aqui.\n\n$$\\int_0^1 x\\,dx$$\n"));
    expect(root.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(2);
    expect(root.querySelector(".katex-display")).not.toBeNull();
    expect(root.querySelector(".katex-error")).toBeNull();
  });

  it("fórmula inválida fica isolada, com o texto original, e o resto é renderizado", () => {
    const root = dom(renderMarkdown("antes\n\n$$\\frac{1}{$$\n\ndepois $x$ ok\n"));
    const errors = root.querySelectorAll(".katex-error");
    expect(errors.length).toBeGreaterThanOrEqual(1);
    expect(root.textContent).toContain("\\frac{1}{");
    expect(root.textContent).toContain("antes");
    expect(root.textContent).toContain("depois");
    expect(root.querySelectorAll(".katex:not(.katex-error)").length).toBeGreaterThanOrEqual(1);
  });

  it("HTML bruto é exibido como texto (FR-017)", () => {
    const html = renderMarkdown('<script>alert(1)</script>\n\n<img src="https://x.test/a.png">\n\n<b>negrito</b>');
    const root = dom(html);
    expect(root.querySelector("script")).toBeNull();
    expect(root.querySelector("img")).toBeNull();
    expect(root.querySelector("b")).toBeNull();
    expect(root.textContent).toContain("<script>alert(1)</script>");
    expect(root.textContent).toContain("<b>negrito</b>");
  });

  it("linkify desligado: endereço solto não vira link", () => {
    const root = dom(renderMarkdown("veja https://example.org agora"));
    expect(root.querySelector("a")).toBeNull();
    expect(root.querySelector("[data-md-link]")).toBeNull();
  });

  it("KaTeX com opções fixadas (trust desligado, expansão limitada)", () => {
    expect(KATEX_OPTIONS.throwOnError).toBe(false);
    expect(KATEX_OPTIONS.trust).toBe(false);
    expect(KATEX_OPTIONS.maxExpand).toBe(1000);
    expect(KATEX_OPTIONS.maxSize).toBe(100);
  });
});

describe("links (FR-040)", () => {
  it("link externo sem href e inativo", () => {
    const root = dom(renderMarkdown("[site](https://example.org/a)"));
    const el = root.querySelector<HTMLElement>(".link-inactive")!;
    expect(el).not.toBeNull();
    expect(el.textContent).toBe("site");
    expect(root.querySelector("[href]")).toBeNull();
    expect(root.querySelector("a")).toBeNull();
    expect(el.getAttribute("title")).toBe("Links externos estão desativados");
  });

  it("link relativo para .md vira elemento com data-md-link", () => {
    const root = dom(renderMarkdown("[outro](artigo.md) e [fora](../../x.md#sec) e [txt](notas.txt) e [âncora](#a)"));
    const md = root.querySelectorAll<HTMLElement>("[data-md-link]");
    expect(md).toHaveLength(2);
    expect(md[0]!.dataset.mdLink).toBe("artigo.md");
    expect(md[1]!.dataset.mdLink).toBe("../../x.md#sec");
    expect(root.querySelectorAll(".link-inactive")).toHaveLength(2);
    expect(root.querySelector("[href]")).toBeNull();
  });

  it("classifyLink", () => {
    expect(classifyLink("a/b.MD")).toBe("markdown");
    expect(classifyLink("b.md?x=1#y")).toBe("markdown");
    expect(classifyLink("https://x/y.md")).toBe("inactive");
    expect(classifyLink("//x/y.md")).toBe("inactive");
    expect(classifyLink("/abs/y.md")).toBe("inactive");
    expect(classifyLink("mailto:a@b.c")).toBe("inactive");
    expect(classifyLink("#topo")).toBe("inactive");
    expect(classifyLink("")).toBe("inactive");
    expect(classifyLink("imagem.png")).toBe("inactive");
  });
});

describe("imagens (FR-013, FR-016, FR-032)", () => {
  it("a regra image gera marcador data-*, nunca <img src>", () => {
    const html = renderMarkdown("![texto alt](imagens/figura1.png)");
    const root = dom(html);
    expect(root.querySelector("img")).toBeNull();
    expect(html).not.toMatch(/\ssrc=/);
    const marker = root.querySelector<HTMLElement>("[data-md-image]")!;
    expect(marker.dataset.src).toBe("imagens/figura1.png");
    expect(marker.dataset.kind).toBe("local");
    expect(marker.dataset.alt).toBe("texto alt");
  });

  it("src remoto ou absoluto não vai ao marcador, só a classificação", () => {
    const root = dom(renderMarkdown("![r](https://example.org/a.png) ![d](data:image/png;base64,AAAA) ![a](/etc/passwd)"));
    const markers = root.querySelectorAll<HTMLElement>("[data-md-image]");
    expect(Array.from(markers, (m) => m.dataset.kind)).toEqual(["remoteBlocked", "remoteBlocked", "outsideFolder"]);
    for (const m of markers) expect(m.hasAttribute("data-src")).toBe(false);
  });

  it("escapa atributos vindos do Markdown", () => {
    const root = dom(renderMarkdown('![a"onerror="x](im".png)'));
    expect(root.querySelector("[onerror]")).toBeNull();
  });

  it("classifyImageSrc marca remoto, absoluto e local", () => {
    expect(classifyImageSrc("https://example.org/a.png")).toBe("remoteBlocked");
    expect(classifyImageSrc("http://example.org/a.png")).toBe("remoteBlocked");
    expect(classifyImageSrc("data:image/png;base64,AAAA")).toBe("remoteBlocked");
    expect(classifyImageSrc("file:///etc/passwd")).toBe("remoteBlocked");
    expect(classifyImageSrc("//example.org/a.png")).toBe("remoteBlocked");
    expect(classifyImageSrc("/etc/passwd")).toBe("outsideFolder");
    expect(classifyImageSrc("imagens/figura1.png")).toBe("local");
    expect(classifyImageSrc("../../segredo.png")).toBe("local"); // o confinamento é do Rust (FR-032)
    expect(classifyImageSrc("imagens/f%C3%ADgura.png")).toBe("local");
  });
});
