// Conteúdo estático do guia de instalação do Docker (FR-004 a FR-006; research.md Decision 15).
// Tudo vive dentro do app: o guia funciona sem internet. Cada passo traz a página oficial de onde
// veio (`sourceUrl`); passo sem fonte não entra. Nenhum passo abre o navegador, baixa ou executa
// instalador: endereços e comandos são só texto para a pessoa copiar (FR-005).
//
// Os textos de comparação e o tom do guia dependem da pendência ux CHK018 do dono do produto e são
// fáceis de trocar: ficam todos neste arquivo.

import type { GuideProgress, RuntimeKind } from "./types";
import { RUNTIME_KINDS } from "./types";

export interface GuideStep {
  runtime: RuntimeKind;
  /** Posição do passo (0..n-1); a interface mostra "passo i de n". */
  index: number;
  /** Instrução curta em português. */
  text: string;
  /** Endereços ou comandos mostrados como texto copiável. */
  copyable: string[];
  /** Página oficial de onde o passo veio. */
  sourceUrl: string;
  /** Versão mínima do macOS, quando a fonte a declara para este runtime. */
  minMacos: string | null;
}

export interface RuntimeGuide {
  runtime: RuntimeKind;
  name: string;
  /** Linha da comparação: para quem serve e como é a licença (research Decision 15). */
  license: string;
  /** Página oficial da licença e do preço (fonte da linha `license`). */
  licenseSource: string;
  /** Observação opcional da comparação. */
  note: string | null;
  /** Versão mínima do macOS declarada pela fonte; `null` quando a fonte não a declara. */
  minMacos: string | null;
  minMacosSource: string | null;
  steps: GuideStep[];
}

const DOCKER_INSTALL = "https://docs.docker.com/desktop/setup/install/mac-install/";
const ORBSTACK_DOWNLOAD = "https://orbstack.dev/download";
const ORBSTACK_QUICK_START = "https://docs.orbstack.dev/quick-start";
const COLIMA_REPO = "https://github.com/abiosoft/colima";
const COLIMA_FAQ = "https://github.com/abiosoft/colima/blob/main/docs/FAQ.md";
const COMPOSE_FORMULA = "https://formulae.brew.sh/formula/docker-compose";

type StepSeed = Omit<GuideStep, "runtime" | "index">;

function build(
  runtime: RuntimeKind,
  head: Omit<RuntimeGuide, "runtime" | "steps">,
  seeds: StepSeed[],
): RuntimeGuide {
  return { runtime, ...head, steps: seeds.map((seed, index) => ({ runtime, index, ...seed })) };
}

