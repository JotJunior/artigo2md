<script lang="ts">
  import { onMount } from "svelte";
  import { getStartupState } from "./lib/api";
  import { errorMessage, messages, warningMessage } from "./lib/messages";
  import { settings } from "./lib/settings.svelte";
  import type { AppError, OpenFolderInfo, View, Warning } from "./lib/types";
  import FilesView from "./components/FilesView.svelte";
  import SettingsView from "./components/SettingsView.svelte";
  import Sidebar from "./components/Sidebar.svelte";

  let ready = $state(false);
  let view = $state<View>("files");
  let initialFolder = $state<OpenFolderInfo | null>(null);
  let folderNotice = $state<string | null>(null);
  let banner = $state<string | null>(null);

  onMount(async () => {
    try {
      const startup = await getStartupState();
      settings.init(startup.settings);
      initialFolder = startup.folder;
      const warnings: Warning[] = startup.warnings;
      if (warnings.includes("lastFolderUnavailable")) folderNotice = warningMessage("lastFolderUnavailable");
      // FR-023: configurações ilegíveis ou inválidas → padrões e aviso em português.
      if (warnings.includes("settingsReset")) banner = warningMessage("settingsReset");
    } catch (error) {
      settings.init(settings.current);
      banner = errorMessage((error as AppError).code ?? "io", "document");
    }
    ready = true;
  });
</script>

{#if ready}
  <div class="app">
    <Sidebar
      {view}
      collapsed={settings.current.sidebarCollapsed}
      width={settings.current.sidebarWidth}
      onSelect={(next) => (view = next)}
      onToggle={() => void settings.update({ sidebarCollapsed: !settings.current.sidebarCollapsed })}
      onResize={(width) => void settings.update({ sidebarWidth: width })}
    />
    <main class="content">
      {#if banner}
        <div class="notice notice-warning banner" role="status">
          <span>{banner}</span>
          <button type="button" class="button button-small" onclick={() => (banner = null)}>
            {messages.dismiss}
          </button>
        </div>
      {/if}
      <div class="view" hidden={view !== "files"}>
        <FilesView {initialFolder} initialNotice={folderNotice} />
      </div>
      {#if view === "settings"}
        <div class="view">
          <SettingsView store={settings} />
        </div>
      {/if}
    </main>
  </div>
{/if}
