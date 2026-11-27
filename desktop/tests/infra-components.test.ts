// Componentes da infraestrutura (tarefas 10.1 a 10.6): monta os componentes Svelte em jsdom com o
// módulo `api` simulado. Nenhum IPC, Docker, worker ou download reais.

import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getStartupState: vi.fn(),
  openFolder: vi.fn(),
  listDir: vi.fn(),
  readMarkdown: vi.fn(),
  readImage: vi.fn(),
  resolveLink: vi.fn(),
  saveSettings: vi.fn(),
  resetSettings: vi.fn(),
  getInfraSnapshot: vi.fn(),
  refreshInfra: vi.fn(),
  startStack: vi.fn(),
  stopStack: vi.fn(),
  startWorkers: vi.fn(),
  stopWorkers: vi.fn(),
  resolvePreviousPool: vi.fn(),
  prepareImages: vi.fn(),
  prepareModels: vi.fn(),
  getStackSettings: vi.fn(),
  saveStackSettings: vi.fn(),
  pickRepoFolder: vi.fn(),
  pickDataDir: vi.fn(),
  saveGuideProgress: vi.fn(),
  getOperationLog: vi.fn(),
  setGuideOpen: vi.fn(),
  getMacosVersion: vi.fn(),
}));
vi.mock("../src/lib/api", () => api);

import App from "../src/App.svelte";
import FirstRunList from "../src/components/FirstRunList.svelte";
import InfraPanel from "../src/components/InfraPanel.svelte";
import InstallGuide from "../src/components/InstallGuide.svelte";
import SettingsView from "../src/components/SettingsView.svelte";
import Sidebar from "../src/components/Sidebar.svelte";
import StackSettings from "../src/components/StackSettings.svelte";
import { InfraStore, infra } from "../src/lib/infra.svelte";
import { DEFAULT_SETTINGS, settings } from "../src/lib/settings.svelte";
import type { FirstRunStep, InfraSnapshot, OperationResult, StackSettings as StackSettingsData } from "../src/lib/types";

const tick = async (n = 6): Promise<void> => {
  for (let i = 0; i < n; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
    flushSync();
  }
};

let target: HTMLElement;
let mounted: Array<Record<string, unknown>> = [];

function render<P extends Record<string, unknown>>(component: any, props: P): HTMLElement {
  const instance = mount(component, { target, props });
  mounted.push(instance as Record<string, unknown>);
  flushSync();
  return target;
}

const text = (): string => target.textContent ?? "";
const button = (label: string): HTMLButtonElement => {
  const found = Array.from(target.querySelectorAll<HTMLButtonElement>("button")).find((b) => b.textContent!.trim() === label);
  if (!found) throw new Error(`botão não encontrado: ${label}`);
  return found;
};
const hasButton = (label: string): boolean =>
  Array.from(target.querySelectorAll("button")).some((b) => b.textContent!.trim() === label);

function step(over: Partial<FirstRunStep> = {}): FirstRunStep {
  return {
    step: "images",
    state: "pending",
    downloadedBytes: null,
    totalBytes: null,
    itemsDone: 0,
    itemsTotal: 3,
    updatedAt: null,
    failure: null,
    approxSize: "cerca de 1,5 GB",
    location: "disco do runtime Docker",
    ...over,
  };
}

function snap(over: Partial<InfraSnapshot> = {}): InfraSnapshot {
  return {
    readOnly: false,
    runtime: {
      runtimes: [
        { kind: "dockerDesktop", state: "running", context: "desktop-linux" },
        { kind: "orbstack", state: "notInstalled", context: null },
        { kind: "colima", state: "notInstalled", context: null },
      ],
      inUse: "dockerDesktop",
      overall: "ready",
      dockerPath: "/usr/local/bin/docker",
      checkedAt: "2026-11-27T12:00:00Z",
    },
    repo: { status: "ok", uvPath: "/opt/homebrew/bin/uv", missing: [] },
    dataDir: { path: "/Users/x/artigo2md-dados", status: "ok", canonicalPath: "/Users/x/artigo2md-dados" },
    services: [
      { service: "postgres", state: "stopped", address: "127.0.0.1:5432", checkedAt: "2026-11-27T12:00:00Z" },
      { service: "rabbitmq", state: "stopped", address: "127.0.0.1:5672", checkedAt: "2026-11-27T12:00:00Z" },
      { service: "api", state: "stopped", address: "127.0.0.1:8778", checkedAt: "2026-11-27T12:00:00Z" },
    ],
    pool: {
      state: "stopped",
      workers: 4,
      minFreeMemoryMb: 4822,
      exitCode: null,
      reason: null,
      lines: [],
      memoryWarning: false,
      adopted: false,
    },
    firstRun: [step(), step({ step: "models", itemsTotal: 2, approxSize: "cerca de 3 GB livres", location: "cache do Hugging Face" })],
    operations: [],
    summary: "stopped",
    consumerConnected: null,
    ...over,
  };
}

const result = (code: string, extra: Partial<OperationResult> = {}): OperationResult => ({
  ok: false,
  code: code as OperationResult["code"],
  service: null,
  port: null,
  failure: null,
  shortfallBytes: null,
  alreadyInState: null,
  ...extra,
});

function storeWith(snapshot: InfraSnapshot | null): InfraStore {
  const store = new InfraStore(api as never);
  store.snapshot = snapshot;
  return store;
}

const stackSettings = (over: Partial<StackSettingsData> = {}): StackSettingsData => ({
  schemaVersion: 1,
  repoPath: "/Users/x/artigo2md",
  workers: 4,
  minFreeMemoryMb: 4822,
  dataDir: "/Users/x/artigo2md-dados",
  guide: null,
  ...over,
});

beforeEach(() => {
  target = document.createElement("div");
  document.body.append(target);
  vi.stubGlobal("Worker", class {
    postMessage(): void {}
    terminate(): void {}
  });
  for (const fn of Object.values(api)) fn.mockReset();
  api.setGuideOpen.mockResolvedValue(undefined);
  api.saveGuideProgress.mockResolvedValue(stackSettings());
});

