<script lang="ts">
  import { messages } from "../lib/messages";
  import { treeItemId } from "../lib/tree-nav";
  import type { TreeModel } from "../lib/tree.svelte";
  import type { TreeEntry } from "../lib/types";
  import TreeNode from "./TreeNode.svelte";

  interface Props {
    entry: TreeEntry;
    level: number;
    model: TreeModel;
    selectedPath: string | null;
    activePath: string | null;
    idPrefix: string;
    onActivate: (entry: TreeEntry) => void;
  }

  let { entry, level, model, selectedPath, activePath, idPrefix, onActivate }: Props = $props();

  let isDirectory = $derived(entry.kind === "directory");
  let denied = $derived(entry.access === "denied");
  let expandable = $derived(isDirectory && !denied);
  let expanded = $derived(expandable && model.isExpanded(entry.relativePath));
  let loading = $derived(model.loading.has(entry.relativePath));
  let children = $derived(model.children.get(entry.relativePath) ?? []);
  let error = $derived(model.errors.get(entry.relativePath));
  let symlinkNote = $derived(
    entry.kind === "symlink"
      ? entry.symlinkTarget === "insideFile" && entry.isMarkdown
        ? messages.symlinkBadge
        : `${messages.symlinkBadge} · ${messages.symlinkNotFollowed}`
      : null,
  );
</script>

<li
  role="treeitem"
  id={treeItemId(entry.relativePath, idPrefix)}
  aria-level={level}
  aria-expanded={expandable ? expanded : undefined}
  aria-selected={selectedPath === entry.relativePath}
  aria-busy={loading ? "true" : undefined}
  aria-disabled={denied ? "true" : undefined}
  data-path={entry.relativePath}
>
  <div
    class="tree-row"
    class:selected={selectedPath === entry.relativePath}
    class:active={activePath === entry.relativePath}
    class:denied
    style:padding-left={`${(level - 1) * 14 + 6}px`}
    onclick={() => onActivate(entry)}
    role="presentation"
  >
    <span class="tree-glyph" aria-hidden="true">
      {#if expandable}{expanded ? "▾" : "▸"}{:else if isDirectory}▸{:else if entry.kind === "symlink"}↪{:else}•{/if}
    </span>
    <span class="tree-name">{entry.name}</span>
    {#if symlinkNote}<span class="tree-note" title={symlinkNote}>{symlinkNote}</span>{/if}
    {#if denied}<span class="tree-note tree-denied">{messages.permissionDenied}</span>{/if}
  </div>

  {#if expanded}
    <ul role="group">
      {#if loading}
        <li role="none" class="tree-status" style:padding-left={`${level * 14 + 6}px`}>{messages.loadingTree}</li>
      {:else if error}
        <li role="none" class="tree-status tree-error" style:padding-left={`${level * 14 + 6}px`}>{error}</li>
      {:else if children.length === 0}
        <li role="none" class="tree-status" style:padding-left={`${level * 14 + 6}px`}>{messages.emptyFolder}</li>
      {:else}
        {#each children as child (child.relativePath)}
          <TreeNode
            entry={child}
            level={level + 1}
            {model}
            {selectedPath}
            {activePath}
            {idPrefix}
            {onActivate}
          />
        {/each}
      {/if}
    </ul>
  {/if}
</li>
