// Tarefas 9.1.4, 9.2.4 e 9.3.1 a 9.3.3 (desktop-stack-control): textos em português para todo
// código e estado, sondagem do retrato e paridade Rust ↔ TypeScript. Nenhum IPC, Docker ou worker
// real: o cliente é simulado e a paridade lê o código-fonte do Rust e a fixture que ele mesmo gerou.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fixture from "./fixtures/infra-snapshot.json";
import {
  InfraStore,
  POLL_FAST_MS,
  POLL_IDLE_MS,
  STALL_MS,
  activeOperations,
  isBusy,
  pollIntervalMs,
  type InfraApi,
} from "../src/lib/infra.svelte";
import {
  dataDirStatusMessages,
  downloadFailureMessages,
  errorMessage,
  formatBytes,
  formatTime,
  messages,
  operationKindLabels,
  operationResultMessage,
  operationStageMessages,
  poolStateLabels,
  progressText,
  repoMessage,
  runtimeMessage,
  runtimeNames,
  runtimeStateLabels,
  serviceNames,
  serviceStateLabels,
  stepNames,
  stepStateLabels,
  summaryAriaLabel,
  summaryIndicatorText,
  summaryLabels,
  workerExitReasonMessages,
} from "../src/lib/messages";
import {
  CONTRACT_KEYS,
  DATA_DIR_STATUSES,
  DOWNLOAD_FAILURES,
  ERROR_CODES,
  OPERATION_KINDS,
  OPERATION_STAGES,
  REPO_STATUSES,
  RUNTIME_KINDS,
  RUNTIME_OVERALLS,
  RUNTIME_STATES,
  SERVICE_NAMES,
  SERVICE_STATES,
  STEP_KINDS,
  STEP_STATES,
  SUMMARIES,
  WORKER_EXIT_REASONS,
  WORKER_POOL_STATES,
  type FirstRunStep,
  type InfraSnapshot,
  type OperationResult,
  type RepoCheck,
  type RuntimeReport,
} from "../src/lib/types";

// ---------------------------------------------------------------------------------------------
// Auxiliares
// ---------------------------------------------------------------------------------------------

const ENGLISH_TECHNICAL = /\b(the|failed|error|exception|stderr|stdout|exit code|undefined|null|timeout|refused|not found|invalid)\b/i;

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

function snapshot(over: Partial<InfraSnapshot> = {}): InfraSnapshot {
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
      dockerPath: null,
      checkedAt: "2026-11-27T12:00:00Z",
    },
    repo: { status: "ok", uvPath: null, missing: [] },
    dataDir: { path: "/dados", status: "ok", canonicalPath: "/dados" },
    services: SERVICE_NAMES.map((service) => ({
      service,
      state: "stopped" as const,
      address: "127.0.0.1:1",
      checkedAt: "2026-11-27T12:00:00Z",
    })),
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

function fakeClient(over: Partial<InfraApi> = {}): InfraApi & Record<keyof InfraApi, ReturnType<typeof vi.fn>> {
  const base = {
    getInfraSnapshot: vi.fn().mockResolvedValue(snapshot()),
    refreshInfra: vi.fn().mockResolvedValue(snapshot()),
    startStack: vi.fn().mockResolvedValue({ alreadyInState: false }),
    stopStack: vi.fn().mockResolvedValue({ alreadyInState: false }),
    startWorkers: vi.fn().mockResolvedValue({ alreadyInState: false }),
    stopWorkers: vi.fn().mockResolvedValue({ alreadyInState: false }),
    resolvePreviousPool: vi.fn().mockResolvedValue({}),
    prepareImages: vi.fn().mockResolvedValue({}),
    prepareModels: vi.fn().mockResolvedValue({}),
    getOperationLog: vi.fn().mockResolvedValue({ lines: ["linha 1"] }),
    setGuideOpen: vi.fn().mockResolvedValue(undefined),
  };
  return { ...base, ...over } as never;
}

const failure = (code: string, extra: Partial<OperationResult> = {}): OperationResult => ({
  ok: false,
  code: code as OperationResult["code"],
  service: null,
  port: null,
  failure: null,
  shortfallBytes: null,
  alreadyInState: null,
  ...extra,
});

