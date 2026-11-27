// Tarefa 10.4.5 (desktop-stack-control): conteúdo estático do guia de instalação. Todo passo tem
// fonte oficial, nenhum passo abre, baixa ou executa, a contagem confere com o Rust e o guia
// funciona sem rede. O comportamento do componente está em tests/infra-components.test.ts.

import { describe, expect, it } from "vitest";
import {
  GUIDE,
  compareVersions,
  guideSteps,
  maxStepCount,
  resumeProgress,
  runtimeAvailability,
  startStepIndex,
  stepCount,
} from "../src/lib/guide";
import { RUNTIME_KINDS } from "../src/lib/types";

// Hosts das fontes oficiais citadas em research.md, Decision 15. Qualquer endereço novo no guia
// precisa estar lá com fonte antes de entrar aqui.
const OFFICIAL_HOSTS = new Set([
  "docs.docker.com",
  "desktop.docker.com",
  "orbstack.dev",
  "docs.orbstack.dev",
  "github.com",
  "formulae.brew.sh",
]);

const STEP_KEYS = ["copyable", "index", "minMacos", "runtime", "sourceUrl", "text"];

describe("conteúdo do guia (FR-004, FR-005, research Decision 15)", () => {
  it("todo passo tem fonte oficial https registrada e texto em português", () => {
    for (const kind of RUNTIME_KINDS) {
      for (const step of guideSteps(kind)) {
        const url = new URL(step.sourceUrl);
        expect(url.protocol, `${kind}#${step.index}`).toBe("https:");
        expect(OFFICIAL_HOSTS.has(url.hostname), `${kind}#${step.index}: ${url.hostname}`).toBe(true);
        expect(step.text.length).toBeGreaterThan(20);
        expect(step.runtime).toBe(kind);
      }
      expect(new URL(GUIDE[kind].licenseSource).protocol).toBe("https:");
    }
  });

  it("todo endereço copiável é de fonte oficial e comandos são só os da Decision 15", () => {
    const commands = new Set<string>();
    for (const kind of RUNTIME_KINDS) {
      for (const step of guideSteps(kind)) {
        for (const text of step.copyable) {
          if (/^https?:\/\//.test(text)) expect(OFFICIAL_HOSTS.has(new URL(text).hostname), text).toBe(true);
          else commands.add(text);
        }
      }
    }
    expect([...commands].sort()).toEqual(
      ["brew install colima", "brew install docker", "brew install docker-compose", "colima start"].sort(),
    );
  });

  it("nenhuma ação de abrir, baixar ou executar: o passo é só dado (FR-005)", () => {
    for (const kind of RUNTIME_KINDS) {
      for (const step of guideSteps(kind)) {
        expect(Object.keys(step).sort()).toEqual(STEP_KEYS);
        for (const value of Object.values(step)) expect(typeof value).not.toBe("function");
      }
    }
  });

  it("contagem de passos e índices sequenciais", () => {
    expect(stepCount("dockerDesktop")).toBe(3);
    expect(stepCount("orbstack")).toBe(2);
    expect(stepCount("colima")).toBe(4);
    for (const kind of RUNTIME_KINDS) {
      expect(guideSteps(kind).map((s) => s.index)).toEqual(guideSteps(kind).map((_, i) => i));
      expect(startStepIndex(kind)).toBe(stepCount(kind) - 1);
    }
  });

  it("MAX_GUIDE_STEPS do Rust é a maior contagem real do guia (paridade, dec-039)", () => {
    const sources = import.meta.glob("../src-tauri/src/stack/stack_settings.rs", {
      query: "?raw",
      import: "default",
      eager: true,
    }) as Record<string, string>;
    const rust = Object.values(sources)[0] ?? "";
    const match = /pub const MAX_GUIDE_STEPS: u32 = (\d+);/.exec(rust);
    expect(match).not.toBeNull();
    expect(Number(match![1])).toBe(maxStepCount());
  });

  it("o passo do Compose pelo Homebrew manda seguir a mensagem do próprio Homebrew", () => {
    const compose = guideSteps("colima").find((s) => s.copyable.includes("brew install docker-compose"));
    expect(compose?.text).toContain("instrução que o próprio Homebrew mostra");
    expect(compose?.text).not.toContain("cliPluginsExtraDirs");
  });

  it("a comparação traz licença e fonte de cada runtime, e a nota de suporte do Docker Desktop", () => {
    for (const kind of RUNTIME_KINDS) {
      expect(GUIDE[kind].license.length).toBeGreaterThan(10);
      expect(GUIDE[kind].name).toBeTruthy();
    }
    expect(GUIDE.dockerDesktop.note).toContain("duas anteriores");
    expect(GUIDE.orbstack.minMacos).toBe("14");
    expect(GUIDE.colima.minMacos).toBe("13");
    expect(GUIDE.dockerDesktop.minMacos).toBeNull();
  });
});

describe("disponibilidade por versão do macOS (10.4.3)", () => {
  it("compara versões numericamente", () => {
    expect(compareVersions("14", "14.0")).toBe(0);
    expect(compareVersions("13.6.1", "14")).toBeLessThan(0);
    expect(compareVersions("15.1", "14")).toBeGreaterThan(0);
    expect(compareVersions("27.2.1", "14")).toBeGreaterThan(0);
  });

  it("OrbStack exige 14+ e colima 13+; Docker Desktop nunca é marcado", () => {
    expect(runtimeAvailability("orbstack", "13.6")).toEqual({ available: false, requires: "14" });
    expect(runtimeAvailability("orbstack", "14.0")).toEqual({ available: true, requires: null });
    expect(runtimeAvailability("colima", "12.7")).toEqual({ available: false, requires: "13" });
    expect(runtimeAvailability("colima", "13")).toEqual({ available: true, requires: null });
    expect(runtimeAvailability("dockerDesktop", "11.0")).toEqual({ available: true, requires: null });
  });

  it("versão desconhecida não marca nenhum runtime como indisponível", () => {
    for (const kind of RUNTIME_KINDS) expect(runtimeAvailability(kind, null).available).toBe(true);
  });
});

describe("retomada do passo (FR-006, US4-5)", () => {
  it("sem progresso salvo não há retomada", () => {
    expect(resumeProgress(null)).toBeNull();
  });

  it("na comparação, volta à comparação", () => {
    expect(resumeProgress({ runtime: null, step: 0 })).toEqual({ runtime: null, step: 0 });
  });

  it("mantém runtime e passo válidos", () => {
    expect(resumeProgress({ runtime: "colima", step: 2 })).toEqual({ runtime: "colima", step: 2 });
  });

  it("passo além do conteúdo atual é limitado ao último passo do runtime", () => {
    expect(resumeProgress({ runtime: "orbstack", step: 3 })).toEqual({ runtime: "orbstack", step: 1 });
    expect(resumeProgress({ runtime: "orbstack", step: -2 })).toEqual({ runtime: "orbstack", step: 0 });
  });

  it("runtime desconhecido volta à comparação", () => {
    expect(resumeProgress({ runtime: "podman" as never, step: 1 })).toEqual({ runtime: null, step: 0 });
  });
});
