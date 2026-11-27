// Catálogo único de textos da interface (FR-003, Princípio III). Chaves em inglês, textos em
// português do Brasil. Os textos literais da spec e as decisões de desenho estão em
// docs/specs/desktop-shell/data-model.md (§Catálogo de mensagens).

import type {
  Appearance,
  DataDirCheck,
  DataDirStatus,
  DownloadFailure,
  ErrorCode,
  FirstRunStep,
  FontSize,
  ImageStatus,
  OperationKind,
  OperationResult,
  OperationStage,
  RepoCheck,
  RepoStatus,
  RuntimeKind,
  RuntimeReport,
  RuntimeState,
  ServiceName,
  ServiceState,
  StepKind,
  StepState,
  Summary,
  Warning,
  WorkerExitReason,
  WorkerPool,
  WorkerPoolState,
} from "./types";

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

  // Códigos de erro da infraestrutura (contracts/tauri-commands.md, "Error Responses")
  infraError_operationInProgress: "Outra operação da infraestrutura já está em andamento. Aguarde ela terminar.",
  infraError_anotherInstance: "Outra janela do artigo2md já controla a infraestrutura. Use aquela janela para iniciar ou parar.",
  infraError_dockerNotReady: "O Docker ainda não está pronto. Abra o Docker Desktop, o OrbStack ou o colima e tente de novo.",
  infraError_repoInvalid: "A pasta do repositório do artigo2md não passou na verificação. Confira a pasta nas Configurações.",
  infraError_dataDirInvalid: "A pasta de dados não existe, não permite escrita ou não é válida. Escolha outra nas Configurações.",
  infraError_imagesMissing: "As imagens da infraestrutura ainda não foram preparadas. Prepare-as antes de iniciar.",
  infraError_confirmationRequired: "Confirme o download para continuar.",
  infraError_insufficientDisk: "Não há espaço livre suficiente no disco para esta operação.",
  infraError_invalidParams: "Os parâmetros da infraestrutura são inválidos. Revise as Configurações.",
  infraError_dockerUnavailable: "Não foi possível falar com o Docker. Confira se ele está aberto e funcionando.",
  infraError_portInUse: "Uma porta necessária já está em uso por outro programa. Libere a porta ou escolha outra nas Configurações.",
  infraError_serviceUnhealthy: "Um serviço da infraestrutura não ficou saudável a tempo. Veja os detalhes técnicos.",
  infraError_stopFailed: "Não foi possível parar a infraestrutura. Veja os detalhes técnicos e tente de novo.",
  infraError_scriptFailed: "O script de iniciar ou parar a infraestrutura terminou de forma inesperada. Veja os detalhes técnicos; se o problema continuar, confira se a pasta do repositório está íntegra e atualizada.",
  infraError_modelsMissing: "Os modelos de conversão ainda não foram preparados. Prepare-os antes de iniciar os workers.",
  infraError_stackNotRunning: "A infraestrutura não está em execução. Inicie-a antes de iniciar os workers.",
  infraError_workerExited: "Os workers terminaram sozinhos. Veja o motivo no painel.",
  infraError_downloadFailed: "O download falhou. Veja o motivo e tente de novo.",
  infraError_invalidStackSettings: "Algum valor das configurações da infraestrutura é inválido.",

  // --- Infraestrutura: navegação, indicador e painel (FR-025 a FR-029, FR-042) ---
  navInfra: "Infraestrutura",
  infraTitle: "Infraestrutura",
  infraIntro: "Aqui você acompanha o Docker, os serviços locais e os workers de conversão. Tudo roda só neste Mac.",
  infraIndicatorLabel: "Estado da infraestrutura",
  infraRegionLabel: "Painel da infraestrutura",
  infraStatusRegion: "Avisos da infraestrutura",
  recheck: "Verificar novamente",
  notCheckedYet: "ainda não verificado",
  readError: "Não foi possível ler o estado da infraestrutura agora. O app tenta de novo sozinho.",
  readOnlyNotice:
    "Outra janela do artigo2md controla a infraestrutura. Esta janela só mostra o estado; iniciar e parar ficam desativados.",
  sectionDocker: "Docker",
  sectionRepo: "Pasta do repositório",
  sectionDataDir: "Pasta de dados",
  sectionServices: "Serviços",
  sectionPool: "Workers de conversão",
  sectionActions: "Ações",
  addressLabel: "Endereço local",
  verifiedAt: "verificado às",
  openGuide: "Abrir o guia de instalação",
  showStartSteps: "Ver como iniciar",
  consumerStopped: "Consumidor de eventos parado",
  consumerStoppedHint: "A API está no ar, mas o consumidor de eventos dos workers está desconectado.",
  poolClosing: "Encerrando workers…",
  poolMemoryWarning:
    "O número de workers é maior do que a memória deste Mac comporta. Reduza-o em Configurações para evitar lentidão.",
  poolOrphanFound: "Foi encontrado um grupo de workers deixado por uma sessão anterior do app. O que fazer com ele?",
  poolAdopted: "Grupo de workers de uma sessão anterior, retomado sem mensagens.",
  poolStop: "Parar",
  poolKeep: "Continuar usando",
  showWorkerLines: "Ver mensagens dos workers",
  hideWorkerLines: "Ocultar mensagens dos workers",
  noWorkerLines: "Nenhuma mensagem dos workers ainda.",
  showTechnical: "Ver detalhes técnicos",
  hideTechnical: "Ocultar detalhes técnicos",
  noTechnical: "Não há detalhes técnicos desta operação.",
  technicalLabel: "Detalhes técnicos da última operação",
  startInfra: "Iniciar infraestrutura",
  stopInfra: "Parar infraestrutura",
  startWorkers: "Iniciar workers",
  stopWorkers: "Parar workers",
  restartWorkers: "Reiniciar workers",
  working: "Em andamento…",
  checking: "Verificando a infraestrutura…",
  dismissNotice: "Fechar aviso",
  stackStarted: "Infraestrutura no ar.",
  stackAlreadyRunning: "A infraestrutura já estava no ar.",
  stackStopped: "Infraestrutura parada.",
  stackAlreadyStopped: "A infraestrutura já estava parada.",
  workersStarted: "Workers em execução.",
  workersAlreadyRunning: "Os workers já estavam em execução.",
  workersStoppedNotice: "Workers parados.",
  workersAlreadyStopped: "Os workers já estavam parados.",
  imagesNeededForStart: "Antes de iniciar, é preciso preparar as imagens da infraestrutura. Veja a primeira execução abaixo.",
  modelsNeededForWorkers: "Os modelos de conversão ainda não foram preparados. Use “Preparar modelos” na primeira execução, abaixo.",

  // --- Primeira execução (FR-030 a FR-036) ---
  firstRunTitle: "Primeira execução",
  firstRunIntro:
    "Na primeira vez, é preciso baixar as imagens da infraestrutura e os modelos de conversão. A internet só é usada nesta etapa.",
  firstRunAllDone: "As imagens e os modelos já estão preparados. Nenhum download será pedido.",
  stepImages: "Imagens da infraestrutura",
  stepModels: "Modelos de conversão",
  approxSizeLabel: "Tamanho aproximado",
  locationLabel: "Onde ficam os arquivos",
  prepareImages: "Preparar imagens",
  prepareModels: "Preparar modelos",
  retryDownload: "Tentar de novo",
  resumeDownload: "Retomar",
  confirmDownload: "Baixar agora",
  cancelDownload: "Cancelar",
  confirmTitle: "Confirmar o download",
  progressNoResponse: "Sem resposta: nada mudou nos últimos segundos.",
  progressWaiting: "Aguardando o primeiro retorno do download…",
  lastUpdate: "última atualização às",

  // --- Guia de instalação do Docker (FR-004 a FR-006) ---
  guideTitle: "Guia de instalação do Docker",
  guideIntro:
    "O artigo2md precisa de um runtime Docker. Compare as três opções, escolha uma e siga os passos. Todo o conteúdo deste guia está dentro do app e funciona sem internet. Os endereços aparecem como texto para você copiar: o app não abre o navegador, não baixa e não executa nenhum instalador.",
  guideChoose: "Escolher",
  guideChosen: "Escolhido",
  guideBack: "Voltar",
  guideNext: "Próximo passo",
  guideFinish: "Concluir",
  guideChangeRuntime: "Escolher outro runtime",
  guideClose: "Fechar o guia",
  guideSource: "Fonte oficial",
  guideCopy: "Copiar",
  guideCopied: "Copiado",
  guideCopyFailed: "Não foi possível copiar. Selecione o texto e copie manualmente.",
  guideWatching: "O artigo2md confere sozinho, a cada poucos segundos, se o Docker ficou pronto.",
  guideReady: "Docker pronto. O guia terminou.",
  guideGoToInfra: "Ir para a infraestrutura",
  guideUnavailablePrefix: "Indisponível neste Mac",
  guideMacosNeeds: "exige macOS",
  guideMacosOrNewer: "ou mais novo",
  guideDockerSupportNote:
    "O Docker Desktop suporta a versão atual do macOS e as duas anteriores. Confira a página oficial do Docker Desktop.",
  guideHomebrewFollow: "Depois de instalar, siga a instrução que o próprio Homebrew mostra ao final para registrar o plugin do Compose.",

  // --- Configurações da infraestrutura (FR-013, FR-016, FR-019) ---
  stackSettingsTitle: "Infraestrutura",
  stackSettingsLoading: "Carregando as configurações da infraestrutura…",
  stackSettingsLoadFailed: "Não foi possível ler as configurações da infraestrutura.",
  stackSettingsEnvInvalid:
    "O arquivo de parâmetros da infraestrutura (stack/.env, na pasta do repositório) tem um valor inválido: uma chave repetida, o caractere “$” ou uma porta inválida. Corrija o arquivo e tente de novo.",
  stackSettingsRetry: "Tentar de novo",
  repoFolderLabel: "Pasta do repositório do artigo2md",
  dataDirLabel: "Pasta de dados",
  folderNotSet: "Nenhuma pasta indicada",
  chooseFolder: "Escolher pasta…",
  changeFolder: "Trocar pasta…",
  workersLabel: "Número de workers",
  workersHelp: "Número inteiro, maior ou igual a 1. Padrão: 4.",
  minFreeLabel: "Memória livre mínima (MB)",
  minFreeHelp: "Número inteiro, maior ou igual a 0. Padrão: 4822.",
  advancedSettings: "Configurações avançadas",
  portsHelp:
    "As portas valem na próxima vez que a infraestrutura for iniciada. Os serviços ficam acessíveis somente neste Mac (127.0.0.1).",
  portsDefaultsNote: "O arquivo de parâmetros ainda não existe: são mostradas as portas padrão e elas serão usadas na primeira inicialização.",
  portPostgres: "Porta do banco de dados (Postgres)",
  portRabbitmq: "Porta da fila de trabalhos (RabbitMQ)",
  portApi: "Porta da API",
  saveStackSettings: "Salvar",
  stackSettingsSaved: "Configurações da infraestrutura salvas.",
  workersInvalid: "O número de workers deve ser um número inteiro maior ou igual a 1.",
  minFreeInvalid: "A memória livre mínima deve ser um número inteiro maior ou igual a 0.",
  portInvalid: "deve ser um número inteiro entre 1 e 65535.",
  stackSettingsBusy: "Há uma operação da infraestrutura em andamento. Aguarde para trocar pastas ou salvar.",
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
    case "operationInProgress":
      return messages.infraError_operationInProgress;
    case "anotherInstance":
      return messages.infraError_anotherInstance;
    case "dockerNotReady":
      return messages.infraError_dockerNotReady;
    case "repoInvalid":
      return messages.infraError_repoInvalid;
    case "dataDirInvalid":
      return messages.infraError_dataDirInvalid;
    case "imagesMissing":
      return messages.infraError_imagesMissing;
    case "confirmationRequired":
      return messages.infraError_confirmationRequired;
    case "insufficientDisk":
      return messages.infraError_insufficientDisk;
    case "invalidParams":
      return messages.infraError_invalidParams;
    case "dockerUnavailable":
      return messages.infraError_dockerUnavailable;
    case "portInUse":
      return messages.infraError_portInUse;
    case "serviceUnhealthy":
      return messages.infraError_serviceUnhealthy;
    case "stopFailed":
      return messages.infraError_stopFailed;
    case "scriptFailed":
      return messages.infraError_scriptFailed;
    case "modelsMissing":
      return messages.infraError_modelsMissing;
    case "stackNotRunning":
      return messages.infraError_stackNotRunning;
    case "workerExited":
      return messages.infraError_workerExited;
    case "downloadFailed":
      return messages.infraError_downloadFailed;
    case "invalidStackSettings":
      return messages.infraError_invalidStackSettings;
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

