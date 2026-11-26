<script lang="ts">
  import { messages } from "../lib/messages";
  import { navItemsFromElements, treeItemId, treeKeyAction } from "../lib/tree-nav";
  import type { TreeModel } from "../lib/tree.svelte";
  import type { TreeEntry } from "../lib/types";
  import TreeNode from "./TreeNode.svelte";

  interface Props {
    model: TreeModel;
    selectedPath: string | null;
    onSelect: (entry: TreeEntry) => void;
  }

  let { model, selectedPath, onSelect }: Props = $props();

  const idPrefix = "tree";
  let activePath = $state<string | null>(null);
  let treeElement = $state<HTMLElement | null>(null);

  function visibleItems(): HTMLElement[] {
    return treeElement ? Array.from(treeElement.querySelectorAll<HTMLElement>('[role="treeitem"]')) : [];
  }

  function findEntry(path: string): TreeEntry | undefined {
    if (model.rootEntries?.some((e) => e.relativePath === path)) {
      return model.rootEntries.find((e) => e.relativePath === path);
    }
    for (const list of model.children.values()) {
      const found = list.find((e) => e.relativePath === path);
      if (found) return found;
    }
    return undefined;
  }

  /** Clique ou Enter/Espaço: pasta alterna; arquivo ou atalho dentro da pasta abre no visualizador. */
  function activate(entry: TreeEntry): void {
    activePath = entry.relativePath;
    if (entry.access === "denied") return;
    if (entry.kind === "directory") {
      void model.toggle(entry.relativePath);
    } else if (entry.kind === "symlink" && entry.symlinkTarget !== "insideFile") {
      // Atalho que não aponta para arquivo da pasta aberta: identificado, nunca seguido (FR-030).
    } else {
      onSelect(entry);
    }
  }

  function handleKey(event: KeyboardEvent): void {
    const elements = visibleItems();
    const items = navItemsFromElements(elements);
    const current = elements.findIndex((el) => el.dataset.path === activePath);
    const action = treeKeyAction(event.key, items, current);
    if (action.type === "none") return;
    event.preventDefault();
    const path = (index: number): string => elements[index]!.dataset.path!;
    switch (action.type) {
      case "focus":
        activePath = path(action.index);
        elements[action.index]!.scrollIntoView?.({ block: "nearest" });
        break;
      case "expand":
        void model.expand(path(action.index));
        break;
      case "collapse":
        model.collapse(path(action.index));
        break;
      case "activate": {
        const entry = findEntry(path(action.index));
        if (entry) activate(entry);
        break;
      }
    }
  }

  function focusFirstOnEnter(): void {
    if (activePath === null) {
      const first = visibleItems()[0];
      if (first) activePath = first.dataset.path ?? null;
    }
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<ul
  class="file-tree"
  role="tree"
  tabindex="0"
  bind:this={treeElement}
  aria-label={messages.fileTreeLabel}
  aria-activedescendant={activePath ? treeItemId(activePath, idPrefix) : undefined}
  onkeydown={handleKey}
  onfocus={focusFirstOnEnter}
>
  {#if model.rootEntries === null}
    <li role="none" class="tree-status">{messages.loadingTree}</li>
  {:else if model.rootError}
    <li role="none" class="tree-status tree-error">{model.rootError}</li>
  {:else if model.rootEntries.length === 0}
    <li role="none" class="tree-status">{messages.emptyFolder}</li>
  {:else}
    {#each model.rootEntries as entry (entry.relativePath)}
      <TreeNode
        {entry}
        level={1}
        {model}
        {selectedPath}
        {activePath}
        {idPrefix}
        onActivate={activate}
      />
    {/each}
  {/if}
</ul>
