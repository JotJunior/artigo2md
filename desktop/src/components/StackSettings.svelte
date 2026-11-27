<script lang="ts">
  import { onMount } from "svelte";
  import * as api from "../lib/api";
  import { activeOperations, infra as defaultInfra, type InfraStore } from "../lib/infra.svelte";
  import { dataDirMessage, errorMessage, messages, repoMessage } from "../lib/messages";
  import { DEFAULT_STACK_PORTS, type AppError, type StackPorts, type StackSettings } from "../lib/types";

  interface Props {
    store?: InfraStore;
  }

  let { store = defaultInfra }: Props = $props();

  let loading = $state(true);
  let loadError = $state<string | null>(null);
  let settings = $state<StackSettings | null>(null);
  let ports = $state<StackPorts | null>(null);

  // Rascunhos dos campos numéricos (texto, para validar antes de gravar).
  let workers = $state("");
  let minFree = $state("");
  let portDrafts = $state({ postgres: "", rabbitmq: "", api: "" });
  let errors = $state<Record<string, string>>({});
  let saveNotice = $state<{ kind: "info" | "error"; text: string } | null>(null);
  let picking = $state(false);

  let busy = $derived(store.pending !== null || activeOperations(store.snapshot).length > 0);
  let portsEditable = $derived(ports !== null);

  function fill(next: { settings: StackSettings; ports: StackPorts | null }): void {
    settings = next.settings;
    ports = next.ports;
    workers = String(next.settings.workers);
    minFree = String(next.settings.minFreeMemoryMb);
    const shown = next.ports ?? DEFAULT_STACK_PORTS;
    portDrafts = { postgres: String(shown.postgres), rabbitmq: String(shown.rabbitmq), api: String(shown.api) };
  }

  async function load(): Promise<void> {
    loading = true;
    loadError = null;
    try {
      fill(await api.getStackSettings());
    } catch (raw) {
      settings = null;
      ports = null;
      const code = (raw as Partial<AppError>)?.code ?? "io";
      // `.env` com chave repetida, "$" ou porta inválida: a tela abre mesmo assim e mostra o erro.
      loadError = code === "invalidParams" ? messages.stackSettingsEnvInvalid : errorMessage(code, "settings");
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void load();
  });

  const INTEGER = /^\d+$/;

  function validate(): { workers: number; minFree: number; ports: StackPorts | null } | null {
    const next: Record<string, string> = {};
    const w = INTEGER.test(workers.trim()) ? Number(workers.trim()) : NaN;
    if (!Number.isInteger(w) || w < 1) next.workers = messages.workersInvalid;
    const m = INTEGER.test(minFree.trim()) ? Number(minFree.trim()) : NaN;
    if (!Number.isInteger(m) || m < 0) next.minFree = messages.minFreeInvalid;

    let parsedPorts: StackPorts | null = null;
    if (portsEditable) {
      const labels = { postgres: messages.portPostgres, rabbitmq: messages.portRabbitmq, api: messages.portApi };
      const values: Record<string, number> = {};
      for (const key of ["postgres", "rabbitmq", "api"] as const) {
        const text = portDrafts[key].trim();
        const value = INTEGER.test(text) ? Number(text) : NaN;
        if (!Number.isInteger(value) || value < 1 || value > 65535) next[key] = `${labels[key]} ${messages.portInvalid}`;
        else values[key] = value;
      }
      if (!next.postgres && !next.rabbitmq && !next.api) parsedPorts = values as unknown as StackPorts;
    }
    errors = next;
    if (Object.keys(next).length > 0) return null;
    return { workers: w, minFree: m, ports: parsedPorts };
  }

  async function save(): Promise<void> {
    saveNotice = null;
    if (!settings) return;
    const valid = validate();
    if (!valid) return;
    try {
      // `repoPath` e `dataDir` seguem exatamente os valores gravados: só mudam pelos seletores (M1).
      const response = await api.saveStackSettings(
        { ...settings, workers: valid.workers, minFreeMemoryMb: valid.minFree },
        valid.ports,
      );
      fill(response);
      saveNotice = { kind: "info", text: messages.stackSettingsSaved };
    } catch (raw) {
      saveNotice = { kind: "error", text: errorMessage((raw as Partial<AppError>)?.code ?? "io", "settings") };
    }
  }

  async function pick(which: "repo" | "data"): Promise<void> {
    saveNotice = null;
    picking = true;
    try {
      // O seletor nativo é aberto pelo Rust; a interface nunca envia um caminho novo (M1).
      if (which === "repo") await api.pickRepoFolder();
      else await api.pickDataDir();
      await load();
      void store.recheck();
    } catch (raw) {
      saveNotice = { kind: "error", text: errorMessage((raw as Partial<AppError>)?.code ?? "io", "settings") };
    } finally {
      picking = false;
    }
  }
