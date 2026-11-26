// Estados de componente que não dependem do webview real (tarefa 4.3.4): monta os componentes
// Svelte em jsdom, com o módulo `api` simulado. Nenhum IPC real, nenhum Worker real.

import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getStartupState: vi.fn(),
  openFolder: vi.fn(),
  listDir: vi.fn(),
  readMarkdown: vi.fn(),
  readImage: vi.fn(),
  resolveLink: vi.fn(),
  saveSettings: vi.fn(),
  resetSettings: vi.fn(),
}));
vi.mock("../src/lib/api", () => api);

import App from "../src/App.svelte";
import FileTree from "../src/components/FileTree.svelte";
import FilesView from "../src/components/FilesView.svelte";
import SettingsView from "../src/components/SettingsView.svelte";
import Sidebar from "../src/components/Sidebar.svelte";
import Viewer from "../src/components/Viewer.svelte";
import { renderMarkdown } from "../src/lib/markdown";
import { DEFAULT_SETTINGS, SettingsStore, settings } from "../src/lib/settings.svelte";
import { TreeModel } from "../src/lib/tree.svelte";
import type { MarkdownDocument, TreeEntry, UserSettings } from "../src/lib/types";

const tick = async (n = 6): Promise<void> => {
  for (let i = 0; i < n; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
    flushSync();
  }
};

/** Worker simulado: renderiza no mesmo thread e responde de forma assíncrona. */
class FakeWorker {
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  postMessage(message: { source: string }): void {
    queueMicrotask(() => this.onmessage?.({ data: { ok: true, html: renderMarkdown(message.source) } }));
  }
  terminate(): void {
    this.onmessage = null;
  }
}

const entry = (path: string, over: Partial<TreeEntry> = {}): TreeEntry => ({
  name: path.split("/").pop()!,
  relativePath: path,
  kind: "file",
  access: "ok",
  isMarkdown: path.toLowerCase().endsWith(".md"),
  symlinkTarget: null,
  ...over,
});
const folder = (path: string, over: Partial<TreeEntry> = {}): TreeEntry => entry(path, { kind: "directory", ...over });

const doc = (content: string, over: Partial<MarkdownDocument> = {}): MarkdownDocument => ({
  relativePath: "a.md",
  content,
  sizeBytes: content.length,
  isLarge: false,
  ...over,
});

let target: HTMLElement;
let mounted: Array<Record<string, unknown>> = [];

function render<P extends Record<string, unknown>>(component: any, props: P): HTMLElement {
  const instance = mount(component, { target, props });
  mounted.push(instance as Record<string, unknown>);
  flushSync();
  return target;
}

beforeEach(() => {
  target = document.createElement("div");
  document.body.append(target);
  vi.stubGlobal("Worker", FakeWorker);
  for (const fn of Object.values(api)) fn.mockReset();
});

afterEach(() => {
  for (const instance of mounted) void unmount(instance);
  mounted = [];
  target.remove();
  vi.unstubAllGlobals();
});

describe("Sidebar (FR-002, FR-039)", () => {
  const props = (over: Record<string, unknown> = {}) => ({
    view: "files",
    collapsed: false,
    width: 260,
    onSelect: vi.fn(),
    onToggle: vi.fn(),
    onResize: vi.fn(),
    ...over,
  });

  it("mostra Arquivos e Configurações e destaca a entrada atual", () => {
    render(Sidebar, props({ view: "settings" }));
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>(".nav-item"));
    expect(buttons.map((b) => b.textContent!.trim())).toEqual(["Arquivos", "Configurações"]);
    expect(buttons[1]!.getAttribute("aria-current")).toBe("page");
    expect(buttons[0]!.hasAttribute("aria-current")).toBe(false);
  });

  it("clicar numa entrada pede a troca de tela", () => {
    const p = props();
    render(Sidebar, p);
    target.querySelectorAll<HTMLButtonElement>(".nav-item")[1]!.click();
    expect(p.onSelect).toHaveBeenCalledWith("settings");
  });

  it("recolhida, esconde as entradas e oferece Expandir barra lateral", () => {
    const p = props({ collapsed: true });
    render(Sidebar, p);
    expect(target.querySelector(".nav-item")).toBeNull();
    const toggle = target.querySelector<HTMLButtonElement>(".sidebar-toggle")!;
    expect(toggle.getAttribute("aria-label")).toBe("Expandir barra lateral");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    toggle.click();
    expect(p.onToggle).toHaveBeenCalled();
  });

  it("aberta, o botão se chama Recolher barra lateral", () => {
    render(Sidebar, props());
    expect(target.querySelector(".sidebar-toggle")!.getAttribute("aria-label")).toBe("Recolher barra lateral");
  });

  it("separador operável por teclado, com valores aria (4.3.5)", () => {
    const p = props();
    render(Sidebar, p);
    const separator = target.querySelector<HTMLElement>('[role="separator"]')!;
    expect(separator.getAttribute("aria-label")).toBe("Ajustar largura da barra lateral");
    expect(separator.getAttribute("aria-valuenow")).toBe("260");
    expect(separator.getAttribute("aria-valuemin")).toBe("200");
    expect(separator.getAttribute("aria-valuemax")).toBe("480");
    expect(separator.tabIndex).toBe(0);
    separator.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    expect(p.onResize).toHaveBeenCalledWith(276);
    separator.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true }));
    expect(p.onResize).toHaveBeenLastCalledWith(200);
  });
});