// ---------------------------------------------------------------------------------------------
// Infraestrutura: tabelas por enum (o teste de 9.1.4 confere que cada valor tem texto em português)
// e funções de composição de frases.
// ---------------------------------------------------------------------------------------------

export const runtimeNames: Record<RuntimeKind, string> = {
  dockerDesktop: "Docker Desktop",
  orbstack: "OrbStack",
  colima: "colima",
};

export const runtimeStateLabels: Record<RuntimeState, string> = {
  notInstalled: "não instalado",
  stopped: "instalado, parado",
  running: "em execução",
};

export const serviceNames: Record<ServiceName, string> = {
  postgres: "Banco de dados (Postgres)",
  rabbitmq: "Fila de trabalhos (RabbitMQ)",
  api: "API do projeto",
};

/** Nome curto do serviço, usado dentro de frases ("A porta 5432 do Postgres…"). */
export const serviceShortNames: Record<ServiceName, string> = {
  postgres: "Postgres",
  rabbitmq: "RabbitMQ",
  api: "API",
};

export const serviceStateLabels: Record<ServiceState, string> = {
  stopped: "parado",
  starting: "iniciando",
  healthy: "saudável",
  unhealthy: "com problema",
};

export const poolStateLabels: Record<WorkerPoolState, string> = {
  stopped: "parado",
  starting: "iniciando",
  running: "em execução",
  stopping: "encerrando",
  crashed: "parado de forma inesperada",
  orphanFound: "grupo de uma sessão anterior encontrado",
};

