// Espelho dos contratos de docs/specs/desktop-shell/contracts/commands.md e data-model.md.
// Nomes de campo em camelCase, exatamente como o Rust serializa (serde rename_all).

export const ERROR_CODES = [
  "noFolderOpen",
  "folderUnavailable",
  "permissionDenied",
  "notFound",
  "outsideFolder",
  "notMarkdown",
  "notText",
  "unsupportedFormat",
  "tooLarge",
  "invalidSettings",
  "io",
  "operationInProgress",
  "anotherInstance",
  "dockerNotReady",
  "repoInvalid",
  "dataDirInvalid",
  "imagesMissing",
  "confirmationRequired",
  "insufficientDisk",
  "invalidParams",
  "dockerUnavailable",
  "portInUse",
  "serviceUnhealthy",
  "stopFailed",
  "scriptFailed",
  "modelsMissing",
  "stackNotRunning",
  "workerExited",
  "downloadFailed",
  "invalidStackSettings",
] as const;
export type ErrorCode = (typeof ERROR_CODES)[number];

/** Erro devolvido pela rejeição do `invoke`; `detail` é sempre `null` na interface. */
export interface AppError {
  code: ErrorCode;
  detail: null;
}

export const APPEARANCES = ["system", "light", "dark"] as const;
export type Appearance = (typeof APPEARANCES)[number];

export const FONT_SIZES = ["small", "medium", "large"] as const;
export type FontSize = (typeof FONT_SIZES)[number];

export const SIDEBAR_WIDTH_MIN = 200;
export const SIDEBAR_WIDTH_MAX = 480;
export const SIDEBAR_WIDTH_DEFAULT = 260;

/**
 * Configurações trocadas com o Rust: entrada de `save_settings` e retorno de `get_startup_state`,
 * `save_settings` e `reset_settings`. Não há `lastFolder`: o caminho absoluto fica só no Rust
 * (plan.md, "Fronteira de confiança").
 */
export interface UserSettings {
  schemaVersion: 1;
  appearance: Appearance;
  viewerFontSize: FontSize;
  sidebarCollapsed: boolean;
  sidebarWidth: number;
}

export type Warning = "settingsReset" | "lastFolderUnavailable";

export interface OpenFolderInfo {
  displayName: string;
}

export interface StartupState {
  settings: UserSettings;
  warnings: Warning[];
  folder: OpenFolderInfo | null;
}

export interface OpenFolderResult {
  folder: OpenFolderInfo | null;
}

export type EntryKind = "directory" | "file" | "symlink";
export type Access = "ok" | "denied";
export type SymlinkTarget = "insideFile" | "insideDirectory" | "outside" | "broken";

export interface TreeEntry {
  name: string;
  relativePath: string;
  kind: EntryKind;
  access: Access;
  isMarkdown: boolean;
  symlinkTarget: SymlinkTarget | null;
}

export interface ListDirResult {
  entries: TreeEntry[];
}

export interface MarkdownDocument {
  relativePath: string;
  content: string;
  sizeBytes: number;
  isLarge: boolean;
}

export interface ResolveLinkResult {
  relativePath: string;
}

export type ImageStatus =
  | "loading"
  | "loaded"
  | "remoteBlocked"
  | "notFound"
  | "outsideFolder"
  | "unsupportedFormat"
  | "tooLarge"
  | "readError";

/** Telas da barra lateral. */
export type View = "files" | "settings" | "infra";

// ---------------------------------------------------------------------------------------------
// Infraestrutura (desktop-stack-control): espelho de data-model.md e de
// contracts/tauri-commands.md. Os nomes e valores seguem exatamente a serialização do Rust
// (serde camelCase); `tests/infra.test.ts` confere a paridade lendo o código-fonte do Rust e a
// fixture gerada por ele, sem listas escritas à mão como única fonte.
// ---------------------------------------------------------------------------------------------

export const RUNTIME_KINDS = ["dockerDesktop", "orbstack", "colima"] as const;
export type RuntimeKind = (typeof RUNTIME_KINDS)[number];