</script>

<section class="stack-settings" aria-labelledby="stack-settings-title">
  <h2 id="stack-settings-title">{messages.stackSettingsTitle}</h2>

  {#if loading}
    <p role="status" aria-busy="true">{messages.stackSettingsLoading}</p>
  {:else if loadError}
    <p class="notice notice-error stack-load-error" role="alert">{loadError}</p>
    <div class="infra-actions">
      <button type="button" class="button" onclick={() => void load()}>{messages.stackSettingsRetry}</button>
      <button type="button" class="button pick-repo" disabled={busy || picking} onclick={() => void pick("repo")}>
        {messages.repoFolderLabel}: {messages.chooseFolder}
      </button>
    </div>
  {:else if settings}
    {#if busy}
      <p class="notice notice-warning" role="status">{messages.stackSettingsBusy}</p>
    {/if}
    {#if saveNotice}
      <p class={`notice ${saveNotice.kind === "error" ? "notice-error" : "notice-info"}`} role={saveNotice.kind === "error" ? "alert" : "status"}>
        {saveNotice.text}
      </p>
    {/if}

    <fieldset class="setting-group">
      <legend>{messages.repoFolderLabel}</legend>
      <p class="path-text repo-path">{settings.repoPath ?? messages.folderNotSet}</p>
      {#if store.snapshot}<p class="infra-muted">{repoMessage(store.snapshot.repo)}</p>{/if}
      <button type="button" class="button pick-repo" disabled={busy || picking} onclick={() => void pick("repo")}>
        {settings.repoPath ? messages.changeFolder : messages.chooseFolder}
      </button>
    </fieldset>

    <fieldset class="setting-group">
      <legend>{messages.dataDirLabel}</legend>
      <p class="path-text data-path">{settings.dataDir}</p>
      {#if store.snapshot}<p class="infra-muted">{dataDirMessage(store.snapshot.dataDir)}</p>{/if}
      <button type="button" class="button pick-data" disabled={busy || picking} onclick={() => void pick("data")}>
        {messages.changeFolder}
      </button>
    </fieldset>

    <fieldset class="setting-group">
      <legend>{messages.sectionPool}</legend>
      <div class="field">
        <label for="stack-workers">{messages.workersLabel}</label>
        <input id="stack-workers" inputmode="numeric" bind:value={workers} aria-describedby="stack-workers-help" aria-invalid={errors.workers ? "true" : undefined} />
        <span id="stack-workers-help" class="infra-muted">{messages.workersHelp}</span>
        {#if errors.workers}<span class="field-error" role="alert">{errors.workers}</span>{/if}
      </div>
      <div class="field">
        <label for="stack-minfree">{messages.minFreeLabel}</label>
        <input id="stack-minfree" inputmode="numeric" bind:value={minFree} aria-describedby="stack-minfree-help" aria-invalid={errors.minFree ? "true" : undefined} />
        <span id="stack-minfree-help" class="infra-muted">{messages.minFreeHelp}</span>
        {#if errors.minFree}<span class="field-error" role="alert">{errors.minFree}</span>{/if}
      </div>
    </fieldset>

    <details class="setting-group advanced">
      <summary>{messages.advancedSettings}</summary>
      <p class="infra-muted">{messages.portsHelp}</p>
      {#if !portsEditable}<p class="infra-muted">{messages.portsDefaultsNote}</p>{/if}
      {#each [["postgres", messages.portPostgres], ["rabbitmq", messages.portRabbitmq], ["api", messages.portApi]] as [key, label] (key)}
        <div class="field">
          <label for={`stack-port-${key}`}>{label}</label>
          <input
            id={`stack-port-${key}`}
            inputmode="numeric"
            disabled={!portsEditable}
            bind:value={portDrafts[key as "postgres" | "rabbitmq" | "api"]}
            aria-invalid={errors[key as string] ? "true" : undefined}
          />
          {#if errors[key as string]}<span class="field-error" role="alert">{errors[key as string]}</span>{/if}
        </div>
      {/each}
    </details>

    <button type="button" class="button save-stack" disabled={busy} onclick={() => void save()}>
      {messages.saveStackSettings}
    </button>
  {/if}
</section>
