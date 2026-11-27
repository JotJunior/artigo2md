//! Detecção dos três runtimes Docker e classificação do diagnóstico (spec FR-001, FR-002, FR-003;
//! research Decision 2; data-model.md, RuntimeReport).
//!
//! Três sinais independentes, sem conexão de rede:
//! 1. instalado: `/Applications/Docker.app`, `/Applications/OrbStack.app` ou `~/.orbstack/bin`, e
//!    executável `colima` resolvido por `locate.rs`;
//! 2. contexto Docker de cada runtime (`desktop-linux`, `orbstack`, `colima`), listados por
//!    `docker context ls --format '{{json .}}'`;
//! 3. em execução: `docker --context=<nome> info --format '{{json .}}'` com saída 0.
//!
//! "Docker em uso" é o contexto corrente. Com o corrente sendo `default` (ou desconhecido) e mais
//! de um runtime em execução o diagnóstico é `ambiguous`: o app não escolhe um sem avisar.

use std::path::{Path, PathBuf};
use std::thread;
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::stack::clock;
use crate::stack::exec::{self, context_arg, ExecSpec, Stream};
use crate::stack::locate::Located;
use crate::stack::secret::Masker;

/// Tempo máximo de cada chamada ao `docker` do diagnóstico (M20). O daemon parado costuma falhar
/// em milissegundos; o teto cobre um daemon travado.
pub const DOCKER_QUERY_TIMEOUT: Duration = Duration::from_secs(8);

/// Pasta onde o macOS instala os aplicativos (injetável nos testes).
pub const APPLICATIONS_DIR: &str = "/Applications";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum RuntimeKind {
    DockerDesktop,
    Orbstack,
    Colima,
}

impl RuntimeKind {
    /// Sempre nesta ordem em `RuntimeReport.runtimes`.
    pub const ALL: [RuntimeKind; 3] = [
        RuntimeKind::DockerDesktop,
        RuntimeKind::Orbstack,
        RuntimeKind::Colima,
    ];