afterEach(() => {
  for (const instance of mounted) void unmount(instance);
  mounted = [];
  target.remove();
  vi.unstubAllGlobals();
  infra.stop();
  infra.snapshot = null;
  infra.readFailed = false;
  infra.notice = null;
});

// ---------------------------------------------------------------------------------------------
// 10.1 Sidebar
// ---------------------------------------------------------------------------------------------

describe("Sidebar: entrada Infraestrutura e indicador (10.1, FR-025, FR-042)", () => {
  const props = (over: Record<string, unknown> = {}) => ({
    view: "files",
    collapsed: false,
    width: 260,
    onSelect: vi.fn(),
    onToggle: vi.fn(),
    onResize: vi.fn(),
    summary: null,
    ...over,
  });

  it("a entrada fica entre Arquivos e Configurações e leva à tela de infraestrutura", () => {
    const p = props();
    render(Sidebar, p);
    const items = Array.from(target.querySelectorAll<HTMLButtonElement>(".nav-item"));
    expect(items).toHaveLength(3);
    expect(items[1]!.textContent).toContain("Infraestrutura");
    items[1]!.click();
    expect(p.onSelect).toHaveBeenCalledWith("infra");
  });

  it("os três estados têm texto visível, rótulo acessível e glifo próprio (nunca só cor)", () => {
    const seen = new Set<string>();
    for (const [summary, word] of [
      ["healthy", "saudável"],
      ["partial", "parcial"],
      ["stopped", "parada"],
      [null, "verificando"],
    ] as const) {
      target.innerHTML = "";
      const instance = mount(Sidebar, { target, props: props({ summary }) });
      flushSync();
      const item = target.querySelectorAll<HTMLButtonElement>(".nav-item")[1]!;
      expect(item.getAttribute("aria-label")).toBe(`Infraestrutura: ${word}`);
      expect(item.querySelector(".infra-badge-text")!.textContent).toBe(word);
      const glyph = item.querySelector(".infra-glyph")!;
      expect(glyph.getAttribute("aria-hidden")).toBe("true");
      seen.add(glyph.textContent!);
      void unmount(instance);
    }
    expect(seen.size).toBe(4);
  });

  it("a entrada marca aria-current quando é a tela atual", () => {
    render(Sidebar, props({ view: "infra" }));
    const items = Array.from(target.querySelectorAll<HTMLButtonElement>(".nav-item"));
    expect(items[1]!.getAttribute("aria-current")).toBe("page");
    expect(items[0]!.hasAttribute("aria-current")).toBe(false);
  });

  it("recolhida, o indicador continua visível, rotulado e operável pelo teclado", () => {
    const p = props({ collapsed: true, summary: "partial" });
    render(Sidebar, p);
    const dot = target.querySelector<HTMLButtonElement>(".infra-collapsed")!;
    expect(dot.tagName).toBe("BUTTON");
    expect(dot.getAttribute("aria-label")).toBe("Estado da infraestrutura: parcial");
    expect(dot.tabIndex).toBeGreaterThanOrEqual(0);
    dot.click();
    expect(p.onSelect).toHaveBeenCalledWith("infra");
  });

  it("todos os controles da barra são botões nativos, sem tabindex negativo (foco por teclado)", () => {
    render(Sidebar, props());
    for (const control of target.querySelectorAll<HTMLElement>(".nav-item, .sidebar-toggle")) {
      expect(control.tagName).toBe("BUTTON");
      expect(control.tabIndex).toBeGreaterThanOrEqual(0);
    }
  });
});

// ---------------------------------------------------------------------------------------------
// 10.2 InfraPanel
// ---------------------------------------------------------------------------------------------