// ---------------------------------------------------------------------------------------------
// 9.1.4 — mensagens em português para todo código e estado
// ---------------------------------------------------------------------------------------------

describe("mensagens em português (9.1.4, FR-038, SC-006)", () => {
  it("toda ErrorCode tem mensagem principal em português, sem texto técnico em inglês", () => {
    for (const code of ERROR_CODES) {
      for (const context of ["folder", "document", "image", "link", "settings"] as const) {
        const text = errorMessage(code, context);
        expect(text.length, `${code}/${context}`).toBeGreaterThan(5);
        expect(text, `${code}/${context}`).not.toMatch(ENGLISH_TECHNICAL);
      }
    }
  });

  it("todo WorkerExitReason tem mensagem em português e a de código 3, 4 e 5 tem o próximo passo", () => {
    for (const reason of WORKER_EXIT_REASONS) {
      expect(workerExitReasonMessages[reason], reason).toBeTruthy();
      expect(workerExitReasonMessages[reason], reason).not.toMatch(ENGLISH_TECHNICAL);
    }
    expect(workerExitReasonMessages.queueExtraMissing).toContain("tente de novo");
    expect(workerExitReasonMessages.credentials).toContain("Confira");
    expect(workerExitReasonMessages.topology).toContain("Atualize");
  });

  it("todo valor dos enums do contrato tem texto em português", () => {
    const tables: Array<[string, readonly string[], Record<string, string>]> = [
      ["runtime", RUNTIME_KINDS, runtimeNames],
      ["estado do runtime", RUNTIME_STATES, runtimeStateLabels],
      ["serviço", SERVICE_NAMES, serviceNames],
      ["estado do serviço", SERVICE_STATES, serviceStateLabels],
      ["estado do pool", WORKER_POOL_STATES, poolStateLabels],
      ["resumo", SUMMARIES, summaryLabels],
      ["etapa", STEP_KINDS, stepNames],
      ["estado da etapa", STEP_STATES, stepStateLabels],
      ["falha de download", DOWNLOAD_FAILURES, downloadFailureMessages],
      ["operação", OPERATION_KINDS, operationKindLabels],
      ["etapa da operação", OPERATION_STAGES, operationStageMessages],
      ["pasta de dados", DATA_DIR_STATUSES, dataDirStatusMessages],
    ];
    for (const [name, values, table] of tables) {
      for (const value of values) {
        expect(table[value], `${name}: ${value}`).toBeTruthy();
        expect(table[value], `${name}: ${value}`).not.toMatch(ENGLISH_TECHNICAL);
      }
    }
    const repo: RepoCheck = { status: "ok", uvPath: null, missing: ["compose.yaml"] };
    for (const status of REPO_STATUSES) {
      expect(repoMessage({ ...repo, status }), status).not.toMatch(ENGLISH_TECHNICAL);
    }
    const report = snapshot().runtime;
    for (const overall of RUNTIME_OVERALLS) {
      const text = runtimeMessage({ ...report, overall } as RuntimeReport);
      expect(text, overall).toBeTruthy();
      expect(text, overall).not.toMatch(ENGLISH_TECHNICAL);
    }
  });

  it("as mensagens literais da spec estão exatas", () => {
    expect(messages.poolClosing).toBe("Encerrando workers…");
    expect(messages.consumerStopped).toBe("Consumidor de eventos parado");
    expect(messages.infraError_stopFailed).toBe(
      "Não foi possível parar a infraestrutura. Veja os detalhes técnicos e tente de novo.",
    );
    expect(repoMessage({ status: "modelsUnsupported", uvPath: null, missing: [] })).toBe(
      "A pasta do repositório indicada tem uma versão incompatível com este app: falta o comando de preparação de modelos ou o contrato mudou. Atualize o repositório e verifique de novo.",
    );
    expect(Object.values(serviceStateLabels)).toEqual(["parado", "iniciando", "saudável", "com problema"]);
  });

  it("o resultado de uma operação cita serviço, porta, espaço e motivo quando o retrato os traz", () => {
    expect(operationResultMessage(failure("portInUse", { service: "postgres", port: 5432 }))).toContain("5432");
    expect(operationResultMessage(failure("portInUse", { service: "postgres", port: 5432 }))).toContain("Postgres");
    expect(operationResultMessage(failure("serviceUnhealthy", { service: "rabbitmq" }))).toContain("RabbitMQ");
    expect(operationResultMessage(failure("insufficientDisk", { shortfallBytes: 2_500_000_000 }))).toContain("2,5 GB");
    expect(operationResultMessage(failure("downloadFailed", { failure: "noNetwork" }))).toBe(downloadFailureMessages.noNetwork);
    expect(operationResultMessage(failure("portInUse"))).toBe(messages.infraError_portInUse);
    expect(operationResultMessage({ ...failure("x"), ok: true })).toBe("");
  });

  it("formata volume e hora em português e trata hora vazia", () => {
    expect(formatBytes(1_200_000_000)).toBe("1,2 GB");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatTime("")).toBe(messages.notCheckedYet);
    expect(formatTime(null)).toBe(messages.notCheckedYet);
    expect(formatTime("lixo")).toBe(messages.notCheckedYet);
    expect(formatTime("2026-11-27T12:00:00Z")).toMatch(/\d{2}:\d{2}:\d{2}/);
  });

  it("o indicador resumido tem texto e rótulo para todos os estados, nunca só cor (FR-042, SC-010)", () => {
    for (const summary of [...SUMMARIES, null] as const) {
      expect(summaryIndicatorText(summary)).toMatch(/^Infraestrutura: \S+/);
      expect(summaryAriaLabel(summary)).toMatch(/^Estado da infraestrutura: \S+/);
    }
    expect(summaryIndicatorText("partial")).toBe("Infraestrutura: parcial");
  });

  it("progresso: percentual com total conhecido; volume, itens e hora sem total (FR-032)", () => {
    const known = progressText(step({ state: "downloading", downloadedBytes: 500_000_000, totalBytes: 1_000_000_000, itemsDone: 1 }));
    expect(known).toContain("50% concluído");
    const unknown = progressText(
      step({ step: "models", state: "downloading", downloadedBytes: 1_200_000_000, itemsDone: 1, itemsTotal: 2, updatedAt: "2026-11-27T12:00:05Z" }),
    );
    expect(unknown).toContain("1,2 GB baixados");
    expect(unknown).toContain("1 de 2 modelos");
    expect(unknown).toContain(messages.lastUpdate);
    expect(progressText(step({ state: "downloading" }))).toContain(messages.progressWaiting);
  });
});