export const workerExitReasonMessages: Record<WorkerExitReason, string> = {
  stopped: "Os workers foram encerrados normalmente.",
  failure: "Os workers terminaram por uma falha. Veja as últimas mensagens dos workers.",
  usage: "Os workers recusaram as opções recebidas. Confira se a pasta do repositório está atualizada.",
  queueExtraMissing:
    "Faltam componentes da fila de conversão no ambiente do repositório. Atualize esse ambiente fora do app e tente de novo.",
  credentials:
    "O RabbitMQ recusou as credenciais dos workers, ou elas estão ausentes. Confira o arquivo de parâmetros da infraestrutura.",
  topology: "A configuração da fila é incompatível com a versão dos workers. Atualize o repositório e tente de novo.",
  killed: "Os workers foram encerrados à força pelo sistema.",
  unknown: "Os workers de uma sessão anterior não estão mais em execução.",
};

export const summaryLabels: Record<Summary, string> = {
  healthy: "saudável",
  partial: "parcial",
  stopped: "parada",
};

export const stepNames: Record<StepKind, string> = {
  images: messages.stepImages,
  models: messages.stepModels,
};

export const stepStateLabels: Record<StepState, string> = {
  pending: "pendente",
  downloading: "baixando",
  done: "concluída",
  failed: "falhou",
};

