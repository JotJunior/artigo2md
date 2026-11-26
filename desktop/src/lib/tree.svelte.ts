// Modelo reativo da árvore: raiz e filhos carregados sob demanda por `list_dir` (FR-029).
// Só o estado e a leitura vivem aqui; a marcação fica em TreeNode.svelte.

import { SvelteMap, SvelteSet } from "svelte/reactivity";
import { listDir as defaultListDir } from "./api";
import { errorMessage } from "./messages";
import type { AppError, TreeEntry } from "./types";

export type ListDir = (relativePath: string) => Promise<TreeEntry[]>;

export class TreeModel {
  /** Itens do primeiro nível; `null` enquanto a raiz carrega. */
  rootEntries = $state<TreeEntry[] | null>(null);
  rootError = $state<string | null>(null);
  readonly expanded = new SvelteSet<string>();
  readonly children = new SvelteMap<string, TreeEntry[]>();
  readonly loading = new SvelteSet<string>();
  readonly errors = new SvelteMap<string, string>();

  constructor(private readonly listDir: ListDir = defaultListDir) {}

  async loadRoot(): Promise<void> {
    this.rootEntries = null;
    this.rootError = null;
    try {
      this.rootEntries = await this.listDir("");
    } catch (error) {
      this.rootEntries = [];
      this.rootError = errorMessage((error as AppError).code ?? "io", "folder");
    }
  }

  isExpanded(path: string): boolean {
    return this.expanded.has(path);
  }

  async expand(path: string): Promise<void> {
    if (this.expanded.has(path)) return;
    this.expanded.add(path);
    if (this.children.has(path) || this.loading.has(path)) return;
    this.loading.add(path);
    this.errors.delete(path);
    try {
      this.children.set(path, await this.listDir(path));
    } catch (error) {
      this.children.set(path, []);
      this.errors.set(path, errorMessage((error as AppError).code ?? "io", "folder"));
    } finally {
      this.loading.delete(path);
    }
  }

  collapse(path: string): void {
    this.expanded.delete(path);
  }

  async toggle(path: string): Promise<void> {
    if (this.expanded.has(path)) this.collapse(path);
    else await this.expand(path);
  }
}
