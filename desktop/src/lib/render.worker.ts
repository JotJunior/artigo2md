// Web Worker de renderização: só produz HTML (markdown-it + KaTeX). Nunca toca no DOM; a
// sanitização com DOMPurify acontece no thread principal (plan.md, Endurecimento de segurança).

import { renderMarkdown } from "./markdown";
import type { RenderRequest, RenderResponse } from "./render";

interface WorkerScope {
  onmessage: ((event: { data: RenderRequest }) => void) | null;
  postMessage(message: RenderResponse): void;
}

const scope = self as unknown as WorkerScope;

scope.onmessage = (event) => {
  try {
    scope.postMessage({ ok: true, html: renderMarkdown(event.data.source) });
  } catch (error) {
    // Só a mensagem técnica da exceção, para o detalhe do aviso na interface (tarefa 9.1.2).
    const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
    scope.postMessage({ ok: false, error: message });
  }
};