    /// Contexto Docker do runtime (research Decision 2): `colima` é o perfil padrão.
    pub fn context_name(self) -> &'static str {
        match self {
            RuntimeKind::DockerDesktop => "desktop-linux",
            RuntimeKind::Orbstack => "orbstack",
            RuntimeKind::Colima => "colima",
        }
    }

    fn from_context(name: &str) -> Option<RuntimeKind> {
        RuntimeKind::ALL
            .into_iter()
            .find(|k| k.context_name() == name)
    }

    fn index(self) -> usize {
        match self {
            RuntimeKind::DockerDesktop => 0,
            RuntimeKind::Orbstack => 1,
            RuntimeKind::Colima => 2,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum RuntimeState {
    NotInstalled,
    Stopped,
    Running,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeEntry {
    pub kind: RuntimeKind,
    pub state: RuntimeState,
    /// Nome do contexto Docker do runtime, quando ele existe na lista de contextos.
    pub context: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum RuntimeOverall {
    Ready,
    NoRuntime,
    Stopped,
    ComposeMissing,
    Ambiguous,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeReport {
    /// Sempre três entradas, na ordem dockerDesktop, orbstack, colima.
    pub runtimes: Vec<RuntimeEntry>,
    pub in_use: Option<RuntimeKind>,
    pub overall: RuntimeOverall,
    pub docker_path: Option<String>,
    pub checked_at: String,
}

impl RuntimeReport {
    pub fn is_ready(&self) -> bool {
        self.overall == RuntimeOverall::Ready
    }

    fn entry(&self, kind: RuntimeKind) -> &RuntimeEntry {
        &self.runtimes[kind.index()]
    }

    /// Contexto Docker a passar em `--context=` (só com o diagnóstico `ready`).
    pub fn docker_context(&self) -> Option<&str> {
        if !self.is_ready() {
            return None;
        }
        self.entry(self.in_use?).context.as_deref()
    }
}

/// Insumos da detecção, todos explícitos para a verificação funcionar com `docker` falso.
#[derive(Debug, Clone)]
pub struct DetectEnv {
    pub docker: Option<PathBuf>,
    pub colima: Option<PathBuf>,
    pub home: PathBuf,
    pub applications_dir: PathBuf,
    /// Diretórios do `docker` e do `uv` para o `PATH` dos filhos.
    pub path_dirs: Vec<PathBuf>,
}

impl DetectEnv {
    pub fn from_located(located: &Located, home: &Path) -> Self {
        Self {
            docker: located.docker.clone(),
            colima: located.colima.clone(),
            home: home.to_path_buf(),
            applications_dir: PathBuf::from(APPLICATIONS_DIR),
            path_dirs: located.path_dirs(),
        }
    }

    fn installed(&self, kind: RuntimeKind) -> bool {
        match kind {
            RuntimeKind::DockerDesktop => self.applications_dir.join("Docker.app").exists(),
            RuntimeKind::Orbstack => {
                self.applications_dir.join("OrbStack.app").exists()
                    || self.home.join(".orbstack/bin").exists()
            }
            RuntimeKind::Colima => self.colima.is_some(),
        }
    }

    fn spec(&self, docker: &Path) -> ExecSpec {
        ExecSpec::new(docker, DOCKER_QUERY_TIMEOUT).path_dirs(self.path_dirs.clone())
    }
}

/// Uma linha de `docker context ls --format '{{json .}}'`; os demais campos são ignorados.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "PascalCase")]
struct ContextLine {
    name: String,
    #[serde(default)]
    current: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ContextInfo {
    name: String,
    current: bool,
}

fn list_contexts(env: &DetectEnv, docker: &Path) -> Vec<ContextInfo> {
    let spec = env
        .spec(docker)
        .args(["context", "ls", "--format", "{{json .}}"]);
    let Ok((outcome, lines)) = exec::run_collect(spec, &Masker::new()) else {
        return Vec::new();
    };
    if !outcome.success() {
        return Vec::new();
    }
    lines
        .iter()
        .filter(|l| l.stream == Stream::Stdout)
        .filter_map(|l| serde_json::from_str::<ContextLine>(l.text.as_str().trim()).ok())
        .map(|c| ContextInfo {
            name: c.name,
            current: c.current,
        })
        .collect()
}

/// `docker --context=<c> info` com saída 0 = daemon acessível.
fn daemon_responds(env: &DetectEnv, docker: &Path, context: &str) -> bool {
    let Some(ctx) = context_arg(context) else {
        return false;
    };
    let spec = env
        .spec(docker)
        .arg(ctx)
        .args(["info", "--format", "{{json .}}"]);
    exec::run_collect(spec, &Masker::new()).is_ok_and(|(o, _)| o.success())
}

/// `docker --context=<c> compose version` com saída 0 = plugin do Compose presente.
fn compose_available(env: &DetectEnv, docker: &Path, context: &str) -> bool {
    let Some(ctx) = context_arg(context) else {
        return false;
    };
    let spec = env.spec(docker).arg(ctx).args(["compose", "version"]);
    exec::run_collect(spec, &Masker::new()).is_ok_and(|(o, _)| o.success())
}

/// Diagnóstico completo. Nunca falha: qualquer erro de execução vira "não instalado" ou "parado".
pub fn detect(env: &DetectEnv) -> RuntimeReport {
    let installed: Vec<bool> = RuntimeKind::ALL.iter().map(|k| env.installed(*k)).collect();

    let contexts = match &env.docker {
        Some(docker) => list_contexts(env, docker),
        None => Vec::new(),
    };
    let current = contexts.iter().find(|c| c.current).map(|c| c.name.as_str());

    // Contexto de cada runtime, se existir na lista.
    let context_of: Vec<Option<String>> = RuntimeKind::ALL
        .iter()
        .map(|k| {
            contexts
                .iter()
                .find(|c| c.name == k.context_name())
                .map(|c| c.name.clone())
        })
        .collect();

    // As três verificações de "em execução" correm em paralelo (cada uma com seu teto de tempo).
    let running: Vec<bool> = match &env.docker {
        Some(docker) => thread::scope(|scope| {
            let handles: Vec<_> = context_of
                .iter()
                .map(|ctx| {
                    scope.spawn(move || match ctx {
                        Some(name) => daemon_responds(env, docker, name),
                        None => false,
                    })
                })
                .collect();
            handles
                .into_iter()
                .map(|h| h.join().unwrap_or(false))
                .collect()
        }),
        None => vec![false; 3],
    };

    let runtimes: Vec<RuntimeEntry> = RuntimeKind::ALL
        .iter()
        .enumerate()
        .map(|(i, kind)| RuntimeEntry {
            kind: *kind,
            state: if running[i] {
                RuntimeState::Running
            } else if installed[i] {
                RuntimeState::Stopped
            } else {
                RuntimeState::NotInstalled
            },
            context: context_of[i].clone(),
        })
        .collect();

    let running_kinds: Vec<RuntimeKind> = runtimes
        .iter()
        .filter(|e| e.state == RuntimeState::Running)
        .map(|e| e.kind)
        .collect();
    let current_kind = current.and_then(RuntimeKind::from_context);
    let in_use = match current_kind {
        Some(kind) => Some(kind),
        None if running_kinds.len() == 1 => Some(running_kinds[0]),
        None => None,
    };

    let any_present = runtimes
        .iter()
        .any(|e| e.state != RuntimeState::NotInstalled);
    let overall = if in_use.is_none() && running_kinds.len() > 1 {
        RuntimeOverall::Ambiguous
    } else if running_kinds.is_empty() {
        if any_present {
            RuntimeOverall::Stopped
        } else {
            RuntimeOverall::NoRuntime
        }
    } else {
        match in_use {
            Some(kind) if runtimes[kind.index()].state == RuntimeState::Running => {
                let context = runtimes[kind.index()].context.as_deref().unwrap_or("");
                let compose_ok = env
                    .docker
                    .as_deref()
                    .is_some_and(|docker| compose_available(env, docker, context));
                if compose_ok {
                    RuntimeOverall::Ready
                } else {
                    RuntimeOverall::ComposeMissing
                }
            }
            // O contexto corrente é de um runtime parado, mesmo havendo outro em execução.
            _ => RuntimeOverall::Stopped,
        }
    };

    RuntimeReport {
        runtimes,
        in_use,
        overall,
        docker_path: env
            .docker
            .as_ref()
            .map(|p| p.to_string_lossy().into_owned()),
        checked_at: clock::now_iso8601(),
    }
}

/// Ambiente para chamar o `docker` e o Compose da stack com o contexto em uso (research
/// Decision 7: `--context=<c>` sempre, sem `DOCKER_HOST`/`DOCKER_CONTEXT`).
#[derive(Debug, Clone)]
pub struct DockerEnv {
    pub docker: PathBuf,
    pub context: String,
    pub path_dirs: Vec<PathBuf>,
    pub repo: PathBuf,
}

impl DockerEnv {
    /// `None` se o diagnóstico não é `ready`, falta o `docker` ou o nome do contexto não passa na
    /// validação (M15).
    pub fn from_report(report: &RuntimeReport, located: &Located, repo: &Path) -> Option<Self> {
        let context = report.docker_context()?;
        context_arg(context)?;
        Some(Self {
            docker: located.docker.clone()?,
            context: context.to_string(),
            path_dirs: located.path_dirs(),
            repo: repo.to_path_buf(),
        })
    }

    pub fn stack_dir(&self) -> PathBuf {
        self.repo.join("stack")
    }

    /// `docker --context=<c>` e nada mais; o chamador acrescenta o subcomando.
    pub fn docker_spec(&self, timeout: Duration) -> ExecSpec {
        let ctx = context_arg(&self.context).unwrap_or_default();
        ExecSpec::new(&self.docker, timeout)
            .arg(ctx)
            .path_dirs(self.path_dirs.clone())
    }

    /// `docker --context=<c> compose --project-directory <repo>/stack --env-file
    /// <repo>/stack/.env -f <repo>/stack/compose.yaml` (`stack/up.sh:24-26`); o chamador acrescenta
    /// opções globais (`--progress json`) e o subcomando.
    pub fn compose_spec(&self, timeout: Duration) -> ExecSpec {
        let stack = self.stack_dir();
        self.docker_spec(timeout)
            .arg("compose")
            .arg("--project-directory")
            .arg(&stack)
            .arg("--env-file")
            .arg(stack.join(".env"))
            .arg("-f")
            .arg(stack.join("compose.yaml"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    /// `docker` falso: contextos de `contexts.json`; `info` responde se existir `running-<ctx>`;
    /// `compose version` falha se existir `no-compose`.
    const FAKE_DOCKER: &str = r#"#!/bin/sh
d=$(dirname "$0")
case "$1" in
  context) cat "$d/contexts.json"; exit 0;;
  --context=*)
    c=${1#--context=}
    shift
    case "$1" in
      info)
        if [ -e "$d/running-$c" ]; then echo '{}'; exit 0; fi
        echo "failed to connect to the docker API" >&2; exit 1;;
      compose)
        if [ -e "$d/no-compose" ]; then exit 1; fi
        echo "Docker Compose version v5"; exit 0;;
    esac;;
esac
exit 99
"#;

    struct Fx {
        _tmp: tempfile::TempDir,
        bin: PathBuf,
        env: DetectEnv,
    }

    impl Fx {
        fn new() -> Fx {
            let tmp = tempfile::tempdir().unwrap();
            let root = fs::canonicalize(tmp.path()).unwrap();
            let bin = root.join("bin");
            fs::create_dir_all(&bin).unwrap();
            let docker = bin.join("docker");
            fs::write(&docker, FAKE_DOCKER).unwrap();
            fs::set_permissions(&docker, fs::Permissions::from_mode(0o755)).unwrap();
            let apps = root.join("Applications");
            let home = root.join("home");
            fs::create_dir_all(&apps).unwrap();
            fs::create_dir_all(&home).unwrap();
            let fx = Fx {
                bin: bin.clone(),
                env: DetectEnv {
                    docker: Some(docker),
                    colima: None,
                    home,
                    applications_dir: apps,
                    path_dirs: vec![bin],
                },
                _tmp: tmp,
            };
            fx.contexts(&["default"], "default");
            fx
        }

        /// Escreve `contexts.json` no formato observado de `docker context ls --format '{{json .}}'`.
        fn contexts(&self, names: &[&str], current: &str) {
            let text: String = names
                .iter()
                .map(|n| {
                    format!(
                        "{{\"Current\":{},\"Description\":\"x\",\"DockerEndpoint\":\"unix:///x.sock\",\"Error\":\"\",\"Name\":\"{n}\"}}\n",
                        *n == current
                    )
                })
                .collect();
            fs::write(self.bin.join("contexts.json"), text).unwrap();
        }

        fn running(&self, ctx: &str) {
            fs::write(self.bin.join(format!("running-{ctx}")), "").unwrap();
        }

        fn install(&self, app: &str) {
            fs::create_dir_all(self.env.applications_dir.join(app)).unwrap();
        }
    }

    fn state_of(report: &RuntimeReport, kind: RuntimeKind) -> RuntimeState {
        report.runtimes[kind.index()].state
    }

    #[test]
    fn scenario_1_no_runtime_installed() {
        let fx = Fx::new();
        let report = detect(&fx.env);
        assert_eq!(report.overall, RuntimeOverall::NoRuntime);
        assert_eq!(report.in_use, None);
        assert!(report
            .runtimes
            .iter()
            .all(|e| e.state == RuntimeState::NotInstalled));
        assert_eq!(report.docker_context(), None);
    }

    #[test]
    fn scenario_1_without_a_docker_executable_never_fails() {
        let mut fx = Fx::new();
        fx.env.docker = None;
        assert_eq!(detect(&fx.env).overall, RuntimeOverall::NoRuntime);
        fx.install("Docker.app");
        let report = detect(&fx.env);
        assert_eq!(report.overall, RuntimeOverall::Stopped);
        assert_eq!(report.docker_path, None);
    }

    #[test]
    fn scenario_2_installed_but_stopped_names_the_runtime() {
        let fx = Fx::new();
        fx.install("Docker.app");
        fx.contexts(&["default", "desktop-linux"], "desktop-linux");
        let report = detect(&fx.env);
        assert_eq!(report.overall, RuntimeOverall::Stopped);
        assert_eq!(report.in_use, Some(RuntimeKind::DockerDesktop));
        assert_eq!(
            state_of(&report, RuntimeKind::DockerDesktop),
            RuntimeState::Stopped
        );
        assert_eq!(
            state_of(&report, RuntimeKind::Orbstack),
            RuntimeState::NotInstalled
        );
        assert_eq!(report.docker_context(), None);
    }

    #[test]
    fn orbstack_is_detected_by_its_app_or_its_bin_folder() {
        let fx = Fx::new();
        fx.install("OrbStack.app");
        assert_eq!(
            state_of(&detect(&fx.env), RuntimeKind::Orbstack),
            RuntimeState::Stopped
        );
        let fx = Fx::new();
        fs::create_dir_all(fx.env.home.join(".orbstack/bin")).unwrap();
        assert_eq!(
            state_of(&detect(&fx.env), RuntimeKind::Orbstack),
            RuntimeState::Stopped
        );
    }

    #[test]
    fn colima_is_installed_when_its_executable_was_found() {
        let mut fx = Fx::new();
        fx.env.colima = Some(fx.bin.join("colima"));
        assert_eq!(
            state_of(&detect(&fx.env), RuntimeKind::Colima),
            RuntimeState::Stopped
        );
    }

    #[test]
    fn scenario_3_running_with_compose_is_ready() {
        let fx = Fx::new();
        fx.install("Docker.app");
        fx.contexts(&["default", "desktop-linux"], "desktop-linux");
        fx.running("desktop-linux");
        let report = detect(&fx.env);
        assert_eq!(report.overall, RuntimeOverall::Ready);
        assert!(report.is_ready());
        assert_eq!(report.in_use, Some(RuntimeKind::DockerDesktop));
        assert_eq!(report.docker_context(), Some("desktop-linux"));
        assert_eq!(
            state_of(&report, RuntimeKind::DockerDesktop),
            RuntimeState::Running
        );
    }

    #[test]
    fn scenario_4_running_without_compose_is_not_ready() {
        let fx = Fx::new();
        fx.install("OrbStack.app");
        fx.contexts(&["default", "orbstack"], "orbstack");
        fx.running("orbstack");
        fs::write(fx.bin.join("no-compose"), "").unwrap();
        let report = detect(&fx.env);
        assert_eq!(report.overall, RuntimeOverall::ComposeMissing);
        assert_eq!(report.in_use, Some(RuntimeKind::Orbstack));
        assert_eq!(report.docker_context(), None);
    }

    #[test]
    fn scenario_6_check_again_reflects_the_new_situation() {
        let fx = Fx::new();
        fx.install("Docker.app");
        fx.contexts(&["default", "desktop-linux"], "desktop-linux");
        assert_eq!(detect(&fx.env).overall, RuntimeOverall::Stopped);
        fx.running("desktop-linux");
        assert_eq!(detect(&fx.env).overall, RuntimeOverall::Ready);
        fs::remove_file(fx.bin.join("running-desktop-linux")).unwrap();
        assert_eq!(detect(&fx.env).overall, RuntimeOverall::Stopped);
    }

    #[test]
    fn two_runtimes_with_default_context_is_ambiguous() {
        let fx = Fx::new();
        fx.install("Docker.app");
        fx.contexts(&["default", "desktop-linux", "colima"], "default");
        fx.running("desktop-linux");
        fx.running("colima");
        let report = detect(&fx.env);
        assert_eq!(report.overall, RuntimeOverall::Ambiguous);
        assert_eq!(report.in_use, None);
        assert_eq!(report.docker_context(), None);
        assert_eq!(
            state_of(&report, RuntimeKind::DockerDesktop),
            RuntimeState::Running
        );
        assert_eq!(
            state_of(&report, RuntimeKind::Colima),
            RuntimeState::Running
        );
    }

    #[test]
    fn two_runtimes_with_a_runtime_context_current_uses_that_one() {
        let fx = Fx::new();
        fx.contexts(&["default", "desktop-linux", "orbstack"], "orbstack");
        fx.running("desktop-linux");
        fx.running("orbstack");
        let report = detect(&fx.env);
        assert_eq!(report.overall, RuntimeOverall::Ready);
        assert_eq!(report.in_use, Some(RuntimeKind::Orbstack));
        assert_eq!(report.docker_context(), Some("orbstack"));
    }

    #[test]
    fn current_context_of_a_stopped_runtime_is_not_ready_even_if_another_runs() {
        let fx = Fx::new();
        fx.contexts(&["default", "desktop-linux", "colima"], "colima");
        fx.running("desktop-linux");
        let report = detect(&fx.env);
        assert_eq!(report.overall, RuntimeOverall::Stopped);
        assert_eq!(report.in_use, Some(RuntimeKind::Colima));
    }

    #[test]
    fn default_context_with_a_single_running_runtime_uses_it() {
        let fx = Fx::new();
        fx.contexts(&["default", "colima"], "default");
        fx.running("colima");
        let report = detect(&fx.env);
        assert_eq!(report.overall, RuntimeOverall::Ready);
        assert_eq!(report.in_use, Some(RuntimeKind::Colima));
    }

    #[test]
    fn report_serializes_in_camel_case_with_three_ordered_entries() {
        let fx = Fx::new();
        fx.install("Docker.app");
        fx.contexts(&["default", "desktop-linux"], "desktop-linux");
        fx.running("desktop-linux");
        let json = serde_json::to_value(detect(&fx.env)).unwrap();
        let kinds: Vec<&str> = json["runtimes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|e| e["kind"].as_str().unwrap())
            .collect();
        assert_eq!(kinds, ["dockerDesktop", "orbstack", "colima"]);
        assert_eq!(json["overall"], "ready");
        assert_eq!(json["inUse"], "dockerDesktop");
        assert_eq!(json["runtimes"][0]["state"], "running");
        assert_eq!(json["runtimes"][1]["state"], "notInstalled");
        assert!(json["runtimes"][1]["context"].is_null());
        assert!(json["checkedAt"].as_str().unwrap().ends_with('Z'));
        assert!(json["dockerPath"].is_string());
    }

    #[test]
    fn docker_env_builds_the_compose_prefix_of_the_script() {
        let fx = Fx::new();
        fx.contexts(&["default", "orbstack"], "orbstack");
        fx.running("orbstack");
        let report = detect(&fx.env);
        let located = Located {
            docker: fx.env.docker.clone(),
            uv: None,
            colima: None,
        };
        let repo = fx.env.home.join("repo com espaço");
        let denv = DockerEnv::from_report(&report, &located, &repo).unwrap();
        assert_eq!(denv.context, "orbstack");
        assert_eq!(denv.stack_dir(), repo.join("stack"));

        // Sem diagnóstico `ready` não há ambiente.
        let stopped = {
            let fx = Fx::new();
            detect(&fx.env)
        };
        assert!(DockerEnv::from_report(&stopped, &located, &repo).is_none());
    }
}
