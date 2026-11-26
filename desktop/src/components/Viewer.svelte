<script lang="ts">
  import { untrack } from "svelte";
  import { readImage, readMarkdown, resolveLink } from "../lib/api";
  import { ImageLoader } from "../lib/images";
  import { decodeReference } from "../lib/refs";
  import { errorMessage, messages } from "../lib/messages";
  import { RenderCancelledError, RenderFailedError, RenderSession } from "../lib/render";
  import { sanitizeHtmlChecked, toFragment } from "../lib/sanitize";
  import type { AppError, MarkdownDocument } from "../lib/types";

  interface Props {
    /** Caminho relativo do `.md` selecionado, ou `null`. */
    path: string | null;
    /** Pede ao pai para abrir outro arquivo (link `.md` interno). */
    onNavigate: (relativePath: string) => void;
  }

  let { path, onNavigate }: Props = $props();

  type Phase = "none" | "loading" | "error" | "ready";

  let phase = $state<Phase>("none");
  let errorText = $state("");
  let largeFile = $state(false);
  let rendering = $state(false);
  let renderFallback = $state(false);
  /** Detalhe técnico da falha de renderização (tarefa 9.1.2); nunca contém caminho do disco. */
  let renderDetail = $state("");
  let linkNotice = $state("");
  let reloadTick = $state(0);
  let host = $state<HTMLElement | null>(null);

  const session = new RenderSession();
  let loader: ImageLoader | null = null;
  let generation = 0;

  function resetBody(): void {
    loader?.dispose();
    loader = null;
    host?.replaceChildren();
  }

  async function load(target: string | null): Promise<void> {
    const mine = (generation += 1);
    session.cancel();
    resetBody();
    linkNotice = "";
    largeFile = false;
    renderFallback = false;
    renderDetail = "";
    rendering = false;
    if (target === null) {
      phase = "none";
      return;
    }
    phase = "loading";
    let doc: MarkdownDocument;
    try {
      doc = await readMarkdown(target);
    } catch (error) {
      if (mine !== generation) return;
      errorText = errorMessage((error as AppError).code ?? "io", "document");
      phase = "error";
      return;
    }
    if (mine !== generation) return;
    // O aviso de arquivo grande aparece antes do resultado da renderização (FR-034).
    largeFile = doc.isLarge;
    rendering = true;
    phase = "ready";

    let html: string | null = null;
    let detail = "";
    try {
      const result = sanitizeHtmlChecked(await session.render(doc.content));
      if (result.ok) html = result.html;
      else detail = `sanitize: ${result.detail}`;
    } catch (error) {
      if (error instanceof RenderCancelledError || mine !== generation) return;
      detail = error instanceof RenderFailedError ? error.detail : `render: ${String(error)}`;
    }
    if (mine !== generation) return;
    rendering = false;
    if (host === null) return;
    if (html === null) {
      // Falha fechada: texto puro, nunca HTML sem sanitização.
      renderFallback = true;
      renderDetail = detail;
      console.error(`artigo2md: ${messages.renderError} (${detail})`);
      const pre = document.createElement("pre");
      pre.className = "viewer-plain";
      pre.textContent = doc.content;
      host.replaceChildren(pre);
      return;
    }
    host.replaceChildren(toFragment(html));
    loader = new ImageLoader({ documentPath: doc.relativePath, readImage });
    loader.attach(host);
  }

  $effect(() => {
    const target = path;
    void reloadTick;
    untrack(() => void load(target));
  });

  $effect(() => {
    return () => {
      generation += 1;
      session.cancel();
      loader?.dispose();
    };
  });

  async function openLink(element: Element): Promise<void> {
    const href = element.getAttribute("data-md-link");
    if (href === null || path === null) return;
    linkNotice = "";
    try {
      onNavigate(await resolveLink(path, decodeReference(href)));
    } catch (error) {
      linkNotice = errorMessage((error as AppError).code ?? "io", "link");
    }
  }

  function handleClick(event: MouseEvent): void {
    const element = (event.target as Element | null)?.closest("[data-md-link]");
    if (element) {
      event.preventDefault();
      void openLink(element);
    }
  }

  function handleKey(event: KeyboardEvent): void {
    if (event.key !== "Enter" && event.key !== " ") return;
    const element = (event.target as Element | null)?.closest("[data-md-link]");
    if (element) {
      event.preventDefault();
      void openLink(element);
    }
  }
</script>

<section class="viewer" aria-label={messages.viewerLabel}>
  {#if phase === "none"}
    <p class="viewer-empty">{messages.noFileSelected}</p>
  {/if}

  {#if phase !== "none"}
    <header class="viewer-toolbar">
      <span class="viewer-path">{path}</span>
      <button type="button" class="button" onclick={() => (reloadTick += 1)}>{messages.reload}</button>
    </header>
  {/if}

  {#if phase === "loading"}
    <p class="viewer-status" role="status">{messages.loadingDocument}</p>
  {:else if phase === "error"}
    <p class="notice notice-error" role="alert">{errorText}</p>
  {/if}

  {#if phase === "ready"}
    {#if largeFile}
      <p class="notice notice-warning" role="status">{messages.largeFile}</p>
    {/if}
    {#if rendering}
      <p class="viewer-status" role="status">{messages.loadingDocument}</p>
    {/if}
    {#if renderFallback}
      <p class="notice notice-warning" role="status">
        {messages.renderError}
        {#if renderDetail}<br /><small class="notice-detail">{messages.technicalDetail}: {renderDetail}</small>{/if}
      </p>
    {/if}
    {#if linkNotice}
      <p class="notice notice-error" role="alert">{linkNotice}</p>
    {/if}
  {/if}

  <!-- Corpo renderizado: preenchido por `load()` com HTML já sanitizado. -->
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions, a11y_no_noninteractive_element_interactions -->
  <article
    class="markdown-body"
    class:hidden={phase !== "ready"}
    bind:this={host}
    onclick={handleClick}
    onkeydown={handleKey}
  ></article>
</section>