describe("InfraPanel (10.2, FR-025 a FR-029)", () => {
  const mountPanel = (snapshot: InfraSnapshot | null, over: Record<string, unknown> = {}) => {
    const store = storeWith(snapshot);
    const props = { store, onOpenGuide: vi.fn(), onOpenSettings: vi.fn(), ...over };
    render(InfraPanel, props);
    return { store, props };
  };

  it("mostra runtime, três serviços e pool, com estado em português e endereço como texto", () => {
    mountPanel(snap());
    expect(text()).toContain("Docker pronto. Em uso: Docker Desktop.");
    const rows = Array.from(target.querySelectorAll<HTMLElement>(".service-row"));
    expect(rows.map((r) => r.dataset.service)).toEqual(["postgres", "rabbitmq", "api"]);
    expect(rows[0]!.getAttribute("aria-label")).toBe("Banco de dados (Postgres): parado");
    expect(rows[0]!.textContent).toContain("127.0.0.1:5432");
    expect(rows[0]!.textContent).toContain("verificado às");
    expect(text()).toContain("Workers de conversão");
    expect(target.querySelector(".pool-state")!.textContent!.trim()).toBe("parado");
    expect(target.querySelector("a[href]")).toBeNull();
  });

  it("parado não aparece como erro (US5-3)", () => {
    mountPanel(snap());
    expect(target.querySelector(".notice-error")).toBeNull();
    expect(text()).not.toContain("com problema");
    for (const pill of target.querySelectorAll<HTMLElement>(".service-row .state-pill")) expect(pill.dataset.state).toBe("stopped");
  });

  it("serviços iniciando e com problema usam os rótulos de FR-026", () => {
    mountPanel(
      snap({
        services: [
          { service: "postgres", state: "healthy", address: "127.0.0.1:5432", checkedAt: "2026-11-27T12:00:00Z" },
          { service: "rabbitmq", state: "starting", address: "127.0.0.1:5672", checkedAt: "2026-11-27T12:00:00Z" },
          { service: "api", state: "unhealthy", address: "127.0.0.1:8778", checkedAt: "2026-11-27T12:00:00Z" },
        ],
      }),
    );
    const pills = Array.from(target.querySelectorAll(".service-row .state-pill")).map((p) => p.textContent!.trim());
    expect(pills).toEqual(["saudável", "iniciando", "com problema"]);
  });

  it("não há credencial nem painel de gestão do RabbitMQ no DOM (SC-007, FR-029)", () => {
    mountPanel(snap({ services: snap().services.map((s) => ({ ...s, state: "healthy" as const })) }));
    const html = target.innerHTML.toLowerCase();
    for (const forbidden of ["password", "senha", "rabbitmq_default_pass", "postgres_password", "15672", "management"]) {
      expect(html, forbidden).not.toContain(forbidden);
    }
  });

  it("ações liberadas só com Docker pronto, repositório e pasta de dados ok", () => {
    mountPanel(snap());
    expect(button("Iniciar infraestrutura").disabled).toBe(false);
    expect(button("Parar infraestrutura").disabled).toBe(false);
    expect(button("Iniciar workers").disabled).toBe(false);
    expect(button("Parar workers").disabled).toBe(true);
  });

  it("sem runtime pronto, o subir fica bloqueado e o guia é oferecido (US1-1)", () => {
    const noRuntime = snap({
      runtime: {
        ...snap().runtime,
        runtimes: snap().runtime.runtimes.map((r) => ({ ...r, state: "notInstalled" as const, context: null })),
        inUse: null,
        overall: "noRuntime",
      },
    });
    const { props } = mountPanel(noRuntime);
    expect(text()).toContain("Nenhum runtime Docker foi encontrado neste Mac.");
    expect(button("Iniciar infraestrutura").disabled).toBe(true);
    expect(button("Parar infraestrutura").disabled).toBe(true);
    button("Abrir o guia de instalação").click();
    expect(props.onOpenGuide).toHaveBeenCalledWith(null);
  });

  it("runtime instalado e parado diz qual é e leva ao passo de iniciar (US1-2)", () => {
    const stopped = snap({
      runtime: {
        ...snap().runtime,
        runtimes: [
          { kind: "dockerDesktop", state: "notInstalled", context: null },
          { kind: "orbstack", state: "stopped", context: "orbstack" },
          { kind: "colima", state: "notInstalled", context: null },
        ],
        inUse: null,
        overall: "stopped",
      },
    });
    const { props } = mountPanel(stopped);
    expect(text()).toContain("OrbStack está instalado, mas não está em execução");
    button("Ver como iniciar").click();
    expect(props.onOpenGuide).toHaveBeenCalledWith({ runtime: "orbstack", step: "start" });
  });

  it("Compose ausente informa e não libera o subir (US1-4)", () => {
    mountPanel(snap({ runtime: { ...snap().runtime, overall: "composeMissing" } }));
    expect(text()).toContain("falta o Docker Compose");
    expect(button("Iniciar infraestrutura").disabled).toBe(true);
  });

  it("repositório inválido bloqueia as ações e orienta em português (US2-12)", () => {
    const { props } = mountPanel(snap({ repo: { status: "notSet", uvPath: null, missing: [] } }));
    expect(text()).toContain("Nenhuma pasta do repositório foi indicada. Indique-a em Configurações.");
    expect(button("Iniciar infraestrutura").disabled).toBe(true);
    expect(button("Iniciar workers").disabled).toBe(true);
    button("Configurações").click();
    expect(props.onOpenSettings).toHaveBeenCalled();
  });

  it("pasta de dados inválida bloqueia o subir e mostra o motivo", () => {
    mountPanel(snap({ dataDir: { path: "/dados", status: "notWritable", canonicalPath: null } }));
    expect(text()).toContain("Sem permissão de escrita na pasta de dados");
    expect(button("Iniciar infraestrutura").disabled).toBe(true);
  });

  it("com operação em andamento mostra a etapa e bloqueia as quatro ações (FR-015)", () => {
    mountPanel(snap({ operations: [{ kind: "startStack", stage: "runningUp", startedAt: "x", result: null }] }));
    expect(text()).toContain("Iniciando a infraestrutura");
    expect(text()).toContain("Iniciando os serviços e aguardando ficarem saudáveis…");
    for (const label of ["Iniciar infraestrutura", "Parar infraestrutura", "Iniciar workers", "Parar workers"]) {
      expect(button(label).disabled, label).toBe(true);
    }
  });

  it("segunda instância: só leitura, ações desativadas e aviso (US edge)", () => {
    mountPanel(snap({ readOnly: true }));
    expect(text()).toContain("Outra janela do artigo2md controla a infraestrutura");
    for (const label of ["Iniciar infraestrutura", "Parar infraestrutura", "Iniciar workers"]) {
      expect(button(label).disabled, label).toBe(true);
    }
  });

  it("iniciar chama o comando e mostra o resultado em português", async () => {
    api.getInfraSnapshot.mockResolvedValue(snap());
    api.startStack.mockResolvedValue({ alreadyInState: true });
    mountPanel(snap());
    button("Iniciar infraestrutura").click();
    await tick();
    expect(api.startStack).toHaveBeenCalledTimes(1);
    expect(text()).toContain("A infraestrutura já estava no ar.");
  });

  it("recusa traduzida: porta em conflito cita serviço e porta", async () => {
    api.startStack.mockRejectedValue({ code: "portInUse", detail: null });
    api.getInfraSnapshot.mockResolvedValue(
      snap({ operations: [{ kind: "startStack", stage: "runningUp", startedAt: "x", result: result("portInUse", { service: "postgres", port: 5432 }) }] }),
    );
    mountPanel(snap());
    button("Iniciar infraestrutura").click();
    await tick();
    expect(target.querySelector(".notice-error")!.textContent).toContain("A porta 5432 do Postgres está em uso por outro programa.");
  });

  it("pool parado de forma inesperada mostra o motivo traduzido e oferece Reiniciar (US3-8)", () => {
    mountPanel(snap({ pool: { ...snap().pool, state: "crashed", exitCode: 4, reason: "credentials" } }));
    expect(target.querySelector(".pool-state")!.textContent!.trim()).toBe("parado de forma inesperada");
    expect(target.querySelector(".pool-reason")!.textContent).toContain("O RabbitMQ recusou as credenciais dos workers");
    expect(button("Reiniciar workers").disabled).toBe(false);
  });

  it("aviso de pool acima da RAM, mensagens do pool sob demanda e aviso do consumidor (US3-4, US3-9, FR-028)", () => {
    mountPanel(snap({ pool: { ...snap().pool, state: "running", memoryWarning: true, lines: ["worker 1 pronto"] }, consumerConnected: false }));
    expect(text()).toContain("O número de workers é maior do que a memória deste Mac comporta");
    expect(target.querySelector(".consumer-warning")!.textContent).toContain("Consumidor de eventos parado");
    expect(target.querySelector(".worker-lines")).toBeNull();
    button("Ver mensagens dos workers").click();
    flushSync();
    expect(target.querySelector(".worker-lines")!.textContent).toBe("worker 1 pronto");
    expect(button("Parar workers").disabled).toBe(false);
  });

  it("'Encerrando workers…' vem do estado stopping do retrato (US3-10)", () => {
    mountPanel(snap({ pool: { ...snap().pool, state: "stopping" } }));
    expect(target.querySelector(".pool-closing")!.textContent).toBe("Encerrando workers…");
  });

  it("pool de sessão anterior: oferece Parar ou Continuar usando (US3-7)", async () => {
    api.resolvePreviousPool.mockResolvedValue({});
    const orphan = snap({ pool: { ...snap().pool, state: "orphanFound" } });
    api.getInfraSnapshot.mockResolvedValue(orphan);
    mountPanel(orphan);
    expect(target.querySelector(".orphan-offer")).not.toBeNull();
    button("Parar").click();
    await tick();
    expect(api.resolvePreviousPool).toHaveBeenCalledWith("stop");
    button("Continuar usando").click();
    await tick();
    expect(api.resolvePreviousPool).toHaveBeenLastCalledWith("keep");
  });

  it("detalhes técnicos só sob demanda, depois de uma falha (FR-014)", async () => {
    api.getOperationLog.mockResolvedValue({ lines: ["linha técnica mascarada"] });
    mountPanel(snap({ operations: [{ kind: "startStack", stage: "runningUp", startedAt: "x", result: result("scriptFailed") }] }));
    expect(target.querySelector(".operation-log")).toBeNull();
    button("Ver detalhes técnicos").click();
    await tick();
    expect(target.querySelector(".operation-log")!.textContent).toBe("linha técnica mascarada");
    button("Ocultar detalhes técnicos").click();
    flushSync();
    expect(target.querySelector(".operation-log")).toBeNull();
  });

  it("erro de leitura mostra aviso e o estado anterior continua visível", () => {
    const store = storeWith(snap());
    store.readFailed = true;
    render(InfraPanel, { store, onOpenGuide: vi.fn(), onOpenSettings: vi.fn() });
    expect(text()).toContain("Não foi possível ler o estado da infraestrutura agora");
    expect(target.querySelector(".service-row")).not.toBeNull();
  });

  it("antes da primeira leitura mostra 'Verificando…' sem travar", () => {
    mountPanel(null);
    expect(text()).toContain("Verificando a infraestrutura…");
  });

  it("regiões de aviso usam role=status/alert e o indicador tem texto (FR-042 d)", () => {
    mountPanel(snap({ summary: "partial" }));
    expect(target.querySelector(".state-summary")!.getAttribute("role")).toBe("status");
    expect(target.querySelector(".state-summary")!.textContent).toBe("Infraestrutura: parcial");
  });
});

