<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { getMacosVersion, getStackSettings, getStartupState } from "./lib/api";
  import { infra } from "./lib/infra.svelte";
  import { errorMessage, messages, warningMessage } from "./lib/messages";
  import { settings } from "./lib/settings.svelte";
  import type { AppError, GuideProgress, OpenFolderInfo, RuntimeKind, View, Warning } from "./lib/types";
  import FilesView from "./components/FilesView.svelte";
  import InfraPanel from "./components/InfraPanel.svelte";
  import InstallGuide from "./components/InstallGuide.svelte";
  import SettingsView from "./components/SettingsView.svelte";
  import Sidebar from "./components/Sidebar.svelte";

  interface GuideState {
    saved: GuideProgress | null;
    macosVersion: string | null;
    target: { runtime: RuntimeKind; step: "start" } | null;
  }

  let ready = $state(false);
  let view = $state<View>("files");
  let initialFolder = $state<OpenFolderInfo | null>(null);
  let folderNotice = $state<string | null>(null);
  let banner = $state<string | null>(null);
  let guide = $state<GuideState | null>(null);

  /** Abre o guia: passo salvo (FR-006) e versão do macOS lidos do núcleo; falha de leitura não bloqueia. */
  async function openGuide(target: GuideState["target"]): Promise<void> {
    let saved: GuideProgress | null = null;
    let macosVersion: string | null = null;
    try {
      saved = (await getStackSettings()).settings.guide;
    } catch {
      /* sem o passo salvo, o guia começa na comparação */
    }
    try {
      macosVersion = (await getMacosVersion()) ?? null;
    } catch {
      /* sem a versão, nenhum runtime é marcado como indisponível */
    }
    guide = { saved, macosVersion, target };
    view = "infra";
  }

  function leaveGuide(): void {
    guide = null;
    view = "infra";
  }

  // Guia deixado no meio na sessão anterior: reabre no passo em que parou (US4-5).
  async function restoreGuide(): Promise<void> {
    try {
      const saved = (await getStackSettings()).settings.guide;
      if (saved !== null && saved !== undefined) await openGuide(null);
    } catch {
      /* configurações da infraestrutura ilegíveis: o app abre normalmente em "Arquivos" */
    }
  }

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
    infra.start();
    void restoreGuide();
  });

  onDestroy(() => infra.stop());
</script>

{#if ready}
  <div class="app">
    <Sidebar
      {view}
      collapsed={settings.current.sidebarCollapsed}
      width={settings.current.sidebarWidth}
      summary={infra.summary}
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
      {#if view === "infra"}
        <div class="view">
          {#if guide}
            <InstallGuide
              store={infra}
              saved={guide.saved}
              target={guide.target}
              macosVersion={guide.macosVersion}
              onDone={leaveGuide}
            />
          {:else}
            <InfraPanel store={infra} onOpenGuide={(target) => void openGuide(target)} onOpenSettings={() => (view = "settings")} />
          {/if}
        </div>
      {/if}
      {#if view === "settings"}
        <div class="view">
          <SettingsView store={settings} />
        </div>
      {/if}
    </main>
  </div>
  {#if infra.closing}
    <div class="closing-overlay" role="status" aria-live="polite">{messages.poolClosing}</div>
  {/if}
{/if}