// ---------------------------------------------------------------------------------------------
// 9.2.4 — sondagem do retrato
// ---------------------------------------------------------------------------------------------

describe("sondagem do retrato (9.2, research Decision 11)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const downloading = snapshot({ firstRun: [step({ state: "downloading", downloadedBytes: 10 }), step({ step: "models" })] });
  const withOperation = snapshot({
    operations: [{ kind: "startStack", stage: "runningUp", startedAt: "2026-11-27T12:00:00Z", result: null }],
  });

  it("cadência: 3 s ocioso; 1 s com operação, download ou ação em andamento", () => {
    expect(POLL_IDLE_MS).toBe(3000);
    expect(POLL_FAST_MS).toBe(1000);
    expect(pollIntervalMs(null)).toBe(3000);
    expect(pollIntervalMs(snapshot())).toBe(3000);
    expect(pollIntervalMs(withOperation)).toBe(1000);
    expect(pollIntervalMs(downloading)).toBe(1000);
    expect(pollIntervalMs(snapshot(), true)).toBe(1000);
    // Operação já terminada (com resultado) não conta como em andamento.
    const finished = snapshot({
      operations: [{ kind: "startStack", stage: "runningUp", startedAt: "x", result: failure("portInUse") }],
    });
    expect(isBusy(finished)).toBe(false);
    expect(activeOperations(withOperation)).toHaveLength(1);
  });

  it("lê ao iniciar e depois a cada 3 s, e a cada 1 s com operação em andamento", async () => {
    const client = fakeClient();
    const store = new InfraStore(client);
    store.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(client.getInfraSnapshot).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2999);
    expect(client.getInfraSnapshot).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(client.getInfraSnapshot).toHaveBeenCalledTimes(2);

    client.getInfraSnapshot.mockResolvedValue(withOperation);
    await vi.advanceTimersByTimeAsync(3000); // leitura 3 traz a operação
    expect(client.getInfraSnapshot).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(1000);
    expect(client.getInfraSnapshot).toHaveBeenCalledTimes(5);
    store.stop();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(client.getInfraSnapshot).toHaveBeenCalledTimes(5);
  });

  it("nunca sobrepõe leituras: a próxima só é agendada depois da anterior terminar", async () => {
    let release: (value: InfraSnapshot) => void = () => undefined;
    const client = fakeClient({
      getInfraSnapshot: vi.fn().mockImplementation(() => new Promise<InfraSnapshot>((r) => (release = r))),
    });
    const store = new InfraStore(client);
    store.start();
    await vi.advanceTimersByTimeAsync(2500); // abaixo do tempo máximo da leitura (5 s)
    expect(client.getInfraSnapshot).toHaveBeenCalledTimes(1);
    release(snapshot());
    await vi.advanceTimersByTimeAsync(0);
    expect(store.snapshot).not.toBeNull();
    store.stop();
  });

  it("erro de leitura vira estado parcial sem congelar; a leitura seguinte recupera", async () => {
    const client = fakeClient();
    client.getInfraSnapshot.mockRejectedValueOnce({ code: "io" });
    const store = new InfraStore(client);
    expect(store.summary).toBeNull();
    store.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(store.readFailed).toBe(true);
    expect(store.summary).toBe("partial");
    await vi.advanceTimersByTimeAsync(3000);
    expect(store.readFailed).toBe(false);
    expect(store.summary).toBe("stopped");
    store.stop();
  });

  it("leitura que não responde vira erro no tempo máximo e a sondagem segue", async () => {
    const client = fakeClient({ getInfraSnapshot: vi.fn().mockReturnValue(new Promise(() => undefined)) });
    const store = new InfraStore(client);
    store.start();
    await vi.advanceTimersByTimeAsync(5001);
    expect(store.readFailed).toBe(true);
    await vi.advanceTimersByTimeAsync(3000);
    expect(client.getInfraSnapshot).toHaveBeenCalledTimes(2);
    store.stop();
  });

  it("o resumo vem do retrato do núcleo (composição saudável, parcial, parada)", async () => {
    const client = fakeClient();
    const store = new InfraStore(client);
    for (const summary of SUMMARIES) {
      client.getInfraSnapshot.mockResolvedValue(snapshot({ summary }));
      await store.readOnce();
      expect(store.summary).toBe(summary);
    }
  });

  it("fechamento do app: pool em encerramento sem ação da interface mostra a tela de encerramento", async () => {
    const client = fakeClient();
    const stopping = snapshot({ pool: { ...snapshot().pool, state: "stopping" } });
    client.getInfraSnapshot.mockResolvedValue(stopping);
    const store = new InfraStore(client);
    await store.readOnce();
    expect(store.closing).toBe(true);
    // "Parar workers" pela interface tem a própria tela: não é fechamento.
    client.stopWorkers.mockImplementation(() => new Promise(() => undefined));
    void store.stopWorkers();
    expect(store.pending).toBe("stopWorkers");
    expect(store.closing).toBe(false);
  });

  it("sinal de atividade: sem mudança por mais de 5 s a etapa fica 'sem resposta' (FR-032)", async () => {
    let now = 1_000_000;
    const client = fakeClient();
    const store = new InfraStore(client, () => now);
    const at = (bytes: number, updatedAt: string) =>
      snapshot({ firstRun: [step(), step({ step: "models", state: "downloading", downloadedBytes: bytes, updatedAt, itemsDone: 1, itemsTotal: 2 })] });

    client.getInfraSnapshot.mockResolvedValue(at(100, "2026-11-27T12:00:00Z"));
    await store.readOnce();
    expect(store.isStalled("models")).toBe(false);
    now += 4000;
    client.getInfraSnapshot.mockResolvedValue(at(200, "2026-11-27T12:00:04Z")); // mudou: zera a contagem
    await store.readOnce();
    now += STALL_MS - 1;
    expect(store.isStalled("models")).toBe(false);
    now += 2;
    expect(store.isStalled("models")).toBe(true);
    client.getInfraSnapshot.mockResolvedValue(at(300, "2026-11-27T12:00:10Z"));
    await store.readOnce();
    expect(store.isStalled("models")).toBe(false);
    // Sem download em curso não há "sem resposta".
    client.getInfraSnapshot.mockResolvedValue(snapshot());
    await store.readOnce();
    now += 60_000;
    expect(store.isStalled("models")).toBe(false);
  });
});

