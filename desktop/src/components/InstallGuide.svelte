<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import * as api from "../lib/api";
  import {
    GUIDE,
    guideSteps,
    resumeProgress,
    runtimeAvailability,
    startStepIndex,
    stepCount,
  } from "../lib/guide";
  import type { InfraStore } from "../lib/infra.svelte";
  import { messages } from "../lib/messages";
  import { RUNTIME_KINDS, type GuideProgress, type RuntimeKind } from "../lib/types";

  interface Props {
    store: InfraStore;
    /** Passo salvo entre execuções do app (`StackSettings.guide`). */
    saved: GuideProgress | null;
    /** Abertura direcionada: o passo de iniciar de um runtime já instalado e parado. */
    target?: { runtime: RuntimeKind; step: "start" } | null;
    /** Versão do macOS (ex.: "14.5"); `null` quando não pôde ser lida. */
    macosVersion?: string | null;
    /** Sai do guia para a tela de infraestrutura. */
    onDone: () => void;
  }

  let { store, saved, target = null, macosVersion = null, onDone }: Props = $props();

  function initialProgress(): GuideProgress {
    if (target) return { runtime: target.runtime, step: startStepIndex(target.runtime) };
    return resumeProgress(saved) ?? { runtime: null, step: 0 };
  }

  // Valores iniciais lidos uma vez, na criação do componente.
  // svelte-ignore state_referenced_locally
  let progress = $state<GuideProgress>(initialProgress());
  let copied = $state<string | null>(null);
  let copyFailed = $state(false);
  let finished = $state(false);

  let runtime = $derived(progress.runtime);
  let guide = $derived(runtime === null ? null : GUIDE[runtime]);
  let steps = $derived(runtime === null ? [] : guideSteps(runtime));
  let current = $derived(steps[progress.step] ?? null);
  let isReady = $derived(store.snapshot?.runtime.overall === "ready");

  function persist(next: GuideProgress | null): void {
    // Falha ao salvar o passo não atrapalha o guia: ele só não será lembrado.
    void api.saveGuideProgress(next).catch(() => undefined);
  }

  function choose(kind: RuntimeKind): void {
    progress = { runtime: kind, step: 0 };
    persist(progress);
  }

  function backToComparison(): void {
    progress = { runtime: null, step: 0 };
    persist(progress);
  }

  function move(delta: number): void {
    if (runtime === null) return;
    const next = progress.step + delta;
    if (next < 0) return backToComparison();
    progress = { runtime, step: Math.min(next, stepCount(runtime) - 1) };
    persist(progress);
  }

  /** Fechar na comparação: nada a lembrar, o guia não reabre sozinho na próxima abertura. */
  function closeFromComparison(): void {
    persist(null);
    onDone();
  }

  async function copy(text: string): Promise<void> {
    copyFailed = false;
    try {
      await navigator.clipboard.writeText(text);
      copied = text;
    } catch {
      copied = null;
      copyFailed = true;
    }
  }

  onMount(() => {
    void store.setGuideOpen(true);
  });
  onDestroy(() => {
    void store.setGuideOpen(false);
  });

  // "Docker pronto": o guia se encerra sozinho e leva à tela de infraestrutura (FR-006).
  $effect(() => {
    if (isReady && !finished) {
      finished = true;
      persist(null);
      onDone();
    }
  });
</script>

<section class="guide-view" aria-labelledby="guide-title">
  <h1 id="guide-title">{messages.guideTitle}</h1>

  {#if finished}
    <p role="status" class="guide-ready">{messages.guideReady}</p>
    <button type="button" class="button" onclick={onDone}>{messages.guideGoToInfra}</button>
  {:else if guide === null}
    <p>{messages.guideIntro}</p>
    <div class="guide-compare">
      {#each RUNTIME_KINDS as kind (kind)}
        {@const info = GUIDE[kind]}
        {@const availability = runtimeAvailability(kind, macosVersion)}
        <article class="guide-card" data-runtime={kind} aria-labelledby={`guide-card-${kind}`}>
          <h2 id={`guide-card-${kind}`}>{info.name}</h2>
          <p>{info.license}</p>
          {#if info.note}<p class="infra-muted">{info.note}</p>{/if}
          <p class="infra-muted">{messages.guideSource}: <code>{info.licenseSource}</code></p>
          {#if !availability.available}
            <p class="notice notice-warning guide-unavailable" role="status">
              {messages.guideUnavailablePrefix}: {info.name} {messages.guideMacosNeeds} {availability.requires} {messages.guideMacosOrNewer}.
            </p>
          {/if}
          <button
            type="button"
            class="button"
            disabled={!availability.available}
            aria-label={`${messages.guideChoose} ${info.name}`}
            onclick={() => choose(kind)}
          >
            {messages.guideChoose}
          </button>
        </article>
      {/each}
    </div>
    <button type="button" class="button" onclick={closeFromComparison}>{messages.guideClose}</button>
  {:else if current}
    <h2>{guide.name}</h2>
    <p role="status" class="guide-step-counter">Passo {progress.step + 1} de {steps.length}</p>
    <p class="guide-step-text">{current.text}</p>

    {#each current.copyable as text (text)}
      <div class="guide-copy">
        <code>{text}</code>
        <button type="button" class="button button-small" onclick={() => void copy(text)}>
          {copied === text ? messages.guideCopied : messages.guideCopy}
        </button>
      </div>
    {/each}
    {#if copyFailed}
      <p class="notice notice-warning" role="status">{messages.guideCopyFailed}</p>
    {/if}

    <p class="infra-muted guide-source">{messages.guideSource}: <code>{current.sourceUrl}</code></p>

    <div class="guide-nav">
      <button type="button" class="button" onclick={() => move(-1)}>{messages.guideBack}</button>
      {#if progress.step < steps.length - 1}
        <button type="button" class="button" onclick={() => move(1)}>{messages.guideNext}</button>
      {:else}
        <button type="button" class="button" onclick={onDone}>{messages.guideFinish}</button>
      {/if}
      <button type="button" class="button" onclick={backToComparison}>{messages.guideChangeRuntime}</button>
    </div>
    <p class="infra-muted guide-watching">{messages.guideWatching}</p>
  {/if}
</section>
