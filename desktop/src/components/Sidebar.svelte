<script lang="ts">
  import { messages } from "../lib/messages";
  import { sidebarKeyWidth } from "../lib/tree-nav";
  import { SIDEBAR_WIDTH_MAX, SIDEBAR_WIDTH_MIN, type View } from "../lib/types";

  interface Props {
    view: View;
    collapsed: boolean;
    width: number;
    onSelect: (view: View) => void;
    onToggle: () => void;
    onResize: (width: number) => void;
  }

  let { view, collapsed, width, onSelect, onToggle, onResize }: Props = $props();

  let draft = $state<number | null>(null);
  let shownWidth = $derived(draft ?? width);

  function clamp(value: number): number {
    return Math.min(SIDEBAR_WIDTH_MAX, Math.max(SIDEBAR_WIDTH_MIN, Math.round(value)));
  }

  function startDrag(event: PointerEvent): void {
    const handle = event.currentTarget as HTMLElement;
    const aside = handle.parentElement!;
    const left = aside.getBoundingClientRect().left;
    handle.setPointerCapture(event.pointerId);
    const move = (e: PointerEvent): void => {
      draft = clamp(e.clientX - left);
    };
    const end = (): void => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      handle.removeEventListener("pointercancel", end);
      if (draft !== null) onResize(draft);
      draft = null;
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  }

  function handleKey(event: KeyboardEvent): void {
    const next = sidebarKeyWidth(event.key, width, SIDEBAR_WIDTH_MIN, SIDEBAR_WIDTH_MAX);
    if (next !== null) {
      event.preventDefault();
      onResize(next);
    }
  }
</script>

<aside
  class="sidebar"
  class:collapsed
  aria-label={messages.sidebarLabel}
  style:width={collapsed ? undefined : `${shownWidth}px`}
>
  <button
    type="button"
    class="sidebar-toggle"
    aria-label={collapsed ? messages.expandSidebar : messages.collapseSidebar}
    aria-expanded={!collapsed}
    title={collapsed ? messages.expandSidebar : messages.collapseSidebar}
    onclick={onToggle}
  >
    <span aria-hidden="true">{collapsed ? "»" : "«"}</span>
  </button>

  {#if !collapsed}
    <nav class="sidebar-nav">
      <button
        type="button"
        class="nav-item"
        class:active={view === "files"}
        aria-current={view === "files" ? "page" : undefined}
        onclick={() => onSelect("files")}
      >
        {messages.navFiles}
      </button>
      <button
        type="button"
        class="nav-item"
        class:active={view === "settings"}
        aria-current={view === "settings" ? "page" : undefined}
        onclick={() => onSelect("settings")}
      >
        {messages.navSettings}
      </button>
    </nav>

    <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
    <div
      class="sidebar-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label={messages.resizeSidebar}
      aria-valuemin={SIDEBAR_WIDTH_MIN}
      aria-valuemax={SIDEBAR_WIDTH_MAX}
      aria-valuenow={shownWidth}
      tabindex="0"
      onpointerdown={startDrag}
      onkeydown={handleKey}
    ></div>
  {/if}
</aside>