describe("ações com erro traduzido (9.2.2)", () => {
  it("iniciar: informa 'já estava no ar' (FR-009) e 'no ar' conforme alreadyInState", async () => {
    const client = fakeClient();
    const store = new InfraStore(client);
    await store.startStack();
    expect(store.notice).toEqual({ kind: "info", text: messages.stackStarted });
    client.startStack.mockResolvedValue({ alreadyInState: true });
    await store.startStack();
    expect(store.notice?.text).toBe(messages.stackAlreadyRunning);
    client.stopStack.mockResolvedValue({ alreadyInState: true });
    await store.stopStack();
    expect(store.notice?.text).toBe(messages.stackAlreadyStopped);
    expect(store.pending).toBeNull();
  });

  it("porta ocupada cita serviço e porta do resultado da operação", async () => {
    const client = fakeClient();
    client.startStack.mockRejectedValue({ code: "portInUse", detail: null });
    client.getInfraSnapshot.mockResolvedValue(
      snapshot({
        operations: [
          { kind: "startStack", stage: "runningUp", startedAt: "x", result: failure("portInUse", { service: "postgres", port: 5432 }) },
        ],
      }),
    );
    const store = new InfraStore(client);
    await store.startStack();
    expect(store.notice?.kind).toBe("error");
    expect(store.notice?.text).toContain("5432");
    expect(store.notice?.text).toContain("Postgres");
  });

  it("imagesMissing e modelsMissing levam à etapa da primeira execução, sem texto técnico", async () => {
    const client = fakeClient();
    const store = new InfraStore(client);
    client.startStack.mockRejectedValue({ code: "imagesMissing", detail: null });
    await store.startStack();
    expect(store.attention).toBe("images");
    expect(store.notice?.text).toBe(messages.imagesNeededForStart);
    client.startWorkers.mockRejectedValue({ code: "modelsMissing", detail: null });
    await store.startWorkers();
    expect(store.attention).toBe("models");
    expect(store.notice?.text).toBe(messages.modelsNeededForWorkers);
  });

  it("código desconhecido ou erro sem código vira mensagem em português", async () => {
    const client = fakeClient();
    client.stopStack.mockRejectedValue(new Error("boom"));
    const store = new InfraStore(client);
    await store.stopStack();
    expect(store.notice?.text).toBe(errorMessage("io"));
  });

  it("uma ação por vez na interface (o núcleo também recusa)", async () => {
    const client = fakeClient();
    let finish: (value: { alreadyInState: boolean }) => void = () => undefined;
    client.startStack.mockImplementation(() => new Promise((r) => (finish = r)));
    const store = new InfraStore(client);
    const first = store.startStack();
    await store.stopStack();
    expect(client.stopStack).not.toHaveBeenCalled();
    finish({ alreadyInState: false });
    await first;
    expect(store.pending).toBeNull();
  });

  it("download só dispara com o texto de confirmação já aceito: prepare(step) envia confirmed=true", async () => {
    const client = fakeClient();
    const store = new InfraStore(client);
    await store.prepare("models");
    expect(client.prepareModels).toHaveBeenCalledWith(true);
    await store.prepare("images");
    expect(client.prepareImages).toHaveBeenCalledWith(true);
  });

  it("detalhes técnicos sob demanda; falha de leitura vira lista vazia", async () => {
    const client = fakeClient();
    const store = new InfraStore(client);
    expect(store.technicalLog).toBeNull();
    await store.loadTechnicalLog();
    expect(store.technicalLog).toEqual(["linha 1"]);
    store.hideTechnicalLog();
    expect(store.technicalLog).toBeNull();
    client.getOperationLog.mockRejectedValue({ code: "io" });
    await store.loadTechnicalLog();
    expect(store.technicalLog).toEqual([]);
  });
});

