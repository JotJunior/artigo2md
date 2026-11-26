<script lang="ts">
  import { onMount } from "svelte";
  import { openFolder } from "../lib/api";
  import { errorMessage, messages } from "../lib/messages";
  import { TreeModel } from "../lib/tree.svelte";
  import type { AppError, OpenFolderInfo, TreeEntry } from "../lib/types";
  import FileTree from "./FileTree.svelte";
  import Viewer from "./Viewer.svelte";

  interface Props {
    /** Pasta reaberta na inicialização (`get_startup_state`), se existir. */
    initialFolder: OpenFolderInfo | null;
    /** Aviso `lastFolderUnavailable` da inicialização. */
    initialNotice: string | null;
  }

  let { initialFolder, initialNotice }: Props = $props();

  // Valores iniciais: lidos uma vez, na criação do componente.
  // svelte-ignore state_referenced_locally
  let folder = $state<OpenFolderInfo | null>(initialFolder);
  // svelte-ignore state_referenced_locally
  let notice = $state<string | null>(initialNotice);
  let model = $state<TreeModel | null>(null);
  let selectedPath = $state<string | null>(null);
  let opening = $state(false);

  function startTree(): void {
    const next = new TreeModel();
    model = next;
    selectedPath = null;
    void next.loadRoot();
  }

  onMount(() => {
    if (folder) startTree();
  });

  async function chooseFolder(): Promise<void> {
    opening = true;
    try {
      const chosen = await openFolder();
      if (chosen === null) return; // diálogo cancelado: nada muda
      folder = chosen;
      notice = null;
      startTree();
    } catch (error) {
      notice = errorMessage((error as AppError).code ?? "io", "folder");
    } finally {
      opening = false;
    }
  }

  function selectEntry(entry: TreeEntry): void {
    selectedPath = entry.relativePath;
  }
</script>

<div class="files-view">
  <div class="files-tree-pane">
    <div class="files-tree-header">
      <button type="button" class="button" onclick={chooseFolder} disabled={opening}>
        {messages.openFolder}
      </button>
      {#if folder}<span class="folder-name" title={folder.displayName}>{folder.displayName}</span>{/if}
    </div>
    {#if notice}
      <p class="notice notice-warning" role="status">{notice}</p>
    {/if}
    {#if folder && model}
      <FileTree {model} {selectedPath} onSelect={selectEntry} />
    {:else}
      <p class="tree-empty">{messages.noFolderOpen}</p>
    {/if}
  </div>
  <div class="files-viewer-pane">
    <Viewer path={selectedPath} onNavigate={(p) => (selectedPath = p)} />
  </div>
</div>