describe("FileTree (FR-005, FR-030, 4.3.5)", () => {
  async function tree(entries: TreeEntry[], children: Record<string, TreeEntry[]> = {}) {
    const model = new TreeModel(async (path) => (path === "" ? entries : (children[path] ?? [])));
    await model.loadRoot();
    const onSelect = vi.fn();
    render(FileTree, { model, selectedPath: null, onSelect });
    return { model, onSelect, el: () => target.querySelector<HTMLElement>('[role="tree"]')! };
  }
  const key = (el: HTMLElement, k: string): void => {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
    flushSync();
  };

  it("mostra o estado de carregamento da raiz", () => {
    const model = new TreeModel(() => new Promise(() => {}));
    void model.loadRoot();
    render(FileTree, { model, selectedPath: null, onSelect: vi.fn() });
    expect(target.textContent).toContain("Carregando…");
  });

  it("pasta aberta sem itens visíveis mostra o estado vazio", async () => {
    await tree([]);
    expect(target.textContent).toContain("Esta pasta não tem arquivos visíveis");
  });

  it("identifica atalhos e pastas sem permissão", async () => {
    await tree([
      folder("trancada", { access: "denied" }),
      entry("atalho-fora", { kind: "symlink", symlinkTarget: "outside" }),
      entry("ok.md", { kind: "symlink", symlinkTarget: "insideFile" }),
    ]);
    expect(target.textContent).toContain("Sem permissão para ler esta pasta");
    expect(target.textContent).toContain("Atalho não expandido");
    const items = target.querySelectorAll('[role="treeitem"]');
    expect(items[0]!.getAttribute("aria-disabled")).toBe("true");
    expect(items[0]!.hasAttribute("aria-expanded")).toBe(false);
  });

  it("roles e rótulos aria da árvore", async () => {
    const { el } = await tree([folder("A"), entry("b.md")]);
    expect(el().getAttribute("aria-label")).toBe("Árvore de arquivos");
    expect(el().tabIndex).toBe(0);
    const [a, b] = Array.from(target.querySelectorAll('[role="treeitem"]'));
    expect(a!.getAttribute("aria-expanded")).toBe("false");
    expect(a!.getAttribute("aria-level")).toBe("1");
    expect(b!.hasAttribute("aria-expanded")).toBe(false);
  });

  it("setas, Home, End e Enter operam a árvore só com o teclado", async () => {
    const { el, onSelect, model } = await tree([folder("A"), entry("b.md"), entry("c.txt")], {
      A: [entry("A/a1.md")],
    });
    el().focus(); // ao receber o foco, o primeiro item fica ativo
    flushSync();
    expect(el().getAttribute("aria-activedescendant")).toBe("tree-A");
    key(el(), "ArrowRight"); // expande A
    await tick();
    expect(model.isExpanded("A")).toBe(true);
    expect(target.querySelectorAll('[role="treeitem"]')).toHaveLength(4);
    key(el(), "ArrowRight"); // entra na pasta
    expect(el().getAttribute("aria-activedescendant")).toBe("tree-A_a1_md");
    key(el(), "Enter"); // abre o arquivo
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0]![0].relativePath).toBe("A/a1.md");
    key(el(), "ArrowLeft"); // sobe ao pai
    expect(el().getAttribute("aria-activedescendant")).toBe("tree-A");
    key(el(), "ArrowLeft"); // recolhe
    expect(model.isExpanded("A")).toBe(false);
    key(el(), "End");
    expect(el().getAttribute("aria-activedescendant")).toBe("tree-c_txt");
    key(el(), "Home");
    expect(el().getAttribute("aria-activedescendant")).toBe("tree-A");
    key(el(), " "); // Espaço alterna a pasta
    await tick();
    expect(model.isExpanded("A")).toBe(true);
  });

  it("clicar numa pasta expande; clicar num arquivo seleciona; atalho para fora não abre", async () => {
    const { onSelect, model } = await tree([
      folder("A"),
      entry("b.md"),
      entry("fora", { kind: "symlink", symlinkTarget: "outside" }),
    ]);
    const rows = target.querySelectorAll<HTMLElement>(".tree-row");
    rows[0]!.click();
    await tick();
    expect(model.isExpanded("A")).toBe(true);
    rows[1]!.click();
    expect(onSelect).toHaveBeenCalledTimes(1);
    rows[2]!.click();
    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});

