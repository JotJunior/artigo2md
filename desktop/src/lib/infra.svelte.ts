// Estado reativo da infraestrutura: sondagem do retrato (research Decision 11), ações com erro
// traduzido e estado do indicador resumido da barra lateral (FR-025, FR-027). A leitura nunca
// bloqueia a interface: é assíncrona, sem sobreposição e com tempo máximo; erro de leitura vira
// "com problema" (indicador parcial) e a sondagem continua.

import * as api from "./api";
import { errorMessage, messages, operationResultMessage } from "./messages";
import type {
  AppError,
  ErrorCode,
  InfraSnapshot,
  LifecycleOutcome,
  Operation,
  OperationKind,
  PreviousPoolAction,
  StepKind,
  Summary,
} from "./types";

/** Cadência de leitura com operação ou download em andamento (contracts/tauri-commands.md). */
export const POLL_FAST_MS = 1000;
/** Cadência de leitura fora disso. Mantida com a janela oculta (spec FR-027; pendência performance CHK016). */
export const POLL_IDLE_MS = 3000;
/** Tempo máximo de uma leitura: passado dele, a leitura conta como erro e a sondagem segue. */
export const READ_TIMEOUT_MS = 5000;
/** Sem mudança de volume nem de hora por mais que isto, a etapa aparece como "sem resposta" (FR-032). */
export const STALL_MS = 5000;

export interface InfraApi {
  getInfraSnapshot(): Promise<InfraSnapshot>;
  refreshInfra(): Promise<InfraSnapshot>;
  startStack(): Promise<LifecycleOutcome>;
  stopStack(): Promise<LifecycleOutcome>;
  startWorkers(): Promise<LifecycleOutcome>;
  stopWorkers(): Promise<LifecycleOutcome>;
  resolvePreviousPool(action: PreviousPoolAction): Promise<unknown>;
  prepareImages(confirmed: boolean): Promise<unknown>;
  prepareModels(confirmed: boolean): Promise<unknown>;
  getOperationLog(): Promise<{ lines: string[] }>;
  setGuideOpen(open: boolean): Promise<void>;
}

/** Operação ativa = ainda sem resultado. */
export function activeOperations(snapshot: InfraSnapshot | null): Operation[] {
  return snapshot ? snapshot.operations.filter((operation) => operation.result === null) : [];
}

/** Há operação ou download em andamento (cadência de 1 s). */
export function isBusy(snapshot: InfraSnapshot | null): boolean {
  if (!snapshot) return false;
  return activeOperations(snapshot).length > 0 || snapshot.firstRun.some((step) => step.state === "downloading");
}

export function pollIntervalMs(snapshot: InfraSnapshot | null, actionInFlight = false): number {
  return actionInFlight || isBusy(snapshot) ? POLL_FAST_MS : POLL_IDLE_MS;
}

export type ActionName =
  | "startStack"
  | "stopStack"
  | "startWorkers"
  | "stopWorkers"
  | "resolvePool"
  | "prepareImages"
  | "prepareModels"
  | "refresh";

export interface InfraNotice {
  kind: "info" | "error";
  text: string;
}

const OPERATION_OF_ACTION: Partial<Record<ActionName, OperationKind>> = {
  startStack: "startStack",
  stopStack: "stopStack",
  startWorkers: "startWorkers",
  stopWorkers: "stopWorkers",
  prepareImages: "prepareImages",
  prepareModels: "prepareModels",
};

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timeout")), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

export class InfraStore {
  snapshot = $state<InfraSnapshot | null>(null);
  readFailed = $state(false);
  notice = $state<InfraNotice | null>(null);
  pending = $state<ActionName | null>(null);
  /** Etapa da primeira execução a destacar depois de uma recusa (`imagesMissing`, `modelsMissing`). */
  attention = $state<StepKind | null>(null);
  technicalLog = $state<string[] | null>(null);

  private running = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  /** Última assinatura de progresso de cada etapa e desde quando ela não muda (sinal de atividade). */
  private progressSeen = new Map<StepKind, { signature: string; since: number }>();

  constructor(
    private readonly client: InfraApi = api,
    private readonly clock: () => number = () => Date.now(),
  ) {}

  private noteProgress(snapshot: InfraSnapshot): void {
    const now = this.clock();
    for (const step of snapshot.firstRun) {
      if (step.state !== "downloading") {
        this.progressSeen.delete(step.step);
        continue;
      }
      const signature = `${step.downloadedBytes}|${step.updatedAt}|${step.itemsDone}`;
      const seen = this.progressSeen.get(step.step);
      if (!seen || seen.signature !== signature) this.progressSeen.set(step.step, { signature, since: now });
    }
  }

  /**
   * Download em curso sem nenhuma mudança de `downloadedBytes`, `updatedAt` ou `itemsDone` por mais
   * de 5 s: a tela mostra "sem resposta" em vez de um valor parado (FR-032).
   */
  isStalled(kind: StepKind): boolean {
    const seen = this.progressSeen.get(kind);
    return seen !== undefined && this.clock() - seen.since > STALL_MS;
  }

