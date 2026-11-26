declare module "*.svelte" {
  import type { Component } from "svelte";
  const component: Component<any>;
  export default component;
}

/** `true` quando o Vite serve sem empacotar (`vite`/`tauri dev`); `false` no build (vite.config.ts). */
declare const __RENDER_WORKER_AS_MODULE__: boolean;
