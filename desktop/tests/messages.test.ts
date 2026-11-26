import { describe, expect, it } from "vitest";
import {
  appearanceLabels,
  errorMessage,
  fontSizeLabels,
  imageStatusMessage,
  messages,
  warningMessage,
  type ErrorContext,
} from "../src/lib/messages";
import { ERROR_CODES, type ImageStatus } from "../src/lib/types";

describe("catálogo de mensagens (SC-006)", () => {
  it("nenhuma chave tem texto vazio", () => {
    for (const [key, text] of Object.entries(messages)) {
      expect(text.trim(), `chave ${key}`).not.toBe("");
    }
  });

  it("mantém os textos literais da spec", () => {
    expect(messages.lastFolderUnavailable).toBe("A última pasta aberta não está mais disponível");
    expect(messages.permissionDenied).toBe("Sem permissão para ler esta pasta");
    expect(messages.unsupportedFile).toBe("Este tipo de arquivo não pode ser visualizado");
    expect(messages.imageNotFound).toBe("Imagem não encontrada");
    expect(messages.imageRemoteBlocked).toBe("Imagem remota bloqueada");
    expect(messages.imageOutsideFolder).toBe("Imagem fora da pasta aberta");
    expect(messages.imageUnsupportedFormat).toBe("Formato de imagem não suportado");
    expect(messages.fileNotFound).toBe("Arquivo não encontrado");
    expect(messages.linkOutsideFolder).toBe("Link fora da pasta aberta");
    expect(messages.largeFile).toBe("Arquivo grande: a renderização pode demorar");
    expect(messages.notText).toBe("Não foi possível ler este arquivo como texto");
    expect(messages.settingsReset).toBe(
      "Não foi possível ler as configurações salvas; os padrões foram restaurados",
    );
    expect(messages.openFolder).toBe("Abrir pasta…");
    expect(messages.restoreDefaults).toBe("Restaurar padrões");
    expect(messages.reload).toBe("Recarregar");
  });

  it("todo código de erro tem texto em qualquer contexto", () => {
    const contexts: ErrorContext[] = ["folder", "document", "image", "link", "settings"];
    for (const code of ERROR_CODES) {
      for (const context of contexts) {
        expect(errorMessage(code, context).trim(), `${code}/${context}`).not.toBe("");
      }
    }
  });

  it("escolhe o texto pelo contexto (imagem x documento x link)", () => {
    expect(errorMessage("notFound", "image")).toBe(messages.imageNotFound);
    expect(errorMessage("notFound", "document")).toBe(messages.fileNotFound);
    expect(errorMessage("outsideFolder", "image")).toBe(messages.imageOutsideFolder);
    expect(errorMessage("outsideFolder", "link")).toBe(messages.linkOutsideFolder);
    expect(errorMessage("tooLarge", "image")).toBe(messages.imageTooLarge);
    expect(errorMessage("tooLarge", "document")).toBe(messages.fileTooLarge);
    expect(errorMessage("notMarkdown")).toBe(messages.unsupportedFile);
    expect(errorMessage("notText")).toBe(messages.notText);
    expect(errorMessage("invalidSettings", "settings")).toBe(messages.invalidSettings);
  });

  it("todo status de imagem, exceto loaded, tem aviso", () => {
    const statuses: ImageStatus[] = [
      "loading",
      "remoteBlocked",
      "notFound",
      "outsideFolder",
      "unsupportedFormat",
      "tooLarge",
      "readError",
    ];
    for (const status of statuses) {
      expect(imageStatusMessage(status).trim(), status).not.toBe("");
    }
    expect(imageStatusMessage("loaded")).toBe("");
  });

  it("avisos de inicialização e rótulos de configuração têm texto", () => {
    expect(warningMessage("settingsReset")).toBe(messages.settingsReset);
    expect(warningMessage("lastFolderUnavailable")).toBe(messages.lastFolderUnavailable);
    expect(Object.values(appearanceLabels).every((v) => v.length > 0)).toBe(true);
    expect(Object.values(fontSizeLabels).every((v) => v.length > 0)).toBe(true);
  });
});