// ---------------------------------------------------------------------------------------------
// 10.3 FirstRunList
// ---------------------------------------------------------------------------------------------

describe("FirstRunList (10.3, FR-030 a FR-036)", () => {
  const mountList = (steps: FirstRunStep[], over: { store?: InfraStore; readOnly?: boolean } = {}) => {
    const store = over.store ?? storeWith(snap({ firstRun: steps }));
    render(FirstRunList, { store, steps, readOnly: over.readOnly ?? false });
    return store;
  };
  const modelsStep = (over: Partial<FirstRunStep> = {}) =>
    step({ step: "models", itemsTotal: 2, approxSize: "cerca de 3 GB livres", location: "cache do Hugging Face", ...over });

  it("lista as duas etapas com estado, tamanho com fonte e local", () => {
    mountList([step(), modelsStep()]);
    const items = Array.from(target.querySelectorAll<HTMLElement>(".first-run-step"));
    expect(items.map((i) => i.dataset.step)).toEqual(["images", "models"]);
    expect(items[0]!.textContent).toContain("Imagens da infraestrutura");
    expect(items[0]!.textContent).toContain("pendente");
    expect(items[0]!.textContent).toContain("cerca de 1,5 GB");
    expect(items[1]!.textContent).toContain("Modelos de conversão");
    expect(items[1]!.textContent).toContain("cerca de 3 GB livres");
    expect(items[1]!.textContent).toContain("cache do Hugging Face");
    expect(text()).toContain("A internet só é usada nesta etapa.");
  });

  it("sem confirmação nada é disparado; só 'Baixar agora' dispara (FR-031)", async () => {
    api.prepareModels.mockResolvedValue({});
    api.getInfraSnapshot.mockResolvedValue(snap());
    mountList([step({ state: "done" }), modelsStep()]);
    button("Preparar modelos").click();
    flushSync();
    expect(api.prepareModels).not.toHaveBeenCalled();
    expect(text()).toContain("é preciso estar conectado à internet, só nesta etapa");
    expect(text()).toContain("cerca de 3 GB livres");
    button("Cancelar").click();
    flushSync();
    expect(api.prepareModels).not.toHaveBeenCalled();
    button("Preparar modelos").click();
    flushSync();
    button("Baixar agora").click();
    await tick();
    expect(api.prepareModels).toHaveBeenCalledWith(true);
    expect(api.prepareImages).not.toHaveBeenCalled();
  });

  it("imagens: o mesmo pedido de confirmação antes de baixar", async () => {
    api.prepareImages.mockResolvedValue({});
    api.getInfraSnapshot.mockResolvedValue(snap());
    mountList([step(), modelsStep()]);
    button("Preparar imagens").click();
    flushSync();
    expect(api.prepareImages).not.toHaveBeenCalled();
    button("Baixar agora").click();
    await tick();
    expect(api.prepareImages).toHaveBeenCalledWith(true);
  });

  it("tamanho total conhecido: barra com percentual", () => {
    mountList([step({ state: "downloading", downloadedBytes: 500_000_000, totalBytes: 1_000_000_000, itemsDone: 1 }), modelsStep()]);
    const bar = target.querySelector<HTMLProgressElement>("progress.download")!;
    expect(bar.value).toBe(50);
    expect(target.querySelector(".download-progress")!.textContent).toContain("50% concluído");
    expect(hasButton("Preparar imagens")).toBe(false);
  });

  it("tamanho desconhecido: volume baixado, itens e hora; sem barra de percentual (FR-032)", () => {
    mountList([step({ state: "done" }), modelsStep({ state: "downloading", downloadedBytes: 1_200_000_000, itemsDone: 1, updatedAt: "2026-11-27T12:00:05Z" })]);
    expect(target.querySelector("progress")).toBeNull();
    const line = target.querySelector(".download-progress")!.textContent!;
    expect(line).toContain("1,2 GB baixados");
    expect(line).toContain("1 de 2 modelos");
    expect(line).toContain("última atualização às");
  });

  it("sem mudança por mais de 5 s mostra 'sem resposta' em vez de valor parado (FR-032)", async () => {
    let now = 0;
    const client = { ...api, getInfraSnapshot: vi.fn() };
    const store = new InfraStore(client as never, () => now);
    const downloading = [step({ state: "done" }), modelsStep({ state: "downloading", downloadedBytes: 10, updatedAt: "2026-11-27T12:00:00Z" })];
    client.getInfraSnapshot.mockResolvedValue(snap({ firstRun: downloading }));
    await store.readOnce();
    render(FirstRunList, { store, steps: downloading, readOnly: false });
    expect(text()).not.toContain("Sem resposta");
    now += 6000;
    await store.readOnce();
    store.snapshot = snap({ firstRun: [...downloading] });
    flushSync();
    unmount(mounted.pop()!);
    target.innerHTML = "";
    render(FirstRunList, { store, steps: downloading, readOnly: false });
    expect(text()).toContain("Sem resposta: nada mudou nos últimos segundos.");
  });

  it("falha mostra o motivo e oferece 'Tentar de novo'; interrompido oferece 'Retomar' (FR-034)", () => {
    mountList([step({ state: "failed", failure: "noNetwork" }), modelsStep({ state: "failed", failure: "interrupted" })]);
    const items = Array.from(target.querySelectorAll<HTMLElement>(".first-run-step"));
    expect(items[0]!.textContent).toContain("falhou");
    expect(items[0]!.textContent).toContain("Sem conexão com a internet");
    expect(items[0]!.querySelector("button")!.textContent!.trim()).toBe("Tentar de novo");
    expect(items[1]!.querySelector("button")!.textContent!.trim()).toBe("Retomar");
  });

  it("etapas concluídas: nenhum botão e aviso de que nada será pedido (US6-7)", () => {
    mountList([step({ state: "done" }), modelsStep({ state: "done" })]);
    expect(target.querySelector("button")).toBeNull();
    expect(text()).toContain("Nenhum download será pedido.");
  });

  it("modelsMissing destaca a etapa de modelos (US3-2)", () => {
    const store = storeWith(snap());
    store.attention = "models";
    mountList([step({ state: "done" }), modelsStep()], { store });
    expect(target.querySelector('[data-step="models"]')!.classList.contains("step-attention")).toBe(true);
    expect(target.querySelector('[data-step="images"]')!.classList.contains("step-attention")).toBe(false);
  });

  it("somente leitura ou ação em andamento desativam os botões", () => {
    mountList([step(), modelsStep()], { readOnly: true });
    expect(button("Preparar imagens").disabled).toBe(true);
    expect(button("Preparar modelos").disabled).toBe(true);
  });
});