export const stepItemNames: Record<StepKind, string> = { images: "imagens", models: "modelos" };

export const downloadFailureMessages: Record<DownloadFailure, string> = {
  noNetwork: "Sem conexão com a internet. Conecte-se e tente de novo.",
  diskFull: "O disco ficou cheio durante o download. Libere espaço e tente de novo.",
  interrupted: "O download foi interrompido. Tente de novo para retomar.",
  other: "O download falhou por um motivo não identificado. Veja os detalhes técnicos e tente de novo.",
};

export const operationKindLabels: Record<OperationKind, string> = {
  startStack: "Iniciando a infraestrutura",
  stopStack: "Parando a infraestrutura",
  startWorkers: "Iniciando os workers",
  stopWorkers: "Parando os workers",
  prepareImages: "Preparando as imagens da infraestrutura",
  prepareModels: "Preparando os modelos de conversão",
};

export const operationStageMessages: Record<OperationStage, string> = {
  checkingRepo: "Conferindo a pasta do repositório…",
  checkingDataDir: "Conferindo a pasta de dados…",
  ensuringParams: "Preparando os parâmetros da infraestrutura…",
  validatingParams: "Validando os parâmetros…",
  checkingImages: "Conferindo as imagens…",
  pullingImages: "Baixando as imagens da infraestrutura…",
  buildingApi: "Preparando a imagem da API…",
  runningUp: "Iniciando os serviços e aguardando ficarem saudáveis…",
  runningDown: "Parando os serviços…",
  startingWorkers: "Iniciando os workers…",
  stoppingWorkers: "Encerrando os workers com calma…",
  forcingStop: "Forçando o encerramento dos workers…",
  checkingDisk: "Conferindo o espaço livre em disco…",
  downloadingModels: "Baixando os modelos de conversão…",
};

