import { describe, expect, it } from "vitest";
import {
  RenderCancelledError,
  RenderFailedError,
  RenderSession,
  describeWorkerError,
  type RenderRequest,
  type RenderResponse,
  type RenderWorkerLike,
} from "../src/lib/render";

class FakeWorker implements RenderWorkerLike {
  onmessage: ((event: { data: RenderResponse }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessageerror: ((event: unknown) => void) | null = null;
  posted: RenderRequest[] = [];
  terminated = false;
  postMessage(message: RenderRequest): void {
    this.posted.push(message);
  }
  terminate(): void {
    this.terminated = true;
  }
  reply(response: RenderResponse): void {
    this.onmessage?.({ data: response });
  }
}

describe("RenderSession (tarefa 4.2.4)", () => {
  it("devolve o HTML do Worker e o encerra", async () => {
    const workers: FakeWorker[] = [];
    const session = new RenderSession(() => {
      const w = new FakeWorker();
      workers.push(w);
      return w;
    });
    const promise = session.render("# oi");
    expect(workers[0]!.posted).toEqual([{ source: "# oi" }]);
    workers[0]!.reply({ ok: true, html: "<h1>oi</h1>" });
    await expect(promise).resolves.toBe("<h1>oi</h1>");
    expect(workers[0]!.terminated).toBe(true);
  });

  it("trocar de documento encerra o Worker em curso e rejeita a renderização antiga", async () => {
    const workers: FakeWorker[] = [];
    const session = new RenderSession(() => {
      const w = new FakeWorker();
      workers.push(w);
      return w;
    });
    const first = session.render("a");
    const second = session.render("b");
    expect(workers[0]!.terminated).toBe(true);
    await expect(first).rejects.toBeInstanceOf(RenderCancelledError);
    workers[1]!.reply({ ok: true, html: "b" });
    await expect(second).resolves.toBe("b");
  });

  it("cancel encerra o Worker pendente", async () => {
    const w = new FakeWorker();
    const session = new RenderSession(() => w);
    const promise = session.render("a");
    session.cancel();
    expect(w.terminated).toBe(true);
    await expect(promise).rejects.toBeInstanceOf(RenderCancelledError);
  });

  it("falha do Worker ou da criação vira rejeição", async () => {
    const w = new FakeWorker();
    const session = new RenderSession(() => w);
    const promise = session.render("a");
    w.onerror?.(new Event("error"));
    await expect(promise).rejects.toThrow();

    const broken = new RenderSession(() => {
      throw new Error("sem Worker");
    });
    await expect(broken.render("a")).rejects.toThrow("sem Worker");

    const w2 = new FakeWorker();
    const failing = new RenderSession(() => w2);
    const p2 = failing.render("a");
    w2.reply({ ok: false });
    await expect(p2).rejects.toThrow();
  });

  it("a falha leva o detalhe técnico do erro do Worker (tarefa 9.1.2, research R9.12)", async () => {
    // Mensagem que o WebKit dá a um Worker clássico com `import` ESM (research R9.12).
    const syntax = {
      message: "SyntaxError: Unexpected token '{'. import call expects one or two arguments.",
      filename: "http://localhost:1420/src/lib/render.worker.ts?worker_file&type=classic",
      lineno: 3,
      colno: 0,
    };
    const w = new FakeWorker();
    const p = new RenderSession(() => w).render("a");
    w.onerror?.(syntax);
    const error = await p.catch((e: unknown) => e);
    expect(error).toBeInstanceOf(RenderFailedError);
    expect((error as RenderFailedError).stage).toBe("workerError");
    expect((error as RenderFailedError).detail).toBe(
      "workerError: SyntaxError: Unexpected token '{'. import call expects one or two arguments. " +
        "(http://localhost:1420/src/lib/render.worker.ts?worker_file&type=classic:3:0)",
    );
    expect(w.terminated).toBe(true);

    const w2 = new FakeWorker();
    const p2 = new RenderSession(() => w2).render("a");
    w2.reply({ ok: false, error: "TypeError: x is not a function" });
    await expect(p2).rejects.toMatchObject({ stage: "renderError", detail: "renderError: TypeError: x is not a function" });

    const w3 = new FakeWorker();
    const p3 = new RenderSession(() => w3).render("a");
    w3.onmessageerror?.(new Event("messageerror"));
    await expect(p3).rejects.toMatchObject({ stage: "messageError" });

    const created = new RenderSession(() => {
      throw new TypeError("Worker is not a constructor");
    });
    await expect(created.render("a")).rejects.toMatchObject({
      stage: "workerCreate",
      detail: "workerCreate: TypeError: Worker is not a constructor",
    });
  });

  it("describeWorkerError tolera eventos sem campos e corta detalhe longo", () => {
    expect(describeWorkerError(new Event("error"))).toBe("sem mensagem");
    expect(describeWorkerError(null)).toBe("evento de erro sem detalhes");
    const long = new RenderFailedError("workerError", "x".repeat(1000));
    expect(long.detail.length).toBeLessThan(330);
    expect(long.detail.endsWith("…")).toBe(true);
  });
});
