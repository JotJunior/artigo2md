<script lang="ts">
  import { activeOperations, type InfraStore } from "../lib/infra.svelte";
  import {
    dataDirMessage,
    formatTime,
    messages,
    operationKindLabels,
    operationStageMessages,
    poolStateLabels,
    repoMessage,
    runtimeMessage,
    runtimeNames,
    runtimeStateLabels,
    serviceAriaLabel,
    serviceNames,
    serviceStateLabels,
    summaryIndicatorText,
    workerExitReasonMessages,
  } from "../lib/messages";
  import FirstRunList from "./FirstRunList.svelte";
  import type { RuntimeKind } from "../lib/types";

  interface Props {
    store: InfraStore;
    /** Abre o guia de instalação (num runtime e passo, se informados). */
    onOpenGuide: (target: { runtime: RuntimeKind; step: "start" } | null) => void;
    onOpenSettings: () => void;
  }

  let { store, onOpenGuide, onOpenSettings }: Props = $props();

  let showLines = $state(false);

  let snap = $derived(store.snapshot);
  let readOnly = $derived(snap?.readOnly ?? false);
  let working = $derived(store.pending !== null || activeOperations(snap).length > 0);
  let ready = $derived(snap?.runtime.overall === "ready");
  let repoOk = $derived(snap?.repo.status === "ok");
  let dataOk = $derived(snap?.dataDir.status === "ok");
  let poolState = $derived(snap?.pool.state ?? "stopped");

  let canStartStack = $derived(!readOnly && !working && ready && repoOk && dataOk);
  let canStopStack = $derived(!readOnly && !working && ready && repoOk);
  let canStartWorkers = $derived(!readOnly && !working && repoOk && (poolState === "stopped" || poolState === "crashed"));
  let canStopWorkers = $derived(!readOnly && !working && (poolState === "running" || poolState === "starting"));

  let stoppedRuntime = $derived(snap?.runtime.runtimes.find((entry) => entry.state === "stopped") ?? null);
  let api = $derived(snap?.services.find((service) => service.service === "api") ?? null);
  let hasOperationInfo = $derived((snap?.operations.length ?? 0) > 0 || store.notice?.kind === "error");

  function toggleLog(): void {
    if (store.technicalLog === null) void store.loadTechnicalLog();
    else store.hideTechnicalLog();
  }
</script>