const FAILED_REPO_LIST_LIMIT = 6;

function joinMissing(missing: string[]): string {
  const shown = missing.slice(0, FAILED_REPO_LIST_LIMIT).join(", ");
  return missing.length > FAILED_REPO_LIST_LIMIT ? `${shown} e mais ${missing.length - FAILED_REPO_LIST_LIMIT}` : shown;
}

export function repoMessage(repo: RepoCheck): string {
  const missing = repo.missing.length > 0 ? ` Faltam: ${joinMissing(repo.missing)}.` : "";
  const table: Record<RepoStatus, string> = {
    ok: "A pasta do repositório foi verificada e está pronta.",
    notSet: "Nenhuma pasta do repositório foi indicada. Indique-a em Configurações.",
    missingFolder: "A pasta do repositório indicada não existe mais. Indique-a de novo em Configurações.",
    missingStackFiles: `A pasta indicada não tem os arquivos da infraestrutura (compose e scripts).${missing}`,
    missingProjectFiles: `A pasta indicada não tem os arquivos do projeto necessários para os workers.${missing}`,
    uvNotFound: "O programa uv não foi encontrado neste Mac. Instale-o fora do app e verifique de novo.",
    uvEnvBroken:
      "O ambiente do uv da pasta indicada não está pronto para iniciar os workers. Prepare-o fora do app e verifique de novo.",
    modelsUnsupported:
      "A pasta do repositório indicada tem uma versão incompatível com este app: falta o comando de preparação de modelos ou o contrato mudou. Atualize o repositório e verifique de novo.",
  };
  return table[repo.status];
}

export const dataDirStatusMessages: Record<DataDirStatus, string> = {
  ok: "A pasta de dados está pronta.",
  missing: "A pasta de dados não existe e não foi possível criá-la. Escolha outra em Configurações.",
  notDirectory: "O caminho da pasta de dados não é uma pasta. Escolha outra em Configurações.",
  notWritable: "Sem permissão de escrita na pasta de dados. Escolha outra em Configurações.",
  notCanonical: "O caminho da pasta de dados passa por um atalho (link simbólico). Escolha outra em Configurações.",
  forbidden: "Esta pasta é ampla demais para guardar os dados. Escolha outra em Configurações.",
};

export function dataDirMessage(check: DataDirCheck): string {
  return dataDirStatusMessages[check.status];
}

/** Situação geral do runtime (FR-002), em frases para pessoa não técnica. */
export function runtimeMessage(report: RuntimeReport): string {
  switch (report.overall) {
    case "ready":
      return report.inUse
        ? `Docker pronto. Em uso: ${runtimeNames[report.inUse]}.`
        : "Docker pronto.";
    case "noRuntime":
      return "Nenhum runtime Docker foi encontrado neste Mac.";
    case "stopped": {
      const installed = report.runtimes.filter((r) => r.state === "stopped").map((r) => runtimeNames[r.kind]);
      return installed.length > 0
        ? `${installed.join(" e ")} ${installed.length > 1 ? "estão instalados" : "está instalado"}, mas não ${installed.length > 1 ? "estão" : "está"} em execução. Abra-${installed.length > 1 ? "os" : "o"} para continuar.`
        : "O runtime Docker está instalado, mas não está em execução.";
    }
    case "composeMissing":
      return "O Docker está em execução, mas falta o Docker Compose. Sem ele, não é possível iniciar a infraestrutura.";
    case "ambiguous":
      return "Mais de um runtime Docker está em execução e não foi possível saber qual está em uso. Feche os que não for usar e verifique de novo.";
  }
}

export function summaryText(summary: Summary | null): string {
  return summary === null ? "verificando" : summaryLabels[summary];
}

