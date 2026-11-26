import DOMPurify from "dompurify";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderMarkdown } from "../src/lib/markdown";
import { isSafeStyleValue, sanitizeHtml, sanitizeHtmlChecked, toFragment } from "../src/lib/sanitize";

function sanitizedDom(html: string): HTMLElement {
  const clean = sanitizeHtml(html);
  expect(clean).not.toBeNull();
  const root = document.createElement("div");
  root.innerHTML = clean!;
  return root;
}

const FETCHING_ATTRIBUTES = ["src", "href", "srcset", "xlink:href", "action", "formaction", "data", "poster", "background", "ping"];

/** Elementos com atributo capaz de disparar uma requisição, ou `style` com `url(`. */
function remoteReferences(root: Element): Element[] {
  return Array.from(root.querySelectorAll("*")).filter(
    (el) =>
      FETCHING_ATTRIBUTES.some((name) => el.hasAttribute(name)) || /url\s*\(/i.test(el.getAttribute("style") ?? ""),
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("sanitização (plan.md, Endurecimento de segurança)", () => {
  it("remove as tags proibidas e atributos de rede", () => {
    const root = sanitizedDom(
      [
        '<form action="https://x.test"><input name="a"></form>',
        '<iframe src="https://x.test"></iframe>',
        '<object data="https://x.test"></object>',
        '<embed src="https://x.test">',
        '<svg><use href="https://x.test/s.svg#a"></use></svg>',
        '<script>alert(1)</script>',
        '<img src="https://x.test/a.png" onerror="alert(1)">',
        '<a href="https://x.test" onclick="alert(1)">link</a>',
        '<p onclick="alert(1)" style="background:url(https://x.test/a.png)">texto</p>',
      ].join(""),
    );
    for (const selector of ["form", "iframe", "object", "embed", "use", "script", "img", "a", "input"]) {
      expect(root.querySelector(selector), selector).toBeNull();
    }
    expect(remoteReferences(root)).toEqual([]);
    expect(root.querySelector("[onclick]")).toBeNull();
    expect(root.querySelector("[style]")).toBeNull();
    expect(root.textContent).toContain("texto");
  });

  it("preserva a saída do markdown (tabela, marcador de imagem, link .md)", () => {
    const root = sanitizedDom(
      renderMarkdown("| a | b |\n|:--|--:|\n| 1 | 2 |\n\n![alt](img/a.png)\n\n[l](b.md) [x](https://x.test)"),
    );
    expect(root.querySelector("table")).not.toBeNull();
    expect(root.querySelector<HTMLElement>("[data-md-image]")?.dataset.src).toBe("img/a.png");
    expect(root.querySelector<HTMLElement>("[data-md-image]")?.dataset.kind).toBe("local");
    expect(root.querySelector<HTMLElement>("[data-md-link]")?.dataset.mdLink).toBe("b.md");
    expect(root.querySelector(".link-inactive")).not.toBeNull();
    expect(root.querySelector("td")?.getAttribute("style")).toContain("text-align");
  });

  it("preserva a saída do KaTeX (HTML, MathML e SVG)", () => {
    const root = sanitizedDom(renderMarkdown("$$\\sqrt{x^2+1} + \\frac{a}{b}$$\n\n$\\widehat{abc}$"));
    expect(root.querySelector(".katex")).not.toBeNull();
    expect(root.querySelector(".katex-html")).not.toBeNull();
    expect(root.querySelector("math")).not.toBeNull();
    expect(root.querySelector("annotation")).not.toBeNull();
    expect(root.querySelector("svg")).not.toBeNull();
    expect(remoteReferences(root)).toEqual([]);
  });

  it("\\href, \\url e \\includegraphics ficam inertes (trust desligado)", () => {
    const source = [
      "$\\href{https://x.test/a}{clique}$",
      "$\\url{https://x.test/b}$",
      "$\\includegraphics{https://x.test/c.png}$",
      "$$\\htmlClass{evil}{x}$$",
    ].join("\n\n");
    const root = sanitizedDom(renderMarkdown(source));
    expect(root.querySelector("a")).toBeNull();
    expect(root.querySelector("img")).toBeNull();
    expect(remoteReferences(root)).toEqual([]);
    expect(root.querySelector(".evil")).toBeNull();
  });

  it("HTML final de um documento com recursos remotos não tem src nem href remoto (SC-004)", () => {
    const source = [
      "![r](https://example.org/a.png)",
      "[site](https://example.org)",
      "<img src='https://example.org/b.png'>",
      '<a href="https://example.org">x</a>',
      "![d](data:image/png;base64,AAAA)",
    ].join("\n\n");
    const root = sanitizedDom(renderMarkdown(source));
    expect(remoteReferences(root)).toEqual([]);
    // Nenhum atributo de nenhum elemento carrega o endereço remoto (só o texto exibido o menciona).
    for (const el of Array.from(root.querySelectorAll("*"))) {
      for (const attr of Array.from(el.attributes)) {
        expect(attr.value, `${el.tagName}[${attr.name}]`).not.toContain("example.org");
      }
    }
  });

  it("style: só declarações simples de layout", () => {
    expect(isSafeStyleValue("height:0.9em;vertical-align:-0.2em")).toBe(true);
    expect(isSafeStyleValue("text-align:right")).toBe(true);
    expect(isSafeStyleValue("background:url(https://x.test/a.png)")).toBe(false);
    expect(isSafeStyleValue("width:expression(alert(1))")).toBe(false);
    expect(isSafeStyleValue("background:\\75rl(x)")).toBe(false);
    expect(isSafeStyleValue("a:b /* c */")).toBe(false);
  });

  it("falha fechada: sem suporte do DOMPurify, devolve null (o chamador mostra texto puro)", () => {
    vi.spyOn(DOMPurify, "isSupported", "get").mockReturnValue(false);
    expect(sanitizeHtml("<p>x</p>")).toBeNull();
    expect(sanitizeHtmlChecked("<p>x</p>")).toEqual({ ok: false, detail: "DOMPurify.isSupported é false" });
  });

  it("falha fechada: erro na sanitização devolve null", () => {
    vi.spyOn(DOMPurify, "sanitize").mockImplementation(() => {
      throw new Error("boom");
    });
    expect(sanitizeHtml("<p>x</p>")).toBeNull();
    expect(sanitizeHtmlChecked("<p>x</p>")).toEqual({ ok: false, detail: "Error: boom" });
  });
});

describe("toFragment (CSSOM no lugar de style inline)", () => {
  it("aplica o style pelo CSSOM (o atributo é só reflexo da CSSOM)", () => {
    const clean = sanitizeHtml('<table><tbody><tr><td style="text-align:right">1</td></tr></tbody></table>')!;
    const fragment = toFragment(clean);
    const td = fragment.querySelector("td")!;
    expect(td.style.getPropertyValue("text-align")).toBe("right");
  });

  it("KaTeX continua com as dimensões depois da conversão", () => {
    const fragment = toFragment(sanitizeHtml(renderMarkdown("$x^2$"))!);
    const strut = fragment.querySelector<HTMLElement>(".katex-strut");
    expect(strut?.style.getPropertyValue("height")).not.toBe("");
  });
});