  /** Indicador da barra lateral: `null` até a primeira leitura; erro de leitura vira "parcial". */
  get summary(): Summary | null {
    if (this.readFailed) return "partial";
    return this.snapshot?.summary ?? null;
  }

  /**
   * Fechamento do app: o núcleo está parando o pool sem que a interface tenha pedido (FR-024).
   * "Parar workers" e "resolver pool anterior" têm a própria tela e não contam.
   */
  get closing(): boolean {
    return this.snapshot?.pool.state === "stopping" && this.pending === null;
  }

  // --- Sondagem -------------------------------------------------------------------------------

  start(): void {
    if (this.running) return;
    this.running = true;
    void this.tick();
  }

  stop(): void {
    this.running = false;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private async tick(): Promise<void> {
    await this.readOnce();
    if (!this.running) return;
    this.timer = setTimeout(() => void this.tick(), pollIntervalMs(this.snapshot, this.pending !== null));
  }

  /** Uma leitura do último retrato do núcleo. Nunca lança. */
  async readOnce(): Promise<void> {
    try {
      const next = await withTimeout(this.client.getInfraSnapshot(), READ_TIMEOUT_MS);
      this.noteProgress(next);
      this.snapshot = next;
      this.readFailed = false;
    } catch {
      this.readFailed = true;
    }
  }

  /** "Verificar novamente" (FR-003): nova verificação completa pelo núcleo. */
  async recheck(): Promise<void> {
    await this.run("refresh", async () => {
      const next = await this.client.refreshInfra();
      this.noteProgress(next);
      this.snapshot = next;
      this.readFailed = false;
    });
  }

  // --- Ações ----------------------------------------------------------------------------------

  startStack(): Promise<void> {
    return this.lifecycle("startStack", () => this.client.startStack(), messages.stackStarted, messages.stackAlreadyRunning);
  }

  stopStack(): Promise<void> {
    return this.lifecycle("stopStack", () => this.client.stopStack(), messages.stackStopped, messages.stackAlreadyStopped);
  }

  startWorkers(): Promise<void> {
    return this.lifecycle(
      "startWorkers",
      () => this.client.startWorkers(),
      messages.workersStarted,
      messages.workersAlreadyRunning,
    );
  }

  stopWorkers(): Promise<void> {
    return this.lifecycle(
      "stopWorkers",
      () => this.client.stopWorkers(),
      messages.workersStoppedNotice,
      messages.workersAlreadyStopped,
    );
  }

  resolvePreviousPool(action: PreviousPoolAction): Promise<void> {
    return this.run("resolvePool", async () => {
      await this.client.resolvePreviousPool(action);
    });
  }

  /** Só chamado depois da confirmação em português (FR-031); o diálogo nativo do núcleo confirma de novo. */
  prepare(step: StepKind): Promise<void> {
    const name: ActionName = step === "images" ? "prepareImages" : "prepareModels";
    return this.run(name, async () => {
      if (step === "images") await this.client.prepareImages(true);
      else await this.client.prepareModels(true);
    });
  }

  dismissNotice(): void {
    this.notice = null;
  }

  async loadTechnicalLog(): Promise<void> {
    try {
      this.technicalLog = (await this.client.getOperationLog()).lines;
    } catch {
      this.technicalLog = [];
    }
  }

  hideTechnicalLog(): void {
    this.technicalLog = null;
  }

  /** Avisa o núcleo que o guia abriu/fechou (diagnóstico a cada 5 s). Falha não bloqueia nada. */
  async setGuideOpen(open: boolean): Promise<void> {
    try {
      await this.client.setGuideOpen(open);
    } catch {
      /* o núcleo continua verificando o runtime, só na cadência normal */
    }
  }

  private lifecycle(
    name: ActionName,
    call: () => Promise<LifecycleOutcome>,
    doneText: string,
    alreadyText: string,
  ): Promise<void> {
    return this.run(name, async () => {
      const outcome = await call();
      this.notice = { kind: "info", text: outcome.alreadyInState ? alreadyText : doneText };
    });
  }

  private async run(name: ActionName, body: () => Promise<void>): Promise<void> {
    if (this.pending !== null) return;
    this.pending = name;
    this.notice = null;
    this.attention = null;
    try {
      await body();
    } catch (raw) {
      const error = raw as Partial<AppError>;
      await this.readOnce();
      this.notice = { kind: "error", text: this.explain(name, (error.code ?? "io") as ErrorCode) };
    } finally {
      this.pending = null;
    }
    await this.readOnce();
  }

  /** Mensagem principal em português; cita serviço, porta, espaço e motivo quando o retrato os traz. */
  private explain(name: ActionName, code: ErrorCode): string {
    if (code === "imagesMissing") {
      this.attention = "images";
      return messages.imagesNeededForStart;
    }
    if (code === "modelsMissing") {
      this.attention = "models";
      return messages.modelsNeededForWorkers;
    }
    const kind = OPERATION_OF_ACTION[name];
    const finished = this.snapshot?.operations.find(
      (operation) => operation.kind === kind && operation.result !== null && !operation.result.ok && operation.result.code === code,
    );
    return finished?.result ? operationResultMessage(finished.result) : errorMessage(code, "document");
  }
}

export const infra = new InfraStore();