// ---------------------------------------------------------------------------------------------
// 10.4 InstallGuide
// ---------------------------------------------------------------------------------------------

describe("InstallGuide (10.4, FR-004 a FR-006)", () => {
  const mountGuide = (over: Record<string, unknown> = {}, snapshot: InfraSnapshot | null = null) => {
    const store = storeWith(snapshot);
    const props = { store, saved: null, onDone: vi.fn(), ...over };
    render(InstallGuide, props);
    return { store, props };
  };
  const noRuntime = snap({ runtime: { ...snap().runtime, overall: "noRuntime", inUse: null } });

  it("começa na comparação dos três runtimes e deixa escolher um (US4-1)", () => {
    mountGuide();
    const cards = Array.from(target.querySelectorAll<HTMLElement>(".guide-card"));
    expect(cards.map((c) => c.dataset.runtime)).toEqual(["dockerDesktop", "orbstack", "colima"]);
    expect(text()).toContain("Grátis para uso pessoal");
    expect(text()).toContain("Licença MIT, gratuito.");
    expect(text()).toContain("o app não abre o navegador, não baixa e não executa nenhum instalador");
  });

  it("mostra 'Passo i de n', endereços como texto copiável e a fonte oficial (US4-2, US4-3)", () => {
    mountGuide();
    button("Escolher").click();
    flushSync();
    expect(target.querySelector(".guide-step-counter")!.textContent).toBe("Passo 1 de 3");
    const codes = Array.from(target.querySelectorAll(".guide-copy code")).map((c) => c.textContent);
    expect(codes).toEqual([
      "https://desktop.docker.com/mac/main/arm64/Docker.dmg",
      "https://desktop.docker.com/mac/main/amd64/Docker.dmg",
    ]);
    expect(target.querySelector(".guide-source")!.textContent).toContain("https://docs.docker.com/desktop/setup/install/mac-install/");
    button("Próximo passo").click();
    flushSync();
    expect(target.querySelector(".guide-step-counter")!.textContent).toBe("Passo 2 de 3");
    button("Próximo passo").click();
    flushSync();
    expect(target.querySelector(".guide-step-counter")!.textContent).toBe("Passo 3 de 3");
    expect(hasButton("Próximo passo")).toBe(false);
    expect(hasButton("Concluir")).toBe(true);
  });

  it("nenhuma ação abre navegador, baixa ou executa; funciona sem rede (US4-4, FR-005)", async () => {
    const open = vi.fn();
    const fetchSpy = vi.fn();
    vi.stubGlobal("open", open);
    vi.stubGlobal("fetch", fetchSpy);
    vi.stubGlobal("XMLHttpRequest", fetchSpy);
    vi.stubGlobal("WebSocket", fetchSpy);
    mountGuide();
    for (const label of ["Escolher"]) button(label).click();
    for (let i = 0; i < 2; i += 1) {
      flushSync();
      if (hasButton("Próximo passo")) button("Próximo passo").click();
    }
    flushSync();
    expect(target.querySelector("a")).toBeNull();
    expect(target.querySelector("[href]")).toBeNull();
    expect(target.querySelector("[target]")).toBeNull();
    expect(open).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("copiar usa a área de transferência e avisa se não for possível", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    mountGuide({ saved: { runtime: "colima", step: 0 } });
    button("Copiar").click();
    await tick();
    expect(writeText).toHaveBeenCalledWith("brew install colima");
    expect(button("Copiado")).toBeTruthy();
    writeText.mockRejectedValue(new Error("negado"));
    button("Copiado").click();
    await tick();
    expect(text()).toContain("Não foi possível copiar. Selecione o texto e copie manualmente.");
  });

  it("persiste o passo a cada mudança e reabre onde parou (US4-5, FR-006)", () => {
    const { props } = mountGuide();
    void props;
    button("Escolher").click();
    flushSync();
    expect(api.saveGuideProgress).toHaveBeenLastCalledWith({ runtime: "dockerDesktop", step: 0 });
    button("Próximo passo").click();
    flushSync();
    expect(api.saveGuideProgress).toHaveBeenLastCalledWith({ runtime: "dockerDesktop", step: 1 });
    button("Voltar").click();
    flushSync();
    expect(api.saveGuideProgress).toHaveBeenLastCalledWith({ runtime: "dockerDesktop", step: 0 });
    button("Voltar").click();
    flushSync();
    expect(api.saveGuideProgress).toHaveBeenLastCalledWith({ runtime: null, step: 0 });
    expect(target.querySelector(".guide-compare")).not.toBeNull();
  });

  it("retoma o runtime e o passo salvos", () => {
    mountGuide({ saved: { runtime: "colima", step: 2 } });
    expect(target.querySelector(".guide-step-counter")!.textContent).toBe("Passo 3 de 4");
    expect(text()).toContain("brew install docker-compose");
    expect(text()).toContain("instrução que o próprio Homebrew mostra");
  });

  it("abertura direcionada vai ao passo de iniciar do runtime parado (US4-7)", () => {
    mountGuide({ target: { runtime: "orbstack", step: "start" } });
    expect(target.querySelector(".guide-step-counter")!.textContent).toBe("Passo 2 de 2");
    expect(text()).toContain("Abra o OrbStack");
    expect(text()).toContain("confere sozinho, a cada poucos segundos");
  });

  it("avisa o núcleo que abriu e que fechou (diagnóstico a cada 5 s)", () => {
    mountGuide();
    expect(api.setGuideOpen).toHaveBeenCalledWith(true);
    unmount(mounted.pop()!);
    expect(api.setGuideOpen).toHaveBeenLastCalledWith(false);
  });

  it("encerra sozinho ao detectar 'Docker pronto' e limpa o passo salvo (US4-6)", () => {
    const { props } = mountGuide({ saved: { runtime: "colima", step: 3 } }, snap());
    expect(props.onDone).toHaveBeenCalledTimes(1);
    expect(api.saveGuideProgress).toHaveBeenLastCalledWith(null);
    expect(text()).toContain("Docker pronto. O guia terminou.");
  });

  it("sem Docker pronto o guia segue aberto", () => {
    const { props } = mountGuide({ saved: { runtime: "colima", step: 3 } }, noRuntime);
    expect(props.onDone).not.toHaveBeenCalled();
    expect(target.querySelector(".guide-step-counter")).not.toBeNull();
  });

  it("runtime com macOS insuficiente fica indisponível, com o motivo (10.4.3)", () => {
    mountGuide({ macosVersion: "13.2" });
    const orb = target.querySelector<HTMLElement>('[data-runtime="orbstack"]')!;
    expect(orb.querySelector<HTMLButtonElement>("button")!.disabled).toBe(true);
    expect(orb.textContent).toContain("Indisponível neste Mac: OrbStack exige macOS 14 ou mais novo.");
    expect(target.querySelector<HTMLButtonElement>('[data-runtime="colima"] button')!.disabled).toBe(false);
    expect(target.querySelector<HTMLButtonElement>('[data-runtime="dockerDesktop"] button')!.disabled).toBe(false);
    expect(text()).toContain("duas anteriores");
  });

  it("fechar na comparação não deixa o guia para reabrir sozinho", () => {
    const { props } = mountGuide();
    button("Fechar o guia").click();
    expect(api.saveGuideProgress).toHaveBeenLastCalledWith(null);
    expect(props.onDone).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------------------------
// 10.5 StackSettings
// ---------------------------------------------------------------------------------------------

describe("StackSettings (10.5, FR-013, FR-016, FR-019)", () => {
  const withPorts = { settings: stackSettings(), ports: { postgres: 5432, rabbitmq: 5672, api: 8778 } };
  const mountSettings = (response: unknown = withPorts, snapshot: InfraSnapshot | null = snap()) => {
    api.getStackSettings.mockResolvedValue(response);
    const store = storeWith(snapshot);
    render(StackSettings, { store });
    return store;
  };
  const input = (id: string): HTMLInputElement => target.querySelector<HTMLInputElement>(`#${id}`)!;
  const type = (el: HTMLInputElement, value: string): void => {
    el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    flushSync();
  };

  it("mostra pasta do repositório e de dados, workers, memória e portas, com os valores salvos", async () => {
    mountSettings();
    await tick();
    expect(target.querySelector(".repo-path")!.textContent).toBe("/Users/x/artigo2md");
    expect(target.querySelector(".data-path")!.textContent).toBe("/Users/x/artigo2md-dados");
    expect(input("stack-workers").value).toBe("4");
    expect(input("stack-minfree").value).toBe("4822");
    expect(input("stack-port-postgres").value).toBe("5432");
    expect(input("stack-port-rabbitmq").value).toBe("5672");
    expect(input("stack-port-api").value).toBe("8778");
    expect(text()).toContain("A pasta do repositório foi verificada e está pronta.");
  });

  it("não há campo de porta do painel de gestão nem campo de caminho editável (M1, FR-029)", async () => {
    mountSettings();
    await tick();
    const inputs = Array.from(target.querySelectorAll("input"));
    expect(inputs.map((i) => i.id).sort()).toEqual(
      ["stack-minfree", "stack-port-api", "stack-port-postgres", "stack-port-rabbitmq", "stack-workers"].sort(),
    );
    expect(target.innerHTML.toLowerCase()).not.toContain("management");
    expect(target.innerHTML).not.toContain("15672");
  });

  it("valores inválidos são recusados em português e nada é gravado", async () => {
    mountSettings();
    await tick();
    type(input("stack-workers"), "0");
    type(input("stack-minfree"), "-1");
    type(input("stack-port-postgres"), "70000");
    type(input("stack-port-api"), "abc");
    button("Salvar").click();
    await tick();
    expect(api.saveStackSettings).not.toHaveBeenCalled();
    expect(text()).toContain("O número de workers deve ser um número inteiro maior ou igual a 1.");
    expect(text()).toContain("A memória livre mínima deve ser um número inteiro maior ou igual a 0.");
    expect(text()).toContain("Porta do banco de dados (Postgres) deve ser um número inteiro entre 1 e 65535.");
    expect(text()).toContain("Porta da API deve ser um número inteiro entre 1 e 65535.");
    expect(input("stack-workers").getAttribute("aria-invalid")).toBe("true");
  });

  it("valores válidos gravam com repoPath e dataDir exatamente como salvos", async () => {
    api.saveStackSettings.mockImplementation(async (s: StackSettingsData, ports: unknown) => ({ settings: s, ports }));
    mountSettings();
    await tick();
    type(input("stack-workers"), "2");
    type(input("stack-minfree"), "0");
    type(input("stack-port-postgres"), "5433");
    button("Salvar").click();
    await tick();
    expect(api.saveStackSettings).toHaveBeenCalledTimes(1);
    const [sent, ports] = api.saveStackSettings.mock.calls[0]!;
    expect(sent).toEqual(stackSettings({ workers: 2, minFreeMemoryMb: 0 }));
    expect(sent.repoPath).toBe("/Users/x/artigo2md");
    expect(sent.dataDir).toBe("/Users/x/artigo2md-dados");
    expect(ports).toEqual({ postgres: 5433, rabbitmq: 5672, api: 8778 });
    expect(text()).toContain("Configurações da infraestrutura salvas.");
  });

  it("sem .env as portas padrão aparecem só para leitura e nada de porta é enviado", async () => {
    api.saveStackSettings.mockImplementation(async (s: StackSettingsData, ports: unknown) => ({ settings: s, ports }));
    mountSettings({ settings: stackSettings(), ports: null });
    await tick();
    expect(input("stack-port-postgres").value).toBe("5432");
    expect(input("stack-port-postgres").disabled).toBe(true);
    expect(text()).toContain("O arquivo de parâmetros ainda não existe");
    button("Salvar").click();
    await tick();
    expect(api.saveStackSettings.mock.calls[0]![1]).toBeNull();
  });

  it("pastas só mudam pelos seletores nativos, que o Rust abre (M1)", async () => {
    api.pickRepoFolder.mockResolvedValue(stackSettings({ repoPath: "/outra" }));
    api.pickDataDir.mockResolvedValue(stackSettings());
    api.refreshInfra.mockResolvedValue(snap());
    mountSettings();
    await tick();
    button("Trocar pasta…").click();
    await tick();
    expect(api.pickRepoFolder).toHaveBeenCalledWith();
    expect(api.pickRepoFolder.mock.calls[0]).toHaveLength(0);
    expect(api.pickDataDir).not.toHaveBeenCalled();
    const [, dataButton] = Array.from(target.querySelectorAll<HTMLButtonElement>("button")).filter((b) => b.textContent!.trim() === "Trocar pasta…");
    dataButton!.click();
    await tick();
    expect(api.pickDataDir).toHaveBeenCalledWith();
  });

  it(".env inválido (invalidParams): a tela abre, mostra o erro e deixa tentar de novo ou escolher a pasta (nota a)", async () => {
    api.getStackSettings.mockRejectedValue({ code: "invalidParams", detail: null });
    render(StackSettings, { store: storeWith(snap()) });
    await tick();
    expect(target.querySelector(".stack-load-error")!.textContent).toContain("stack/.env");
    expect(hasButton("Tentar de novo")).toBe(true);
    expect(target.querySelector(".pick-repo")).not.toBeNull();
    api.getStackSettings.mockResolvedValue(withPorts);
    button("Tentar de novo").click();
    await tick();
    expect(target.querySelector(".stack-load-error")).toBeNull();
    expect(input("stack-workers").value).toBe("4");
  });

  it("recusa do núcleo vira mensagem em português; operação em andamento bloqueia a edição", async () => {
    api.saveStackSettings.mockRejectedValue({ code: "operationInProgress", detail: null });
    mountSettings();
    await tick();
    button("Salvar").click();
    await tick();
    expect(text()).toContain("Outra operação da infraestrutura já está em andamento.");
    unmount(mounted.pop()!);
    target.innerHTML = "";
    mountSettings(withPorts, snap({ operations: [{ kind: "startStack", stage: "runningUp", startedAt: "x", result: null }] }));
    await tick();
    expect(button("Salvar").disabled).toBe(true);
    expect(text()).toContain("Há uma operação da infraestrutura em andamento.");
  });

  it("a seção aparece dentro de Configurações", async () => {
    api.getStackSettings.mockResolvedValue(withPorts);
    render(SettingsView, { store: settings });
    await tick();
    expect(target.querySelector(".stack-settings")).not.toBeNull();
    expect(target.querySelector("#stack-workers")).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
// 10.6 App
// ---------------------------------------------------------------------------------------------

describe("App: view 'infra' e integração (10.6, FR-003, FR-024)", () => {
  const startup = (over: Record<string, unknown> = {}) => ({
    settings: { ...DEFAULT_SETTINGS },
    warnings: [],
    folder: null,
    ...over,
  });
  const nav = (index: number): HTMLButtonElement => target.querySelectorAll<HTMLButtonElement>(".nav-item")[index]!;

  it("Infraestrutura abre o painel; Arquivos e Configurações seguem funcionando", async () => {
    api.getStartupState.mockResolvedValue(startup());
    api.getInfraSnapshot.mockResolvedValue(snap());
    api.getStackSettings.mockResolvedValue({ settings: stackSettings(), ports: null });
    render(App, {});
    await tick();
    nav(1).click();
    await tick();
    expect(target.querySelector(".infra-view")).not.toBeNull();
    nav(2).click();
    await tick();
    expect(target.querySelector(".infra-view")).toBeNull();
    expect(target.querySelector(".settings-view")).not.toBeNull();
    nav(0).click();
    await tick();
    expect(target.querySelector(".files-view")!.closest("[hidden]")).toBeNull();
  });

  it("sem Docker, e mesmo com o núcleo da infraestrutura falhando, Arquivos e Configurações abrem sem erro (US1-5)", async () => {
    api.getStartupState.mockResolvedValue(startup());
    api.getInfraSnapshot.mockRejectedValue({ code: "io", detail: null });
    api.getStackSettings.mockRejectedValue({ code: "invalidParams", detail: null });
    render(App, {});
    await tick();
    expect(target.querySelector(".files-view")).not.toBeNull();
    expect(target.querySelector(".banner")).toBeNull();
    nav(2).click();
    await tick();
    expect(target.querySelector(".settings-view")).not.toBeNull();
    expect(target.querySelector('input[name="appearance"]')).not.toBeNull();
    nav(1).click();
    await tick();
    expect(target.textContent).toContain("Não foi possível ler o estado da infraestrutura agora");
    expect(nav(1).getAttribute("aria-label")).toBe("Infraestrutura: parcial");
  });

  it("o indicador da barra vem do retrato e aparece em qualquer tela", async () => {
    api.getStartupState.mockResolvedValue(startup());
    api.getInfraSnapshot.mockResolvedValue(snap({ summary: "healthy" }));
    api.getStackSettings.mockRejectedValue({ code: "io" });
    render(App, {});
    await tick();
    expect(nav(1).getAttribute("aria-label")).toBe("Infraestrutura: saudável");
    nav(2).click();
    await tick();
    expect(nav(1).getAttribute("aria-label")).toBe("Infraestrutura: saudável");
  });

  it("guia deixado no meio reabre no passo salvo (US4-5)", async () => {
    api.getStartupState.mockResolvedValue(startup());
    api.getInfraSnapshot.mockResolvedValue(snap({ runtime: { ...snap().runtime, overall: "noRuntime", inUse: null } }));
    api.getStackSettings.mockResolvedValue({ settings: stackSettings({ guide: { runtime: "colima", step: 1 } }), ports: null });
    api.getMacosVersion.mockResolvedValue("14.5");
    render(App, {});
    await tick(10);
    expect(target.querySelector(".guide-view")).not.toBeNull();
    expect(target.querySelector(".guide-step-counter")!.textContent).toBe("Passo 2 de 4");
  });

  it("'Abrir o guia' no painel leva ao guia e 'Docker pronto' leva de volta ao painel (US4-6)", async () => {
    api.getStartupState.mockResolvedValue(startup());
    const missing = snap({ runtime: { ...snap().runtime, overall: "noRuntime", inUse: null } });
    api.getInfraSnapshot.mockResolvedValue(missing);
    api.getStackSettings.mockResolvedValue({ settings: stackSettings(), ports: null });
    api.getMacosVersion.mockResolvedValue("15.0");
    render(App, {});
    await tick();
    nav(1).click();
    await tick();
    button("Abrir o guia de instalação").click();
    await tick(10);
    expect(target.querySelector(".guide-view")).not.toBeNull();
    // O runtime ficou pronto: o guia se encerra e mostra o painel.
    infra.snapshot = snap();
    await tick();
    expect(target.querySelector(".guide-view")).toBeNull();
    expect(target.querySelector(".infra-view")).not.toBeNull();
  });

  it("durante o fechamento do app mostra 'Encerrando workers…' (US3-10, FR-024)", async () => {
    api.getStartupState.mockResolvedValue(startup());
    api.getInfraSnapshot.mockResolvedValue(snap({ pool: { ...snap().pool, state: "stopping" } }));
    api.getStackSettings.mockRejectedValue({ code: "io" });
    render(App, {});
    await tick();
    const overlay = target.querySelector(".closing-overlay");
    expect(overlay).not.toBeNull();
    expect(overlay!.textContent).toBe("Encerrando workers…");
    expect(overlay!.getAttribute("role")).toBe("status");
  });

  it("sem fechamento, não há tela de encerramento", async () => {
    api.getStartupState.mockResolvedValue(startup());
    api.getInfraSnapshot.mockResolvedValue(snap());
    api.getStackSettings.mockRejectedValue({ code: "io" });
    render(App, {});
    await tick();
    expect(target.querySelector(".closing-overlay")).toBeNull();
  });
});