<section class="infra-view" aria-labelledby="infra-title">
  <h1 id="infra-title">{messages.infraTitle}</h1>
  <p class="infra-muted">{messages.infraIntro}</p>

  <p class="state-summary" role="status">{summaryIndicatorText(store.summary)}</p>

  {#if store.readFailed}
    <p class="notice notice-warning" role="status">{messages.readError}</p>
  {/if}

  {#if store.notice}
    <div class={`notice ${store.notice.kind === "error" ? "notice-error" : "notice-info"} banner`} role={store.notice.kind === "error" ? "alert" : "status"}>
      <span>{store.notice.text}</span>
      <button type="button" class="button button-small" onclick={() => store.dismissNotice()}>
        {messages.dismissNotice}
      </button>
    </div>
  {/if}

  {#if snap}
    {#if readOnly}
      <p class="notice notice-warning" role="status">{messages.readOnlyNotice}</p>
    {/if}

    <div class="infra-actions">
      <button type="button" class="button" disabled={store.pending !== null} onclick={() => void store.recheck()}>
        {messages.recheck}
      </button>
      <span class="infra-muted">{messages.verifiedAt} {formatTime(snap.runtime.checkedAt)}</span>
    </div>

    <section class="infra-section" aria-labelledby="sec-docker">
      <h2 id="sec-docker">{messages.sectionDocker}</h2>
      <p role="status" class="runtime-message">{runtimeMessage(snap.runtime)}</p>
      <ul class="plain-list">
        {#each snap.runtime.runtimes as entry (entry.kind)}
          <li class="infra-row" aria-label={`${runtimeNames[entry.kind]}: ${runtimeStateLabels[entry.state]}`}>
            <span>{runtimeNames[entry.kind]}</span>
            <span class="state-pill" data-state={entry.state === "running" ? "healthy" : entry.state === "stopped" ? "starting" : "stopped"}>
              {runtimeStateLabels[entry.state]}
            </span>
          </li>
        {/each}
      </ul>
      {#if snap.runtime.overall === "noRuntime" || snap.runtime.overall === "composeMissing"}
        <div class="infra-actions">
          <button type="button" class="button" onclick={() => onOpenGuide(null)}>{messages.openGuide}</button>
        </div>
      {:else if snap.runtime.overall === "stopped" && stoppedRuntime}
        <div class="infra-actions">
          <button type="button" class="button" onclick={() => onOpenGuide({ runtime: stoppedRuntime.kind, step: "start" })}>
            {messages.showStartSteps}
          </button>
        </div>
      {/if}
    </section>

    <section class="infra-section" aria-labelledby="sec-repo">
      <h2 id="sec-repo">{messages.sectionRepo}</h2>
      <p class="repo-message" role={repoOk ? undefined : "status"}>{repoMessage(snap.repo)}</p>
      {#if !repoOk}
        <div class="infra-actions">
          <button type="button" class="button" onclick={onOpenSettings}>{messages.navSettings}</button>
        </div>
      {/if}
    </section>

    <section class="infra-section" aria-labelledby="sec-data">
      <h2 id="sec-data">{messages.sectionDataDir}</h2>
      <p class="path-text">{snap.dataDir.path}</p>
      <p class="data-dir-message" role={dataOk ? undefined : "status"}>{dataDirMessage(snap.dataDir)}</p>
    </section>

    <section class="infra-section" aria-labelledby="sec-services">
      <h2 id="sec-services">{messages.sectionServices}</h2>
      <ul class="plain-list">
        {#each snap.services as service (service.service)}
          <li class="infra-row service-row" data-service={service.service} aria-label={serviceAriaLabel(service.service, service.state)}>
            <span>{serviceNames[service.service]}</span>
            <span class="state-pill" data-state={service.state} role="status">{serviceStateLabels[service.state]}</span>
            <span class="infra-muted">
              {messages.addressLabel}: <code>{service.address}</code> · {messages.verifiedAt} {formatTime(service.checkedAt)}
            </span>
          </li>
        {/each}
      </ul>
      {#if snap.consumerConnected === false}
        <p class="notice notice-warning consumer-warning" role="status">
          <strong>{messages.consumerStopped}.</strong> {messages.consumerStoppedHint}
        </p>
      {/if}
    </section>

    <section class="infra-section" aria-labelledby="sec-pool">
      <h2 id="sec-pool">{messages.sectionPool}</h2>
      <div class="infra-row">
        <span class="state-pill pool-state" data-state={snap.pool.state} role="status">{poolStateLabels[snap.pool.state]}</span>
        <span class="infra-muted">{snap.pool.workers} workers · memória livre mínima {snap.pool.minFreeMemoryMb} MB</span>
      </div>
      {#if snap.pool.state === "stopping"}
        <p role="status" class="pool-closing">{messages.poolClosing}</p>
      {/if}
      {#if snap.pool.state === "crashed" && snap.pool.reason}
        <p class="notice notice-error pool-reason" role="status">{workerExitReasonMessages[snap.pool.reason]}</p>
      {/if}
      {#if snap.pool.memoryWarning}
        <p class="notice notice-warning" role="status">{messages.poolMemoryWarning}</p>
      {/if}
      {#if snap.pool.state === "orphanFound"}
        <div class="notice notice-warning orphan-offer" role="group" aria-label={messages.poolOrphanFound}>
          <p>{messages.poolOrphanFound}</p>
          <div class="infra-actions">
            <button type="button" class="button" disabled={readOnly || working} onclick={() => void store.resolvePreviousPool("stop")}>
              {messages.poolStop}
            </button>
            <button type="button" class="button" disabled={readOnly || working} onclick={() => void store.resolvePreviousPool("keep")}>
              {messages.poolKeep}
            </button>
          </div>
        </div>
      {/if}
      {#if snap.pool.adopted}
        <p class="infra-muted">{messages.poolAdopted}</p>
      {/if}
      <div class="infra-actions">
        <button type="button" class="button button-small" aria-expanded={showLines} onclick={() => (showLines = !showLines)}>
          {showLines ? messages.hideWorkerLines : messages.showWorkerLines}
        </button>
      </div>
      {#if showLines}
        {#if snap.pool.lines.length > 0}
          <pre class="technical-log worker-lines" aria-label={messages.showWorkerLines}>{snap.pool.lines.join("\n")}</pre>
        {:else}
          <p class="infra-muted">{messages.noWorkerLines}</p>
        {/if}
      {/if}
    </section>

    <section class="infra-section" aria-labelledby="sec-actions">
      <h2 id="sec-actions">{messages.sectionActions}</h2>
      {#each activeOperations(snap) as operation (operation.kind)}
        <p role="status" aria-busy="true" class="operation-stage">
          <strong>{operationKindLabels[operation.kind]}</strong> — {operationStageMessages[operation.stage]}
        </p>
      {/each}
      {#if store.pending !== null && activeOperations(snap).length === 0}
        <p role="status" aria-busy="true">{messages.working}</p>
      {/if}
      <div class="infra-actions">
        <button type="button" class="button action-start-stack" disabled={!canStartStack} onclick={() => void store.startStack()}>
          {messages.startInfra}
        </button>
        <button type="button" class="button action-stop-stack" disabled={!canStopStack} onclick={() => void store.stopStack()}>
          {messages.stopInfra}
        </button>
        <button type="button" class="button action-start-workers" disabled={!canStartWorkers} onclick={() => void store.startWorkers()}>
          {poolState === "crashed" ? messages.restartWorkers : messages.startWorkers}
        </button>
        <button type="button" class="button action-stop-workers" disabled={!canStopWorkers} onclick={() => void store.stopWorkers()}>
          {messages.stopWorkers}
        </button>
      </div>

      {#if hasOperationInfo}
        <div class="infra-actions">
          <button type="button" class="button button-small" aria-expanded={store.technicalLog !== null} onclick={toggleLog}>
            {store.technicalLog === null ? messages.showTechnical : messages.hideTechnical}
          </button>
        </div>
        {#if store.technicalLog !== null}
          {#if store.technicalLog.length > 0}
            <pre class="technical-log operation-log" aria-label={messages.technicalLabel}>{store.technicalLog.join("\n")}</pre>
          {:else}
            <p class="infra-muted">{messages.noTechnical}</p>
          {/if}
        {/if}
      {/if}
    </section>

    <FirstRunList {store} steps={snap.firstRun} {readOnly} />
  {:else}
    <p role="status" aria-busy="true">{messages.checking}</p>
  {/if}
</section>