describe("Viewer (FR-009, FR-014, FR-018, FR-031, FR-034, FR-035, FR-040)", () => {
  const open = async (content: MarkdownDocument | Error, over: Record<string, unknown> = {}) => {
    if (content instanceof Error) api.readMarkdown.mockRejectedValue(content);
    else api.readMarkdown.mockResolvedValue(content);
    const onNavigate = vi.fn();
    render(Viewer, { path: "a.md", onNavigate, ...over });
    await tick();
    return onNavigate;
  };
  const fail = (code: string) => Object.assign(new Error(code), { code, detail: null });

  it("sem arquivo selecionado, mostra o convite", () => {
    render(Viewer, { path: null, onNavigate: vi.fn() });
    expect(target.textContent).toContain("Selecione um arquivo Markdown na árvore para visualizá-lo");
    expect(api.readMarkdown).not.toHaveBeenCalled();
  });

  it("renderiza o documento (tabela, fórmula) e mostra o botão Recarregar", async () => {
    await open(doc("# Título\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n$x^2$\n"));
    expect(target.querySelector("h1")?.textContent).toBe("Título");
    expect(target.querySelector("table")).not.toBeNull();
    expect(target.querySelector(".katex")).not.toBeNull();
    expect(target.textContent).toContain("Recarregar");
    expect(target.textContent).not.toContain("Carregando documento…");
  });

  it.each([
    ["notMarkdown", "Este tipo de arquivo não pode ser visualizado"],
    ["notText", "Não foi possível ler este arquivo como texto"],
    ["notFound", "Arquivo não encontrado"],
    ["tooLarge", "Arquivo grande demais para ser aberto (limite de 50 MB)"],
    ["permissionDenied", "Sem permissão para ler esta pasta"],
  ])("erro %s mostra o texto em português", async (code, text) => {
    await open(fail(code));
    expect(target.querySelector('[role="alert"]')?.textContent).toBe(text);
    expect(target.querySelector("h1")).toBeNull();
  });

  it("arquivo grande avisa antes do resultado da renderização", async () => {
    await open(doc("# x", { isLarge: true }));
    expect(target.textContent).toContain("Arquivo grande: a renderização pode demorar");
    expect(target.querySelector("h1")).not.toBeNull();
  });

  it("Recarregar lê o disco de novo (conteúdo atual ou Arquivo não encontrado)", async () => {
    await open(doc("# Antes"));
    expect(target.querySelector("h1")?.textContent).toBe("Antes");
    api.readMarkdown.mockResolvedValue(doc("# Depois"));
    target.querySelector<HTMLButtonElement>(".viewer-toolbar button")!.click();
    await tick();
    expect(api.readMarkdown).toHaveBeenCalledTimes(2);
    expect(target.querySelector("h1")?.textContent).toBe("Depois");
    api.readMarkdown.mockRejectedValue(fail("notFound"));
    target.querySelector<HTMLButtonElement>(".viewer-toolbar button")!.click();
    await tick();
    expect(target.querySelector('[role="alert"]')?.textContent).toBe("Arquivo não encontrado");
  });

  it("imagens: remota bloqueada, fora da pasta, não encontrada e carregada", async () => {
    api.readImage.mockImplementation(async (_doc: string, src: string) => {
      if (src === "ok.png") return new Uint8Array([1, 2, 3]).buffer;
      throw fail(src === "../../segredo.png" ? "outsideFolder" : "notFound");
    });
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:fake/1", revokeObjectURL: () => {} }));
    await open(doc("![r](https://example.org/a.png)\n\n![f](../../segredo.png)\n\n![n](nao.png)\n\n![ok](ok.png)"));
    const notices = Array.from(target.querySelectorAll(".image-notice")).map((n) => n.textContent);
    expect(notices).toEqual([
      "r: Imagem remota bloqueada",
      "f: Imagem fora da pasta aberta",
      "n: Imagem não encontrada",
    ]);
    expect(target.querySelector("img")?.getAttribute("src")).toBe("blob:fake/1");
    expect(api.readImage).not.toHaveBeenCalledWith("a.md", "https://example.org/a.png");
  });

  it("link .md abre o arquivo apontado; link externo não faz nada", async () => {
    api.resolveLink.mockResolvedValue("Artigos/outro.md");
    const onNavigate = await open(doc("[outro](outro.md) e [site](https://example.org)"));
    target.querySelector<HTMLElement>("[data-md-link]")!.click();
    await tick();
    expect(api.resolveLink).toHaveBeenCalledWith("a.md", "outro.md");
    expect(onNavigate).toHaveBeenCalledWith("Artigos/outro.md");
    target.querySelector<HTMLElement>(".link-inactive")!.click();
    await tick();
    expect(api.resolveLink).toHaveBeenCalledTimes(1);
    expect(onNavigate).toHaveBeenCalledTimes(1);
  });

  it("link .md fora da pasta mostra o aviso e não abre nada", async () => {
    api.resolveLink.mockRejectedValue(fail("outsideFolder"));
    const onNavigate = await open(doc("[fora](../../x.md)"));
    target.querySelector<HTMLElement>("[data-md-link]")!.click();
    await tick();
    expect(target.querySelector('[role="alert"]')?.textContent).toBe("Link fora da pasta aberta");
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("link .md operável por teclado (Enter)", async () => {
    api.resolveLink.mockResolvedValue("b.md");
    const onNavigate = await open(doc("[b](b.md)"));
    const link = target.querySelector<HTMLElement>("[data-md-link]")!;
    expect(link.getAttribute("role")).toBe("link");
    expect(link.tabIndex).toBe(0);
    link.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    await tick();
    expect(onNavigate).toHaveBeenCalledWith("b.md");
  });

  it("sem Worker, o documento aparece como texto puro (falha fechada), nunca como HTML", async () => {
    vi.stubGlobal("Worker", undefined);
    await open(doc("# Título <b>x</b>"));
    expect(target.querySelector("h1")).toBeNull();
    expect(target.querySelector("pre.viewer-plain")?.textContent).toBe("# Título <b>x</b>");
    expect(target.textContent).toContain("Não foi possível renderizar este documento");
    // O aviso traz o detalhe técnico para diagnóstico (tarefa 9.1.2).
    expect(target.querySelector(".notice-detail")?.textContent).toMatch(/^Detalhe técnico: workerCreate: TypeError: /);
  });

  it("erro do Worker: texto puro e aviso com mensagem, arquivo e linha do erro (tarefa 9.1.2)", async () => {
    class SyntaxErrorWorker {
      onmessage: ((event: { data: unknown }) => void) | null = null;
      onerror: ((event: unknown) => void) | null = null;
      postMessage(): void {
        queueMicrotask(() =>
          this.onerror?.({ message: "SyntaxError: Unexpected token '{'", filename: "tauri://localhost/w.js", lineno: 3, colno: 0 }),
        );
      }
      terminate(): void {}
    }
    vi.stubGlobal("Worker", SyntaxErrorWorker);
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    await open(doc("# simples"));
    quiet.mockRestore();
    expect(target.querySelector("pre.viewer-plain")?.textContent).toBe("# simples");
    expect(target.querySelector(".notice-detail")?.textContent).toBe(
      "Detalhe técnico: workerError: SyntaxError: Unexpected token '{' (tauri://localhost/w.js:3:0)",
    );
  });
});

describe("FilesView (FR-004, FR-007, FR-028)", () => {
  it("sem pasta, mostra o convite Abrir pasta…", () => {
    render(FilesView, { initialFolder: null, initialNotice: null });
    expect(target.textContent).toContain("Nenhuma pasta aberta");
    expect(target.querySelector(".files-tree-header button")!.textContent!.trim()).toBe("Abrir pasta…");
  });

  it("pasta anterior indisponível mostra o aviso e o convite", () => {
    render(FilesView, { initialFolder: null, initialNotice: "A última pasta aberta não está mais disponível" });
    expect(target.textContent).toContain("A última pasta aberta não está mais disponível");
    expect(target.textContent).toContain("Nenhuma pasta aberta");
  });

  it("abrir pasta lista a raiz; cancelar o diálogo não muda nada", async () => {
    render(FilesView, { initialFolder: null, initialNotice: null });
    api.openFolder.mockResolvedValueOnce(null);
    target.querySelector<HTMLButtonElement>(".files-tree-header button")!.click();
    await tick();
    expect(api.listDir).not.toHaveBeenCalled();
    expect(target.textContent).toContain("Nenhuma pasta aberta");

    api.openFolder.mockResolvedValueOnce({ displayName: "sample" });
    api.listDir.mockResolvedValue([folder("Artigos"), entry("binario.md")]);
    target.querySelector<HTMLButtonElement>(".files-tree-header button")!.click();
    await tick();
    expect(target.querySelector(".folder-name")?.textContent).toBe("sample");
    expect(target.querySelectorAll('[role="treeitem"]')).toHaveLength(2);
    expect(api.openFolder).toHaveBeenCalledWith(); // sem argumentos: nunca caminho vindo da interface
  });

  it("pasta reaberta na inicialização carrega a árvore", async () => {
    api.listDir.mockResolvedValue([entry("a.md")]);
    render(FilesView, { initialFolder: { displayName: "sample" }, initialNotice: null });
    await tick();
    expect(api.listDir).toHaveBeenCalledWith("");
    expect(target.querySelectorAll('[role="treeitem"]')).toHaveLength(1);
  });

  it("falha ao abrir mostra a mensagem em português", async () => {
    render(FilesView, { initialFolder: null, initialNotice: null });
    api.openFolder.mockRejectedValueOnce({ code: "folderUnavailable", detail: null });
    target.querySelector<HTMLButtonElement>(".files-tree-header button")!.click();
    await tick();
    expect(target.textContent).toContain("A pasta não está disponível");
  });

  it("selecionar arquivo abre no visualizador", async () => {
    api.listDir.mockResolvedValue([entry("a.md")]);
    api.readMarkdown.mockResolvedValue(doc("# Olá"));
    render(FilesView, { initialFolder: { displayName: "s" }, initialNotice: null });
    await tick();
    target.querySelector<HTMLElement>(".tree-row")!.click();
    await tick();
    expect(api.readMarkdown).toHaveBeenCalledWith("a.md");
    expect(target.querySelector("h1")?.textContent).toBe("Olá");
  });
});

describe("SettingsView (FR-019, FR-020, FR-021, FR-024)", () => {
  function store(initial: Partial<UserSettings> = {}) {
    const client = {
      saveSettings: vi.fn(async (s: object) => ({ ...s }) as UserSettings),
      resetSettings: vi.fn(async () => ({ ...DEFAULT_SETTINGS })),
    };
    const s = new SettingsStore(client);
    s.init({ ...DEFAULT_SETTINGS, ...initial });
    return { s, client };
  }

  it("oferece aparência (3 opções) e tamanho do texto (3 níveis), com os padrões marcados", () => {
    const { s } = store();
    render(SettingsView, { store: s });
    const labels = Array.from(target.querySelectorAll("label")).map((l) => l.textContent!.trim());
    expect(labels).toEqual(["Seguir o sistema", "Claro", "Escuro", "Pequeno", "Médio", "Grande"]);
    expect(target.querySelector<HTMLInputElement>('input[name="appearance"]:checked')!.value).toBe("system");
    expect(target.querySelector<HTMLInputElement>('input[name="font-size"]:checked')!.value).toBe("medium");
    expect(target.textContent).toContain("Restaurar padrões");
  });

  it("escolher uma opção tem efeito imediato e grava", async () => {
    const { s, client } = store();
    render(SettingsView, { store: s });
    const dark = target.querySelector<HTMLInputElement>('input[name="appearance"][value="dark"]')!;
    dark.click();
    await tick();
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(client.saveSettings).toHaveBeenCalledTimes(1);
    target.querySelector<HTMLInputElement>('input[name="font-size"][value="large"]')!.click();
    await tick();
    expect(document.documentElement.style.getPropertyValue("--viewer-font-size")).toBe("19px");
  });

  it("Restaurar padrões volta aos valores iniciais", async () => {
    const { s, client } = store({ appearance: "dark", viewerFontSize: "large" });
    render(SettingsView, { store: s });
    target.querySelector<HTMLButtonElement>("button")!.click();
    await tick();
    expect(client.resetSettings).toHaveBeenCalled();
    expect(target.querySelector<HTMLInputElement>('input[name="appearance"]:checked')!.value).toBe("system");
    expect(target.querySelector<HTMLInputElement>('input[name="font-size"]:checked')!.value).toBe("medium");
  });

  it("gravação recusada mostra o aviso na tela", async () => {
    const { s, client } = store();
    client.saveSettings.mockRejectedValue({ code: "invalidSettings", detail: null });
    render(SettingsView, { store: s });
    target.querySelector<HTMLInputElement>('input[name="appearance"][value="light"]')!.click();
    await tick();
    expect(target.querySelector('[role="alert"]')?.textContent).toBe(
      "Configuração inválida; nenhuma alteração foi salva",
    );
  });
});

describe("App (FR-001, FR-007, FR-023, FR-028)", () => {
  const startup = (over: Record<string, unknown> = {}) => ({
    settings: { ...DEFAULT_SETTINGS } as UserSettings,
    warnings: [] as string[],
    folder: null,
    ...over,
  });

  beforeEach(() => {
    settings.init({ ...DEFAULT_SETTINGS });
  });

  it("abre com a barra lateral e o convite para abrir pasta", async () => {
    api.getStartupState.mockResolvedValue(startup());
    render(App, {});
    await tick();
    expect(target.querySelector(".sidebar")).not.toBeNull();
    expect(target.textContent).toContain("Nenhuma pasta aberta");
    expect(target.querySelector(".banner")).toBeNull();
  });

  it("configurações inválidas: abre com padrões e avisa em português (FR-023)", async () => {
    api.getStartupState.mockResolvedValue(startup({ warnings: ["settingsReset"] }));
    render(App, {});
    await tick();
    expect(target.querySelector(".banner")?.textContent).toContain(
      "Não foi possível ler as configurações salvas; os padrões foram restaurados",
    );
    target.querySelector<HTMLButtonElement>(".banner button")!.click();
    flushSync();
    expect(target.querySelector(".banner")).toBeNull();
  });

  it("última pasta indisponível: aviso e convite (FR-028)", async () => {
    api.getStartupState.mockResolvedValue(startup({ warnings: ["lastFolderUnavailable"] }));
    render(App, {});
    await tick();
    expect(target.textContent).toContain("A última pasta aberta não está mais disponível");
    expect(target.textContent).toContain("Abrir pasta…");
  });

  it("aplica as configurações salvas (aparência escura, texto grande, barra recolhida e largura)", async () => {
    api.getStartupState.mockResolvedValue(
      startup({
        settings: { ...DEFAULT_SETTINGS, appearance: "dark", viewerFontSize: "large", sidebarCollapsed: true, sidebarWidth: 320 },
      }),
    );
    render(App, {});
    await tick();
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.style.getPropertyValue("--viewer-font-size")).toBe("19px");
    expect(target.querySelector(".sidebar")!.classList.contains("collapsed")).toBe(true);
  });

  it("troca entre Arquivos e Configurações sem perder a pasta aberta", async () => {
    api.getStartupState.mockResolvedValue(startup({ folder: { displayName: "sample" } }));
    api.listDir.mockResolvedValue([entry("a.md")]);
    render(App, {});
    await tick();
    expect(target.querySelectorAll('[role="treeitem"]')).toHaveLength(1);
    target.querySelectorAll<HTMLButtonElement>(".nav-item")[1]!.click();
    await tick();
    expect(target.querySelector(".settings-view")).not.toBeNull();
    expect(target.querySelector(".files-view")!.closest("[hidden]")).not.toBeNull();
    target.querySelectorAll<HTMLButtonElement>(".nav-item")[0]!.click();
    await tick();
    expect(target.querySelector(".settings-view")).toBeNull();
    expect(target.querySelectorAll('[role="treeitem"]')).toHaveLength(1);
    expect(api.listDir).toHaveBeenCalledTimes(1);
  });
});