/** Texto visível do indicador da barra lateral: nunca só cor (FR-042 c). */
export function summaryIndicatorText(summary: Summary | null): string {
  return `${messages.navInfra}: ${summaryText(summary)}`;
}

/** Rótulo acessível do indicador (VoiceOver). */
export function summaryAriaLabel(summary: Summary | null): string {
  return `${messages.infraIndicatorLabel}: ${summaryText(summary)}`;
}

export function serviceAriaLabel(service: ServiceName, state: ServiceState): string {
  return `${serviceNames[service]}: ${serviceStateLabels[state]}`;
}

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/** Volume legível em português (separador decimal vírgula; unidades de 1000, como o texto "cerca de 3 GB"). */
export function formatBytes(bytes: number): string {
  let value = Math.max(0, bytes);
  let unit = 0;
  while (value >= 1000 && unit < BYTE_UNITS.length - 1) {
    value /= 1000;
    unit += 1;
  }
  const text = unit === 0 ? String(Math.round(value)) : value.toLocaleString("pt-BR", { maximumFractionDigits: 1 });
  return `${text} ${BYTE_UNITS[unit]}`;
}

/** Hora local (pt-BR) de um instante ISO 8601; vazio ou inválido vira "ainda não verificado". */
export function formatTime(iso: string | null): string {
  if (!iso) return messages.notCheckedYet;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return messages.notCheckedYet;
  return date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/**
 * Mensagem principal do resultado de uma operação. Cita serviço, porta, espaço que falta e motivo
 * do download quando o retrato os traz; nunca devolve texto técnico em inglês.
 */
export function operationResultMessage(result: OperationResult): string {
  if (result.ok) return "";
  const code = result.code ?? "scriptFailed";
  const service = result.service as ServiceName | null;
  const name = service && service in serviceShortNames ? serviceShortNames[service] : null;
  switch (code) {
    case "portInUse":
      return result.port !== null && name
        ? `A porta ${result.port} do ${name} está em uso por outro programa. Libere a porta ou troque-a em Configurações, na área avançada.`
        : messages.infraError_portInUse;
    case "serviceUnhealthy":
      return name
        ? `O serviço ${name} não ficou saudável a tempo. Veja os detalhes técnicos.`
        : messages.infraError_serviceUnhealthy;
    case "insufficientDisk":
      return result.shortfallBytes !== null
        ? `Faltam cerca de ${formatBytes(result.shortfallBytes)} de espaço livre em disco. Libere espaço e tente de novo.`
        : messages.infraError_insufficientDisk;
    case "downloadFailed":
      return result.failure ? downloadFailureMessages[result.failure] : messages.infraError_downloadFailed;
    default:
      return errorMessage(code, "document");
  }
}

/** Frase de progresso de um download (FR-032): percentual, ou volume + itens + hora, ou espera. */
export function progressText(step: FirstRunStep): string {
  const items = `${step.itemsDone} de ${step.itemsTotal} ${stepItemNames[step.step]}`;
  if (step.totalBytes !== null && step.totalBytes > 0 && step.downloadedBytes !== null) {
    const percent = Math.min(100, Math.floor((step.downloadedBytes / step.totalBytes) * 100));
    return `${percent}% concluído (${formatBytes(step.downloadedBytes)} de ${formatBytes(step.totalBytes)}) · ${items}`;
  }
  if (step.downloadedBytes === null && step.updatedAt === null) return `${messages.progressWaiting} ${items}`;
  const parts = [step.downloadedBytes !== null ? `${formatBytes(step.downloadedBytes)} baixados` : null, items];
  if (step.updatedAt) parts.push(`${messages.lastUpdate} ${formatTime(step.updatedAt)}`);
  return parts.filter(Boolean).join(" · ");
}

/** Confirmação em português antes de qualquer download (FR-031). */
export function downloadConfirmText(step: FirstRunStep): string {
  return `Para baixar ${stepNames[step.step].toLowerCase()} é preciso estar conectado à internet, só nesta etapa. ${messages.approxSizeLabel}: ${step.approxSize}. ${messages.locationLabel}: ${step.location}. Deseja baixar agora?`;
}

export function poolAriaLabel(pool: WorkerPool): string {
  return `${messages.sectionPool}: ${poolStateLabels[pool.state]}`;
}
