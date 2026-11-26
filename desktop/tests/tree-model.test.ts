import { describe, expect, it, vi } from "vitest";
import { TreeModel } from "../src/lib/tree.svelte";
import type { TreeEntry } from "../src/lib/types";

const dir = (path: string): TreeEntry => ({
  name: path.split("/").pop()!,
  relativePath: path,
  kind: "directory",
  access: "ok",
  isMarkdown: false,
  symlinkTarget: null,
});

describe("TreeModel (FR-005, FR-029)", () => {
  it("carrega só a raiz e os filhos de cada pasta expandida, sob demanda", async () => {
    const listDir = vi.fn(async (path: string) => (path === "" ? [dir("A")] : [dir("A/B")]));
    const model = new TreeModel(listDir);
    await model.loadRoot();
    expect(listDir).toHaveBeenCalledTimes(1);
    expect(model.rootEntries).toHaveLength(1);
    await model.expand("A");
    expect(listDir).toHaveBeenCalledWith("A");
    expect(model.isExpanded("A")).toBe(true);
    expect(model.children.get("A")).toHaveLength(1);
    model.collapse("A");
    expect(model.isExpanded("A")).toBe(false);
    await model.expand("A"); // já carregada: não lista de novo
    expect(listDir).toHaveBeenCalledTimes(2);
  });

  it("marca carregamento enquanto list_dir não responde", async () => {
    let release!: (e: TreeEntry[]) => void;
    const model = new TreeModel(() => new Promise<TreeEntry[]>((resolve) => (release = resolve)));
    const pending = model.expand("A");
    expect(model.loading.has("A")).toBe(true);
    release([]);
    await pending;
    expect(model.loading.has("A")).toBe(false);
  });

  it("erro de pasta vira mensagem em português sem derrubar a árvore", async () => {
    const model = new TreeModel(async (path) => {
      if (path === "") return [dir("A")];
      throw { code: "permissionDenied", detail: null };
    });
    await model.loadRoot();
    await model.expand("A");
    expect(model.errors.get("A")).toBe("Sem permissão para ler esta pasta");
    expect(model.rootEntries).toHaveLength(1);
  });

  it("falha na raiz mostra a mensagem da pasta indisponível", async () => {
    const model = new TreeModel(async () => {
      throw { code: "folderUnavailable", detail: null };
    });
    await model.loadRoot();
    expect(model.rootError).toBe("A pasta não está disponível");
    expect(model.rootEntries).toEqual([]);
  });

  it("toggle alterna", async () => {
    const model = new TreeModel(async () => []);
    await model.toggle("A");
    expect(model.isExpanded("A")).toBe(true);
    await model.toggle("A");
    expect(model.isExpanded("A")).toBe(false);
  });
});