// ---------------------------------------------------------------------------------------------
// 9.3 — paridade Rust ↔ TypeScript
// ---------------------------------------------------------------------------------------------

// O código-fonte do Rust entra como texto (`?raw`): sem tipos de Node e sem depender do cwd.
const RUST_SOURCES = import.meta.glob("../src-tauri/src/**/*.rs", { query: "?raw", import: "default", eager: true }) as Record<
  string,
  string
>;
const rust = (file: string): string => {
  const text = RUST_SOURCES[`../src-tauri/src/${file}`];
  if (text === undefined) throw new Error(`fonte Rust não encontrada: ${file}`);
  return text;
};

function camel(name: string): string {
  return name.charAt(0).toLowerCase() + name.slice(1);
}

function snakeToCamel(name: string): string {
  return name.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase());
}

/** Variantes de `pub enum <name>` em camelCase (confere o `rename_all = "camelCase"`). */
function rustEnum(file: string, name: string): string[] {
  const text = rust(file);
  const match = new RegExp(`((?:#\\[[^\\]]*\\]\\s*)*)pub enum ${name} \\{([\\s\\S]*?)\\n\\}`).exec(text);
  if (!match) throw new Error(`enum ${name} não encontrado em ${file}`);
  expect(match[1], `${name} precisa de rename_all camelCase`).toContain('rename_all = "camelCase"');
  const variants: string[] = [];
  for (const line of match[2]!.split("\n")) {
    const trimmed = line.trim();
    if (trimmed.startsWith("//") || trimmed.startsWith("#")) continue;
    const variant = /^([A-Z][A-Za-z0-9]*)\b/.exec(trimmed);
    if (variant) variants.push(camel(variant[1]!));
  }
  return variants;
}