export const GUIDE: Record<RuntimeKind, RuntimeGuide> = {
  dockerDesktop: build(
    "dockerDesktop",
    {
      name: "Docker Desktop",
      license:
        "Grátis para uso pessoal, educação, código aberto não comercial e empresas com menos de 250 pessoas e menos de US$ 10 milhões de receita. Acima disso, exige assinatura paga.",
      licenseSource: DOCKER_INSTALL,
      note: "É o mais conhecido dos três. O Docker Desktop suporta a versão atual do macOS e as duas anteriores.",
      minMacos: null,
      minMacosSource: null,
    },
    [
      {
        text: "Baixe o instalador do Docker Desktop (o arquivo Docker.dmg). O primeiro endereço é para Mac com chip Apple (Apple silicon); o segundo, para Mac com processador Intel. Cole o endereço no navegador.",
        copyable: [
          "https://desktop.docker.com/mac/main/arm64/Docker.dmg",
          "https://desktop.docker.com/mac/main/amd64/Docker.dmg",
        ],
        sourceUrl: DOCKER_INSTALL,
        minMacos: null,
      },
      {
        text: "Abra o arquivo Docker.dmg que você baixou e arraste o Docker para a pasta Aplicativos.",
        copyable: [],
        sourceUrl: DOCKER_INSTALL,
        minMacos: null,
      },
      {
        text: "Abra o Docker (Docker.app) e aceite o contrato de assinatura. Sem aceitá-lo, o Docker não funciona.",
        copyable: [],
        sourceUrl: DOCKER_INSTALL,
        minMacos: null,
      },
    ],
  ),
  orbstack: build(
    "orbstack",
    {
      name: "OrbStack",
      license: "Grátis para uso pessoal. O uso comercial exige uma licença por pessoa.",
      licenseSource: "https://docs.orbstack.dev/licensing",
      note: "Exige macOS 14 ou mais novo.",
      minMacos: "14",
      minMacosSource: ORBSTACK_DOWNLOAD,
    },
    [
      {
        text: "Baixe o OrbStack no endereço oficial abaixo. Há versões separadas para Mac com chip Apple (Apple Silicon) e para Mac com processador Intel: escolha a do seu Mac.",
        copyable: [ORBSTACK_DOWNLOAD],
        sourceUrl: ORBSTACK_DOWNLOAD,
        minMacos: "14",
      },
      {
        text: "Abra o OrbStack. Segundo a documentação oficial, ele já vem pronto para usar, sem configuração extra.",
        copyable: [],
        sourceUrl: ORBSTACK_QUICK_START,
        minMacos: null,
      },
    ],
  ),
  colima: build(
    "colima",
    {
      name: "colima",
      license: "Licença MIT, gratuito.",
      licenseSource: COLIMA_REPO,
      note: "Instalado por comandos do Homebrew; exige macOS 13 ou mais novo.",
      minMacos: "13",
      minMacosSource: COLIMA_FAQ,
    },
    [
      {
        text: "Este caminho usa o Homebrew. Instale o colima com o comando abaixo.",
        copyable: ["brew install colima"],
        sourceUrl: COLIMA_REPO,
        minMacos: "13",
      },
      {
        text: "Instale também o cliente do Docker com o comando abaixo.",
        copyable: ["brew install docker"],
        sourceUrl: COLIMA_REPO,
        minMacos: null,
      },
      {
        text: "Instale o Docker Compose com o comando abaixo. Ao terminar, siga a instrução que o próprio Homebrew mostra ao final da instalação para registrar o plugin do Compose.",
        copyable: ["brew install docker-compose"],
        sourceUrl: COMPOSE_FORMULA,
        minMacos: null,
      },
      {
        text: "Inicie o colima com o comando abaixo.",
        copyable: ["colima start"],
        sourceUrl: COLIMA_REPO,
        minMacos: null,
      },
    ],
  ),
};

export function guideSteps(runtime: RuntimeKind): GuideStep[] {
  return GUIDE[runtime].steps;
}

export function stepCount(runtime: RuntimeKind): number {
  return GUIDE[runtime].steps.length;
}

/** Maior número de passos entre os runtimes; igual a `MAX_GUIDE_STEPS` do Rust (teste de paridade). */
export function maxStepCount(): number {
  return Math.max(...RUNTIME_KINDS.map(stepCount));
}

/** Compara versões "14", "14.5.1" etc. Devolve negativo, zero ou positivo. */
export function compareVersions(a: string, b: string): number {
  const left = a.split(".").map((part) => Number.parseInt(part, 10) || 0);
  const right = b.split(".").map((part) => Number.parseInt(part, 10) || 0);
  for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
    const diff = (left[i] ?? 0) - (right[i] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
}

export interface Availability {
  available: boolean;
  /** Versão mínima exigida quando o runtime está indisponível. */
  requires: string | null;
}

/**
 * Runtime indisponível neste Mac por causa da versão do macOS (research Decision 15). Com a versão
 * desconhecida (`null`) nada é marcado: a comparação mostra a exigência como texto.
 */
export function runtimeAvailability(runtime: RuntimeKind, macosVersion: string | null): Availability {
  const min = GUIDE[runtime].minMacos;
  if (min === null || macosVersion === null) return { available: true, requires: null };
  return compareVersions(macosVersion, min) >= 0
    ? { available: true, requires: null }
    : { available: false, requires: min };
}

/** Progresso salvo → progresso válido para o conteúdo atual (retomada, FR-006). */
export function resumeProgress(saved: GuideProgress | null): GuideProgress | null {
  if (!saved || saved.runtime === null) return saved === null ? null : { runtime: null, step: 0 };
  if (!(RUNTIME_KINDS as readonly string[]).includes(saved.runtime)) return { runtime: null, step: 0 };
  const count = stepCount(saved.runtime);
  return { runtime: saved.runtime, step: Math.min(Math.max(0, saved.step), count - 1) };
}

/** Passo em que o runtime é aberto ou iniciado: o último de cada um (research Decision 15). */
export function startStepIndex(runtime: RuntimeKind): number {
  return stepCount(runtime) - 1;
}
