<script lang="ts">
  import { appearanceLabels, fontSizeLabels, messages } from "../lib/messages";
  import type { SettingsStore } from "../lib/settings.svelte";
  import { APPEARANCES, FONT_SIZES, type Appearance, type FontSize } from "../lib/types";

  interface Props {
    store: SettingsStore;
  }

  let { store }: Props = $props();
</script>

<section class="settings-view" aria-labelledby="settings-title">
  <h1 id="settings-title">{messages.navSettings}</h1>

  {#if store.saveError}
    <p class="notice notice-error" role="alert">{store.saveError}</p>
  {/if}

  <fieldset class="setting-group">
    <legend>{messages.appearanceLabel}</legend>
    {#each APPEARANCES as value (value)}
      <label class="radio">
        <input
          type="radio"
          name="appearance"
          {value}
          checked={store.current.appearance === value}
          onchange={() => void store.update({ appearance: value as Appearance })}
        />
        <span>{appearanceLabels[value]}</span>
      </label>
    {/each}
  </fieldset>

  <fieldset class="setting-group">
    <legend>{messages.fontSizeLabel}</legend>
    {#each FONT_SIZES as value (value)}
      <label class="radio">
        <input
          type="radio"
          name="font-size"
          {value}
          checked={store.current.viewerFontSize === value}
          onchange={() => void store.update({ viewerFontSize: value as FontSize })}
        />
        <span>{fontSizeLabels[value]}</span>
      </label>
    {/each}
  </fieldset>

  <button type="button" class="button" onclick={() => void store.restoreDefaults()}>
    {messages.restoreDefaults}
  </button>
</section>