export const RUNTIME_STATES = ["notInstalled", "stopped", "running"] as const;
export type RuntimeState = (typeof RUNTIME_STATES)[number];

export const RUNTIME_OVERALLS = ["ready", "noRuntime", "stopped", "composeMissing", "ambiguous"] as const;
export type RuntimeOverall = (typeof RUNTIME_OVERALLS)[number];

export interface RuntimeEntry {
  kind: RuntimeKind;
  state: RuntimeState;
  context: string | null;
}

export interface RuntimeReport {
  runtimes: RuntimeEntry[];
  inUse: RuntimeKind | null;
  overall: RuntimeOverall;
  dockerPath: string | null;
  /** ISO 8601; texto vazio no retrato inicial, antes da primeira verificação. */
  checkedAt: string;
}

export const REPO_STATUSES = [
  "ok",
  "notSet",
  "missingFolder",
  "missingStackFiles",
  "missingProjectFiles",
  "uvNotFound",
  "uvEnvBroken",
  "modelsUnsupported",
] as const;
export type RepoStatus = (typeof REPO_STATUSES)[number];

export interface RepoCheck {
  status: RepoStatus;
  uvPath: string | null;
  missing: string[];
}

export const DATA_DIR_STATUSES = ["ok", "missing", "notDirectory", "notWritable", "notCanonical", "forbidden"] as const;
export type DataDirStatus = (typeof DATA_DIR_STATUSES)[number];

export interface DataDirCheck {
  path: string;
  status: DataDirStatus;
  canonicalPath: string | null;
}

export const SERVICE_NAMES = ["postgres", "rabbitmq", "api"] as const;
export type ServiceName = (typeof SERVICE_NAMES)[number];

export const SERVICE_STATES = ["stopped", "starting", "healthy", "unhealthy"] as const;
export type ServiceState = (typeof SERVICE_STATES)[number];

export interface ServiceStatus {
  service: ServiceName;
  state: ServiceState;
  /** `127.0.0.1:<porta>`. */
  address: string;
  checkedAt: string;
}

export const WORKER_POOL_STATES = ["stopped", "starting", "running", "stopping", "crashed", "orphanFound"] as const;
export type WorkerPoolState = (typeof WORKER_POOL_STATES)[number];

export const WORKER_EXIT_REASONS = [
  "stopped",
  "failure",
  "usage",
  "queueExtraMissing",
  "credentials",
  "topology",
  "killed",
  "unknown",
] as const;
export type WorkerExitReason = (typeof WORKER_EXIT_REASONS)[number];

export interface WorkerPool {
  state: WorkerPoolState;
  workers: number;
  minFreeMemoryMb: number;
  exitCode: number | null;
  reason: WorkerExitReason | null;
  lines: string[];
  memoryWarning: boolean;
  adopted: boolean;
}

export const STEP_KINDS = ["images", "models"] as const;
export type StepKind = (typeof STEP_KINDS)[number];

export const STEP_STATES = ["pending", "downloading", "done", "failed"] as const;
export type StepState = (typeof STEP_STATES)[number];

export const DOWNLOAD_FAILURES = ["noNetwork", "diskFull", "interrupted", "other"] as const;
export type DownloadFailure = (typeof DOWNLOAD_FAILURES)[number];

export interface FirstRunStep {
  step: StepKind;
  state: StepState;
  downloadedBytes: number | null;
  totalBytes: number | null;
  itemsDone: number;
  itemsTotal: number;
  updatedAt: string | null;
  failure: DownloadFailure | null;
  approxSize: string;
  location: string;
}

export const OPERATION_KINDS = [
  "startStack",
  "stopStack",
  "startWorkers",
  "stopWorkers",
  "prepareImages",
  "prepareModels",
] as const;
export type OperationKind = (typeof OPERATION_KINDS)[number];

