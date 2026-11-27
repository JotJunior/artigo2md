<script lang="ts">
  import type { InfraStore } from "../lib/infra.svelte";
  import {
    downloadConfirmText,
    downloadFailureMessages,
    messages,
    progressText,
    stepNames,
    stepStateLabels,
  } from "../lib/messages";
  import type { FirstRunStep, StepKind } from "../lib/types";

  interface Props {
    store: InfraStore;
    steps: FirstRunStep[];
    /** Outra instância controla a infraestrutura: nenhuma ação. */
    readOnly: boolean;
  }

  let { store, steps, readOnly }: Props = $props();

  // Etapa cujo pedido de confirmação está aberto: nada é baixado sem o "Baixar agora" (FR-031).
  let confirming = $state<StepKind | null>(null);

  let allDone = $derived(steps.length > 0 && steps.every((step) => step.state === "done"));
  let locked = $derived(readOnly || store.pending !== null);

  function actionLabel(step: FirstRunStep): string {
    if (step.state === "failed") return step.failure === "interrupted" ? messages.resumeDownload : messages.retryDownload;
    if (step.failure === "interrupted") return messages.resumeDownload;
    return step.step === "images" ? messages.prepareImages : messages.prepareModels;
  }

  function confirmDownload(kind: StepKind): void {
    confirming = null;
    void store.prepare(kind);
  }

  function percent(step: FirstRunStep): number | null {
    if (step.totalBytes === null || step.totalBytes <= 0 || step.downloadedBytes === null) return null;
    return Math.min(100, Math.floor((step.downloadedBytes / step.totalBytes) * 100));
  }
</script>

<section class="infra-section" aria-labelledby="first-run-title">
  <h2 id="first-run-title">{messages.firstRunTitle}</h2>
  <p class="infra-muted">{allDone ? messages.firstRunAllDone : messages.firstRunIntro}</p>

  <ul class="first-run-list plain-list">
    {#each steps as step (step.step)}
      <li
        class="first-run-step"
        class:step-attention={store.attention === step.step}
        data-step={step.step}
        aria-busy={step.state === "downloading"}
      >
        <div class="infra-row">
          <strong>{stepNames[step.step]}</strong>
          <span class="state-pill" data-state={step.state} role="status">{stepStateLabels[step.state]}</span>
        </div>
        <div class="infra-muted">
          {messages.approxSizeLabel}: {step.approxSize} · {messages.locationLabel}: {step.location}
        </div>

        {#if step.state === "downloading"}
          {@const known = percent(step)}
          {#if known !== null}
            <progress class="download" value={known} max="100" aria-label={`${stepNames[step.step]}: ${known}%`}></progress>
          {/if}
          <p class="download-progress">{progressText(step)}</p>
          {#if store.isStalled(step.step)}
            <p class="notice notice-warning" role="status">{messages.progressNoResponse}</p>
          {/if}
        {/if}

        {#if step.state === "failed" && step.failure}
          <p class="notice notice-error" role="status">{downloadFailureMessages[step.failure]}</p>
        {/if}

        {#if step.state === "pending" || step.state === "failed"}
          {#if confirming === step.step}
            <div class="confirm-box" role="group" aria-label={messages.confirmTitle}>
              <p>{downloadConfirmText(step)}</p>
              <div class="infra-actions">
                <button type="button" class="button" onclick={() => confirmDownload(step.step)}>
                  {messages.confirmDownload}
                </button>
                <button type="button" class="button" onclick={() => (confirming = null)}>
                  {messages.cancelDownload}
                </button>
              </div>
            </div>
          {:else}
            <div class="infra-actions">
              <button type="button" class="button" disabled={locked} onclick={() => (confirming = step.step)}>
                {actionLabel(step)}
              </button>
            </div>
          {/if}
        {/if}
      </li>
    {/each}
  </ul>
</section>
