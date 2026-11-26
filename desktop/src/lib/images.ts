// Fila de imagens do visualizador: resolve cada marcador `data-md-image` com concorrência
// limitada, mostra o aviso por status e revoga os `blob:` ao trocar de documento (tarefa 4.2.4).

import { decodeReference } from "./refs";
import { imageStatusMessage } from "./messages";
import type { AppError, ImageStatus } from "./types";

export type ReadImage = (documentPath: string, src: string) => Promise<ArrayBuffer>;

export interface ImageLoaderOptions {
  documentPath: string;
  readImage: ReadImage;
  concurrency?: number;
  createObjectUrl?: (blob: Blob) => string;
  revokeObjectUrl?: (url: string) => void;
}

const MIME_BY_EXTENSION: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
};

function mimeFor(path: string): string {
  const ext = path.split(/[?#]/, 1)[0]!.split(".").pop()?.toLowerCase() ?? "";
  return MIME_BY_EXTENSION[ext] ?? "application/octet-stream";
}

export function statusFromError(error: unknown): ImageStatus {
  const code = (error as Partial<AppError> | null)?.code;
  switch (code) {
    case "notFound":
      return "notFound";
    case "outsideFolder":
      return "outsideFolder";
    case "unsupportedFormat":
    case "notMarkdown":
      return "unsupportedFormat";
    case "tooLarge":
      return "tooLarge";
    default:
      return "readError"; // permissionDenied, io e qualquer outro
  }
}

export class ImageLoader {
  readonly concurrency: number;
  private readonly queue: HTMLElement[] = [];
  private readonly urls = new Set<string>();
  private active = 0;
  private disposed = false;
  private readonly createUrl: (blob: Blob) => string;
  private readonly revokeUrl: (url: string) => void;

  constructor(private readonly options: ImageLoaderOptions) {
    this.concurrency = Math.max(1, options.concurrency ?? 4);
    this.createUrl = options.createObjectUrl ?? ((blob) => URL.createObjectURL(blob));
    this.revokeUrl = options.revokeObjectUrl ?? ((url) => URL.revokeObjectURL(url));
  }

  /**
   * Procura marcadores em `root`. A classificação vem do renderer (`data-kind`): remoto e absoluto são
   * resolvidos sem IPC; só `local` entra na fila. Marcador sem `data-kind` reconhecido falha fechado
   * (tratado como remoto bloqueado).
   */
  attach(root: ParentNode): void {
    for (const marker of Array.from(root.querySelectorAll<HTMLElement>("[data-md-image]"))) {
      const kind = marker.dataset.kind;
      if (kind === "outsideFolder") {
        this.showStatus(marker, "outsideFolder");
      } else if (kind === "local") {
        if ((marker.dataset.src ?? "") === "") {
          this.showStatus(marker, "notFound");
        } else {
          this.showStatus(marker, "loading");
          this.queue.push(marker);
        }
      } else {
        this.showStatus(marker, "remoteBlocked");
      }
    }
    this.pump();
  }

  /** Cancela a fila e revoga todos os `blob:` criados. */
  dispose(): void {
    this.disposed = true;
    this.queue.length = 0;
    for (const url of this.urls) this.revokeUrl(url);
    this.urls.clear();
  }

  private pump(): void {
    while (!this.disposed && this.active < this.concurrency && this.queue.length > 0) {
      const marker = this.queue.shift()!;
      this.active += 1;
      void this.load(marker).finally(() => {
        this.active -= 1;
        this.pump();
      });
    }
  }

  private async load(marker: HTMLElement): Promise<void> {
    const src = decodeReference(marker.dataset.src ?? "");
    try {
      const bytes = await this.options.readImage(this.options.documentPath, src);
      if (this.disposed) return;
      const url = this.createUrl(new Blob([bytes], { type: mimeFor(src) }));
      this.urls.add(url);
      const img = document.createElement("img");
      img.alt = marker.dataset.alt ?? "";
      img.className = "md-image-loaded";
      img.src = url;
      marker.replaceChildren(img);
      marker.dataset.status = "loaded";
    } catch (error) {
      if (this.disposed) return;
      this.showStatus(marker, statusFromError(error));
    }
  }

  private showStatus(marker: HTMLElement, status: ImageStatus): void {
    marker.dataset.status = status;
    const alt = marker.dataset.alt ?? "";
    const notice = document.createElement("span");
    notice.className = "image-notice";
    notice.setAttribute("role", status === "loading" ? "status" : "note");
    notice.textContent = alt ? `${alt}: ${imageStatusMessage(status)}` : imageStatusMessage(status);
    marker.replaceChildren(notice);
  }
}