/** Campos serializados de `pub struct <name>`, em camelCase e na ordem do Rust (ignora `serde(skip)`). */
function rustStruct(file: string, name: string): string[] {
  const text = rust(file);
  const match = new RegExp(`((?:#\\[[^\\]]*\\]\\s*)*)pub struct ${name} \\{([\\s\\S]*?)\\n\\}`).exec(text);
  if (!match) throw new Error(`struct ${name} não encontrada em ${file}`);
  const fields: string[] = [];
  let skip = false;
  for (const line of match[2]!.split("\n")) {
    const trimmed = line.trim();
    if (trimmed.startsWith("#[serde") && /\bskip\b/.test(trimmed)) skip = true;
    const field = /^pub ([a-z_0-9]+):/.exec(trimmed);
    if (field) {
      if (!skip) fields.push(snakeToCamel(field[1]!));
      skip = false;
    }
  }
  return fields;
}

describe("paridade Rust ↔ TypeScript (9.3.1 a 9.3.3)", () => {
  const ENUMS: Array<[string, string, readonly string[]]> = [
    ["error.rs", "ErrorCode", ERROR_CODES],
    ["stack/runtime.rs", "RuntimeKind", RUNTIME_KINDS],
    ["stack/runtime.rs", "RuntimeState", RUNTIME_STATES],
    ["stack/runtime.rs", "RuntimeOverall", RUNTIME_OVERALLS],
    ["stack/repo.rs", "RepoStatus", REPO_STATUSES],
    ["stack/data_dir.rs", "DataDirStatus", DATA_DIR_STATUSES],
    ["stack/health.rs", "ServiceName", SERVICE_NAMES],
    ["stack/health.rs", "ServiceState", SERVICE_STATES],
    ["stack/pool.rs", "WorkerPoolState", WORKER_POOL_STATES],
    ["stack/pool.rs", "WorkerExitReason", WORKER_EXIT_REASONS],
    ["stack/first_run.rs", "StepKind", STEP_KINDS],
    ["stack/first_run.rs", "StepState", STEP_STATES],
    ["stack/first_run.rs", "DownloadFailure", DOWNLOAD_FAILURES],
    ["stack/instance_lock.rs", "OperationKind", OPERATION_KINDS],
    ["stack/lifecycle.rs", "OperationStage", OPERATION_STAGES],
    ["stack/mod.rs", "Summary", SUMMARIES],
    ["stack/stack_settings.rs", "GuideRuntime", RUNTIME_KINDS],
  ];

  for (const [file, name, ts] of ENUMS) {
    it(`enum ${name}: mesmos valores no Rust e em types.ts`, () => {
      expect([...rustEnum(file, name)].sort()).toEqual([...ts].sort());
    });
  }

  const STRUCTS: Array<[string, string, keyof typeof CONTRACT_KEYS]> = [
    ["stack/mod.rs", "InfraSnapshot", "InfraSnapshot"],
    ["stack/runtime.rs", "RuntimeReport", "RuntimeReport"],
    ["stack/runtime.rs", "RuntimeEntry", "RuntimeEntry"],
    ["stack/repo.rs", "RepoCheck", "RepoCheck"],
    ["stack/data_dir.rs", "DataDirCheck", "DataDirCheck"],
    ["stack/health.rs", "ServiceStatus", "ServiceStatus"],
    ["stack/pool.rs", "WorkerPool", "WorkerPool"],
    ["stack/first_run.rs", "FirstRunStep", "FirstRunStep"],
    ["stack/mod.rs", "Operation", "Operation"],
    ["stack/mod.rs", "OperationResult", "OperationResult"],
    ["stack/stack_settings.rs", "StackSettings", "StackSettings"],
    ["stack/stack_settings.rs", "GuideProgress", "GuideProgress"],
    ["stack/config.rs", "Ports", "StackPorts"],
    ["stack/config.rs", "StackSettingsResponse", "StackSettingsResponse"],
    ["stack/lifecycle.rs", "LifecycleOutcome", "LifecycleOutcome"],
  ];

  for (const [file, name, key] of STRUCTS) {
    it(`struct ${name}: mesmas chaves camelCase no Rust e em CONTRACT_KEYS`, () => {
      expect(rustStruct(file, name)).toEqual([...CONTRACT_KEYS[key]]);
    });
  }

  it("a fixture serializada pelo Rust tem exatamente as chaves e os valores dos tipos", () => {
    const snap = fixture as unknown as InfraSnapshot;
    const keys = (value: object): string[] => Object.keys(value).sort();
    const same = (value: object, name: keyof typeof CONTRACT_KEYS): void =>
      expect(keys(value), name).toEqual([...CONTRACT_KEYS[name]].sort());

    same(snap, "InfraSnapshot");
    same(snap.runtime, "RuntimeReport");
    snap.runtime.runtimes.forEach((entry) => same(entry, "RuntimeEntry"));
    same(snap.repo, "RepoCheck");
    same(snap.dataDir, "DataDirCheck");
    snap.services.forEach((service) => same(service, "ServiceStatus"));
    same(snap.pool, "WorkerPool");
    snap.firstRun.forEach((entry) => same(entry, "FirstRunStep"));
    snap.operations.forEach((operation) => {
      same(operation, "Operation");
      if (operation.result) same(operation.result, "OperationResult");
    });

    // 9.3.3: valores de state, summary, overall e reason do Rust presentes nos tipos.
    expect(RUNTIME_OVERALLS).toContain(snap.runtime.overall);
    expect(REPO_STATUSES).toContain(snap.repo.status);
    expect(DATA_DIR_STATUSES).toContain(snap.dataDir.status);
    expect(SUMMARIES).toContain(snap.summary);
    expect(WORKER_POOL_STATES).toContain(snap.pool.state);
    if (snap.pool.reason !== null) expect(WORKER_EXIT_REASONS).toContain(snap.pool.reason);
    snap.runtime.runtimes.forEach((entry) => {
      expect(RUNTIME_KINDS).toContain(entry.kind);
      expect(RUNTIME_STATES).toContain(entry.state);
    });
    snap.services.forEach((service) => {
      expect(SERVICE_NAMES).toContain(service.service);
      expect(SERVICE_STATES).toContain(service.state);
    });
    snap.firstRun.forEach((entry) => {
      expect(STEP_KINDS).toContain(entry.step);
      expect(STEP_STATES).toContain(entry.state);
      if (entry.failure !== null) expect(DOWNLOAD_FAILURES).toContain(entry.failure);
    });
    snap.operations.forEach((operation) => {
      expect(OPERATION_KINDS).toContain(operation.kind);
      expect(OPERATION_STAGES).toContain(operation.stage);
      if (operation.result?.code) expect(ERROR_CODES).toContain(operation.result.code);
    });
  });

  it("não há endereço nem campo do painel de gestão do RabbitMQ nos tipos (FR-029)", () => {
    const all = JSON.stringify(CONTRACT_KEYS).toLowerCase();
    expect(all).not.toContain("management");
    expect(all).not.toContain("15672");
  });
});
