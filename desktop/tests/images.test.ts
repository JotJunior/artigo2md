import { describe, expect, it, vi } from "vitest";
import { ImageLoader, statusFromError } from "../src/lib/images";
import { renderMarkdown } from "../src/lib/markdown";
import { sanitizeHtml, toFragment } from "../src/lib/sanitize";
import type { AppError, ErrorCode } from "../src/lib/types";

function host(markdown: string): HTMLElement {
  const root = document.createElement("div");
  root.append(toFragment(sanitizeHtml(renderMarkdown(markdown))!));
  return root;
}

const appError = (code: ErrorCode): AppError => ({ code, detail: null });
const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47]).buffer;
const tick = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

describe("ImageLoader (FR-013, FR-014, FR-016, FR-032, FR-041)", () => {
  it("remoto e absoluto são bloqueados sem chamar o IPC", () => {
    const readImage = vi.fn();
    const root = host("![r](https://example.org/a.png)\n\n![d](data:image/png;base64,AAAA)\n\n![a](/etc/passwd)");
    new ImageLoader({ documentPath: "a.md", readImage }).attach(root);
    expect(readImage).not.toHaveBeenCalled();
    const notices = Array.from(root.querySelectorAll(".image-notice")).map((n) => n.textContent);
    expect(notices[0]).toBe("r: Imagem remota bloqueada");
    expect(notices[1]).toBe("d: Imagem remota bloqueada");
    expect(notices[2]).toBe("a: Imagem fora da pasta aberta");
    expect(root.querySelector("img")).toBeNull();
  });

  it("imagem local vira <img> com blob e texto alternativo", async () => {
    const readImage = vi.fn().mockResolvedValue(png);
    const createObjectUrl = vi.fn().mockReturnValue("blob:fake/1");
    const root = host("![minha figura](imagens/f%C3%ADgura%201.png)");
    new ImageLoader({ documentPath: "Artigos/a.md", readImage, createObjectUrl }).attach(root);
    expect(root.querySelector(".image-notice")?.textContent).toBe("minha figura: Carregando imagem…");
    await tick();
    expect(readImage).toHaveBeenCalledWith("Artigos/a.md", "imagens/fígura 1.png");
    const img = root.querySelector("img")!;
    expect(img.getAttribute("src")).toBe("blob:fake/1");
    expect(img.alt).toBe("minha figura");
    expect(createObjectUrl.mock.calls[0]![0].type).toBe("image/png");
  });

  it("cada erro do Rust vira o aviso do seu status", async () => {
    const cases: Array<[ErrorCode, string]> = [
      ["notFound", "Imagem não encontrada"],
      ["outsideFolder", "Imagem fora da pasta aberta"],
      ["unsupportedFormat", "Formato de imagem não suportado"],
      ["tooLarge", "Imagem grande demais para ser exibida (limite de 20 MB)"],
      ["permissionDenied", "Não foi possível ler a imagem"],
      ["io", "Não foi possível ler a imagem"],
    ];
    for (const [code, text] of cases) {
      const root = host("![x](a.png)");
      new ImageLoader({ documentPath: "a.md", readImage: () => Promise.reject(appError(code)) }).attach(root);
      await tick();
      expect(root.querySelector(".image-notice")?.textContent, code).toBe(`x: ${text}`);
    }
  });

  it("a fila respeita o limite de concorrência", async () => {
    let active = 0;
    let peak = 0;
    const pending: Array<() => void> = [];
    const readImage = (): Promise<ArrayBuffer> =>
      new Promise((resolve) => {
        active += 1;
        peak = Math.max(peak, active);
        pending.push(() => {
          active -= 1;
          resolve(png);
        });
      });
    const root = host(Array.from({ length: 6 }, (_, i) => `![i${i}](a${i}.png)`).join("\n\n"));
    new ImageLoader({ documentPath: "a.md", readImage, concurrency: 2, createObjectUrl: () => "blob:x" }).attach(root);
    await tick();
    expect(pending).toHaveLength(2);
    while (pending.length > 0) {
      pending.shift()!();
      await tick();
    }
    expect(peak).toBe(2);
    expect(root.querySelectorAll("img")).toHaveLength(6);
  });

  it("dispose revoga todos os blob: criados", async () => {
    const revokeObjectUrl = vi.fn();
    let n = 0;
    const root = host("![a](a.png)\n\n![b](b.png)");
    const loader = new ImageLoader({
      documentPath: "a.md",
      readImage: () => Promise.resolve(png),
      createObjectUrl: () => `blob:x/${(n += 1)}`,
      revokeObjectUrl,
    });
    loader.attach(root);
    await tick();
    loader.dispose();
    expect(revokeObjectUrl.mock.calls.map((c) => c[0]).sort()).toEqual(["blob:x/1", "blob:x/2"]);
  });

  it("dispose antes da resposta cancela a fila e não cria blob:", async () => {
    const createObjectUrl = vi.fn().mockReturnValue("blob:x/1");
    const readImage = vi.fn(() => Promise.resolve(png));
    const root = host("![a](a.png)\n\n![b](b.png)\n\n![c](c.png)");
    const loader = new ImageLoader({ documentPath: "a.md", readImage, concurrency: 1, createObjectUrl });
    loader.attach(root);
    loader.dispose();
    await tick();
    expect(readImage).toHaveBeenCalledTimes(1); // só a primeira já tinha saído da fila
    expect(createObjectUrl).not.toHaveBeenCalled();
    expect(root.querySelector("img")).toBeNull();
  });

  it("marcador sem classificação reconhecida falha fechado (remoto bloqueado)", () => {
    const readImage = vi.fn();
    const root = document.createElement("div");
    root.innerHTML = '<span data-md-image="" data-alt="x" data-src="a.png"></span>';
    new ImageLoader({ documentPath: "a.md", readImage }).attach(root);
    expect(readImage).not.toHaveBeenCalled();
    expect(root.textContent).toBe("x: Imagem remota bloqueada");
  });

  it("src local vazio vira \"Imagem não encontrada\"", () => {
    const readImage = vi.fn();
    const root = document.createElement("div");
    root.innerHTML = '<span data-md-image="" data-kind="local" data-alt="x"></span>';
    new ImageLoader({ documentPath: "a.md", readImage }).attach(root);
    expect(readImage).not.toHaveBeenCalled();
    expect(root.textContent).toBe("x: Imagem não encontrada");
  });

  it("statusFromError cai em readError para erro desconhecido", () => {
    expect(statusFromError(new Error("x"))).toBe("readError");
    expect(statusFromError(null)).toBe("readError");
  });
});