export const OPERATION_STAGES = [
  "checkingRepo",
  "checkingDataDir",
  "ensuringParams",
  "validatingParams",
  "checkingImages",
  "pullingImages",
  "buildingApi",
  "runningUp",
  "runningDown",
  "startingWorkers",
  "stoppingWorkers",
  "forcingStop",
  "checkingDisk",
  "downloadingModels",
] as const;
export type OperationStage = (typeof OPERATION_STAGES)[number];

export interface OperationResult {
  ok: boolean;
  code: ErrorCode | null;
  service: string | null;
  port: number | null;
  failure: DownloadFailure | null;
  shortfallBytes: number | null;
  alreadyInState: boolean | null;
}

export interface Operation {
  kind: OperationKind;
  stage: OperationStage;
  startedAt: string;
  result: OperationResult | null;
}

export const SUMMARIES = ["healthy", "partial", "stopped"] as const;
export type Summary = (typeof SUMMARIES)[number];

export interface InfraSnapshot {
  readOnly: boolean;
  runtime: RuntimeReport;
  repo: RepoCheck;
  dataDir: DataDirCheck;
  services: ServiceStatus[];
  pool: WorkerPool;
  firstRun: FirstRunStep[];
  operations: Operation[];
  summary: Summary;
  consumerConnected: boolean | null;
}

export interface GuideProgress {
  /** `null` = ainda na comparação dos runtimes. */
  runtime: RuntimeKind | null;
  step: number;
}

/** Configurações da infraestrutura; `repoPath` e `dataDir` só mudam pelos seletores nativos (M1). */
export interface StackSettings {
  schemaVersion: 1;
  repoPath: string | null;
  workers: number;
  minFreeMemoryMb: number;
  dataDir: string;
  guide: GuideProgress | null;
}

export interface StackPorts {
  postgres: number;
  rabbitmq: number;
  api: number;
}

export interface StackSettingsResponse {
  settings: StackSettings;
  /** `null` quando o `.env` ainda não existe: a interface mostra os padrões. */
  ports: StackPorts | null;
}

export interface LifecycleOutcome {
  alreadyInState: boolean;
}

export interface OperationLog {
  lines: string[];
}

export type PreviousPoolAction = "stop" | "keep";

/** Portas padrão de `stack/.env.example:25-28` (mostradas quando o `.env` ainda não existe). */
export const DEFAULT_STACK_PORTS: StackPorts = { postgres: 5432, rabbitmq: 5672, api: 8778 };

/**
 * Chaves de cada objeto do contrato, na ordem do Rust. O teste de paridade compara estas listas com
 * as `struct` do Rust (lidas do código-fonte) e com a fixture serializada por ele.
 */
export const CONTRACT_KEYS = {
  InfraSnapshot: ["readOnly", "runtime", "repo", "dataDir", "services", "pool", "firstRun", "operations", "summary", "consumerConnected"],
  RuntimeReport: ["runtimes", "inUse", "overall", "dockerPath", "checkedAt"],
  RuntimeEntry: ["kind", "state", "context"],
  RepoCheck: ["status", "uvPath", "missing"],
  DataDirCheck: ["path", "status", "canonicalPath"],
  ServiceStatus: ["service", "state", "address", "checkedAt"],
  WorkerPool: ["state", "workers", "minFreeMemoryMb", "exitCode", "reason", "lines", "memoryWarning", "adopted"],
  FirstRunStep: ["step", "state", "downloadedBytes", "totalBytes", "itemsDone", "itemsTotal", "updatedAt", "failure", "approxSize", "location"],
  Operation: ["kind", "stage", "startedAt", "result"],
  OperationResult: ["ok", "code", "service", "port", "failure", "shortfallBytes", "alreadyInState"],
  StackSettings: ["schemaVersion", "repoPath", "workers", "minFreeMemoryMb", "dataDir", "guide"],
  GuideProgress: ["runtime", "step"],
  StackPorts: ["postgres", "rabbitmq", "api"],
  StackSettingsResponse: ["settings", "ports"],
  LifecycleOutcome: ["alreadyInState"],
} as const satisfies Record<string, readonly string[]>;
