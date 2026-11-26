// Catálogo único de textos da interface (FR-003, Princípio III). Chaves em inglês, textos em
// português do Brasil. Os textos literais da spec e as decisões de desenho estão em
// docs/specs/desktop-shell/data-model.md (§Catálogo de mensagens).

import type { Appearance, ErrorCode, FontSize, ImageStatus, Warning } from "./types";

export const messages = {
  // Navegação e ações
  navFiles: "Arquivos",
  navSettings: "Configurações",
  openFolder: "Abrir pasta…",
  reload: "Recarregar",
  restoreDefaults: "Restaurar padrões",
  collapseSidebar: "Recolher barra lateral",
  expandSidebar: "Expandir barra lateral",
  resizeSidebar: "Ajustar largura da barra lateral",
  expandFolder: "Expandir pasta",
  collapseFolder: "Recolher pasta",
  dismiss: "Fechar aviso",
  sidebarLabel: "Barra lateral",
  fileTreeLabel: "Árvore de arquivos",
  viewerLabel: "Visualizador de documento",

  // Avisos e erros com texto literal da spec
  lastFolderUnavailable: "A última pasta aberta não está mais disponível",
  permissionDenied: "Sem permissão para ler esta pasta",
  unsupportedFile: "Este tipo de arquivo não pode ser visualizado",
  imageNotFound: "Imagem não encontrada",
  imageRemoteBlocked: "Imagem remota bloqueada",
  imageOutsideFolder: "Imagem fora da pasta aberta",
  imageUnsupportedFormat: "Formato de imagem não suportado",
  fileNotFound: "Arquivo não encontrado",
  linkOutsideFolder: "Link fora da pasta aberta",
  largeFile: "Arquivo grande: a renderização pode demorar",
  notText: "Não foi possível ler este arquivo como texto",
  settingsReset: "Não foi possível ler as configurações salvas; os padrões foram restaurados",

  // Erros sem texto literal na spec (decisões de projeto, tarefa 2.1.1)
  fileTooLarge: "Arquivo grande demais para ser aberto (limite de 50 MB)",
  imageTooLarge: "Imagem grande demais para ser exibida (limite de 20 MB)",
  invalidSettings: "Configuração inválida; nenhuma alteração foi salva",
  ioError: "Não foi possível concluir a operação por um erro de leitura ou gravação",
  imageReadError: "Não foi possível ler a imagem",
  folderUnavailable: "A pasta não está disponível",
  renderError: "Não foi possível renderizar este documento; ele é mostrado como texto",
  // Rótulo do detalhe técnico mostrado junto do aviso acima, para diagnóstico (tarefa 9.1.2)
  technicalDetail: "Detalhe técnico",

  // Estados de interface (tarefas 2.1.2 e 2.1.3)
  noFolderOpen: "Nenhuma pasta aberta",
  emptyFolder: "Esta pasta não tem arquivos visíveis",
  noFileSelected: "Selecione um arquivo Markdown na árvore para visualizá-lo",
  loadingTree: "Carregando…",
  loadingDocument: "Carregando documento…",
  loadingImage: "Carregando imagem…",

  // Rótulos de controle (tarefa 2.1.4)
  appearanceLabel: "Aparência",
  appearanceSystem: "Seguir o sistema",
  appearanceLight: "Claro",
  appearanceDark: "Escuro",
  fontSizeLabel: "Tamanho do texto",
  fontSizeSmall: "Pequeno",
  fontSizeMedium: "Médio",
  fontSizeLarge: "Grande",
  symlinkBadge: "Atalho",
  symlinkNotFollowed: "Atalho não expandido",
  externalLinkInactive: "Links externos estão desativados",
} as const;

export type MessageKey = keyof typeof messages;

export function t(key: MessageKey): string {
  return messages[key];
}

/** Contexto em que o erro ocorreu: muda o texto de `notFound`, `outsideFolder` e `tooLarge`. */
export type ErrorContext = "folder" | "document" | "image" | "link" | "settings";

/** Texto em português para um código de erro (contracts/commands.md §Erros). */
export function errorMessage(code: ErrorCode, context: ErrorContext = "document"): string {
  switch (code) {
    case "noFolderOpen":
      return messages.noFolderOpen;
    case "folderUnavailable":
      return context === "folder" ? messages.folderUnavailable : messages.lastFolderUnavailable;
    case "permissionDenied":
      return messages.permissionDenied;
    case "notFound":
      return context === "image" ? messages.imageNotFound : messages.fileNotFound;
    case "outsideFolder":
      return context === "image" ? messages.imageOutsideFolder : messages.linkOutsideFolder;
    case "notMarkdown":
      return messages.unsupportedFile;
    case "notText":
      return messages.notText;
    case "unsupportedFormat":
      return messages.imageUnsupportedFormat;
    case "tooLarge":
      return context === "image" ? messages.imageTooLarge : messages.fileTooLarge;
    case "invalidSettings":
      return messages.invalidSettings;
    case "io":
      return context === "image" ? messages.imageReadError : messages.ioError;
  }
}

/** Aviso mostrado no lugar da imagem, por status (data-model.md §ImageReference). */
export function imageStatusMessage(status: ImageStatus): string {
  switch (status) {
    case "loading":
      return messages.loadingImage;
    case "loaded":
      return "";
    case "remoteBlocked":
      return messages.imageRemoteBlocked;
    case "notFound":
      return messages.imageNotFound;
    case "outsideFolder":
      return messages.imageOutsideFolder;
    case "unsupportedFormat":
      return messages.imageUnsupportedFormat;
    case "tooLarge":
      return messages.imageTooLarge;
    case "readError":
      return messages.imageReadError;
  }
}

export function warningMessage(warning: Warning): string {
  return warning === "settingsReset" ? messages.settingsReset : messages.lastFolderUnavailable;
}

export const appearanceLabels: Record<Appearance, string> = {
  system: messages.appearanceSystem,
  light: messages.appearanceLight,
  dark: messages.appearanceDark,
};

export const fontSizeLabels: Record<FontSize, string> = {
  small: messages.fontSizeSmall,
  medium: messages.fontSizeMedium,
  large: messages.fontSizeLarge,
};
