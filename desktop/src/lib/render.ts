// Sessão de renderização: um Worker por vez; trocar de documento encerra o Worker em curso
// (tarefa 4.2.4). O Worker é injetável para os testes (jsdom não tem Worker).

export interface RenderRequest {
  source: string;
}

/** `error`: mensagem técnica da exceção no Worker (diagnóstico; nunca contém caminho do disco). */
export type RenderResponse = { ok: true; html: string } | { ok: false; error?: string };

export interface RenderWorkerLike {
  onmessage: ((event: { data: RenderResponse }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onmessageerror?: ((event: unknown) => void) | null;
  postMessage(message: RenderRequest): void;
  terminate(): void;
}

export class RenderCancelledError extends Error {
  constructor() {
    super("render cancelled");
  }
}

/** Onde a renderização falhou; vai no detalhe técnico do aviso (tarefa 9.1.2). */
export type RenderFailureStage = "workerCreate" | "workerError" | "messageError" | "renderError";

/** Limite do detalhe técnico mostrado na interface. */
const MAX_DETAIL = 300;

/** Falha da renderização com detalhe técnico para diagnóstico (research R9.12). */
export class RenderFailedError extends Error {
  readonly detail: string;
  constructor(readonly stage: RenderFailureStage, detail: string) {
    const clipped = detail.length > MAX_DETAIL ? `${detail.slice(0, MAX_DETAIL)}…` : detail;
    super(`render failed (${stage}): ${clipped}`);
    this.detail = `${stage}: ${clipped}`;
  }
}

function text(value: unknown): string {
  if (value instanceof Error) return `${value.name}: ${value.message}`;
  return typeof value === "string" ? value : String(value);
}

/** Resume um evento `error` de Worker (ErrorEvent: message, filename, lineno, colno). */
export function describeWorkerError(event: unknown): string {
  if (typeof event !== "object" || event === null) return "evento de erro sem detalhes";
  const e = event as { message?: unknown; filename?: unknown; lineno?: unknown; colno?: unknown };
  const message = typeof e.message === "string" && e.message !== "" ? e.message : "sem mensagem";
  const where =
    typeof e.filename === "string" && e.filename !== ""
      ? ` (${e.filename}:${typeof e.lineno === "number" ? e.lineno : "?"}:${typeof e.colno === "number" ? e.colno : "?"})`
      : "";
  return `${message}${where}`;
}

export function defaultWorkerFactory(): RenderWorkerLike {
  if (__RENDER_WORKER_AS_MODULE__) {
    // `tauri dev`: o Vite serve o Worker sem empacotar, com `import` ESM. Um Worker clássico
    // falharia com SyntaxError em todo documento (research R9.12), por isso aqui ele é de módulo.
    return new Worker(new URL("./render.worker.ts", import.meta.url), {
      type: "module",
    }) as unknown as RenderWorkerLike;
  }
  // Produção: Worker clássico (o Vite empacota como IIFE), servido de 'self': não depende de
  // Workers de módulo (Safari 15+; piso macOS 11) e cabe em `worker-src 'self'` (research R9.12).
  return new Worker(new URL("./render.worker.ts", import.meta.url)) as unknown as RenderWorkerLike;
}

export class RenderSession {
  private worker: RenderWorkerLike | null = null;
  private reject: ((reason: unknown) => void) | null = null;

  constructor(private readonly factory: () => RenderWorkerLike = defaultWorkerFactory) {}

  /** Renderiza `source` e devolve HTML ainda não sanitizado. Uma chamada nova cancela a anterior. */
  render(source: string): Promise<string> {
    this.cancel();
    return new Promise<string>((resolve, reject) => {
      let worker: RenderWorkerLike;
      try {
        worker = this.factory();
      } catch (error) {
        reject(new RenderFailedError("workerCreate", text(error)));
        return;
      }
      this.worker = worker;
      this.reject = reject;
      worker.onmessage = (event) => {
        this.finish(worker);
        if (event.data.ok) resolve(event.data.html);
        else reject(new RenderFailedError("renderError", event.data.error ?? "sem mensagem"));
      };
      worker.onerror = (event) => {
        this.finish(worker);
        reject(new RenderFailedError("workerError", describeWorkerError(event)));
      };
      worker.onmessageerror = () => {
        this.finish(worker);
        reject(new RenderFailedError("messageError", "resposta do Worker não pôde ser lida"));
      };
      worker.postMessage({ source });
    });
  }

  /** Encerra o Worker em curso (se houver) e rejeita a renderização pendente. */
  cancel(): void {
    const reject = this.reject;
    const worker = this.worker;
    this.reject = null;
    this.worker = null;
    worker?.terminate();
    reject?.(new RenderCancelledError());
  }

  private finish(worker: RenderWorkerLike): void {
    if (this.worker === worker) {
      this.worker = null;
      this.reject = null;
    }
    worker.terminate();
  }
}
