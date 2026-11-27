//! Ciclo de vida da stack: `up.sh`, `down.sh` e tradução dos códigos de saída (spec FR-007 a
//! FR-009, FR-014, FR-015; research Decision 7; contracts/external-processes.md e
//! contracts/tauri-commands.md, `start_stack`/`stop_stack`).
//!
//! O app executa `/bin/sh <repo>/stack/up.sh` e `down.sh` SEM opções, com `current_dir` = repositório:
//! nunca passa `--management-panel`, `--apagar-dados` nem `--atualizar-imagens` (FR-008). O resultado
//! vem só do código de saída; o serviço e a porta de uma falha vêm de consulta própria (`compose …
//! ps`, conexão em loopback), nunca do texto em português do script.

use std::collections::HashSet;
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

use serde::Serialize;

use crate::error::{AppError, AppResult, ErrorCode};
use crate::stack::data_dir::DataDirCheck;
use crate::stack::env_file::{
    self, EnvFile, EnvIssue, KEY_API_PORT, KEY_POSTGRES_PORT, KEY_RABBITMQ_PORT,
};
use crate::stack::exec::{self, ExecOutcome, ExecSpec, Stream};
use crate::stack::first_run::FirstRunStep;
use crate::stack::images;
use crate::stack::repo::RepoCheck;
use crate::stack::runtime::{DockerEnv, RuntimeReport};
use crate::stack::secret::{Masked, Masker};

/// Serviços da stack, na ordem do `up.sh` (`stack/up.sh`, variável `SERVICES`).
pub const SERVICES: [&str; 3] = ["postgres", "rabbitmq", "api"];

/// Teto do `up.sh`: o script espera até 120 s pela saúde (`--wait-timeout 120`) e as imagens já
/// existem quando ele é chamado; a folga cobre a criação dos containers (M20).
pub const UP_TIMEOUT: Duration = Duration::from_secs(420);

/// Teto do `down.sh` (o `docker compose down` não declara prazo; SC-005 mede o tempo real).
pub const DOWN_TIMEOUT: Duration = Duration::from_secs(180);

/// Tempo máximo das consultas ao Compose feitas pelo app.
pub const PS_TIMEOUT: Duration = Duration::from_secs(10);

/// Tempo de conexão da checagem de porta ocupada (igual ao `nc -w 1` do `up.sh`).
const PORT_PROBE_TIMEOUT: Duration = Duration::from_secs(1);

/// Etapa atual de uma operação, traduzida na interface (data-model.md, entidade Operation).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum OperationStage {
    CheckingRepo,
    CheckingDataDir,
    EnsuringParams,
    ValidatingParams,
    CheckingImages,
    PullingImages,
    BuildingApi,
    RunningUp,
    RunningDown,
    StartingWorkers,
    StoppingWorkers,
    ForcingStop,
    CheckingDisk,
    DownloadingModels,
}

/// Quem acompanha uma operação: etapas e linhas técnicas já mascaradas.
pub trait Reporter {
    fn stage(&mut self, stage: OperationStage);
    fn line(&mut self, line: Masked);
    /// Progresso de uma etapa da primeira execução (imagens, modelos); ignorado por quem não o usa.
    fn step(&mut self, _step: &FirstRunStep) {}
}

/// Resultado de `start_stack`/`stop_stack`: `alreadyInState` da resposta do comando.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LifecycleOutcome {
    pub already_in_state: bool,
}

/// Falha de uma operação: o código do IPC e, quando a consulta própria achou, o serviço e a porta
/// que a interface cita (o `AppError` não os carrega para a interface).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LifecycleError {
    pub error: AppError,
    pub service: Option<String>,
    pub port: Option<u16>,
}

impl LifecycleError {
    fn new(code: ErrorCode, detail: &str) -> Self {
        Self {
            error: AppError::new(code, detail),
            service: None,
            port: None,
        }
    }
}

impl From<AppError> for LifecycleError {
    fn from(error: AppError) -> Self {
        Self {
            error,
            service: None,
            port: None,
        }
    }
}

/// Pré-condições de `start_stack` (contracts/tauri-commands.md): runtime `ready`, repositório `ok`
/// e pasta de dados `ok`, nesta ordem.
pub fn check_start_preconditions(
    runtime: &RuntimeReport,
    repo: &RepoCheck,
    data_dir: &DataDirCheck,
) -> AppResult<()> {
    check_stop_preconditions(runtime, repo)?;
    if !data_dir.is_ok() {
        return Err(AppError::new(
            ErrorCode::DataDirInvalid,
            "a pasta de dados não está pronta",
        ));
    }
    Ok(())
}

/// Pré-condições de `stop_stack`: runtime `ready` e repositório `ok` (a pasta de dados não importa
/// para derrubar).
pub fn check_stop_preconditions(runtime: &RuntimeReport, repo: &RepoCheck) -> AppResult<()> {
    if !runtime.is_ready() {
        return Err(AppError::new(
            ErrorCode::DockerNotReady,
            "o Docker não está pronto",
        ));
    }
    if !repo.is_ok() {
        return Err(AppError::new(
            ErrorCode::RepoInvalid,
            "a pasta do repositório não está pronta",
        ));
    }
    Ok(())
}

/// Serviços em execução segundo o Compose (`ps --status running --services`, como `stack/up.sh`).
pub fn running_services(env: &DockerEnv) -> AppResult<Vec<String>> {
    let spec = env
        .compose_spec(PS_TIMEOUT)
        .args(["ps", "--status", "running", "--services"]);
    let (outcome, lines) = exec::run_collect(spec, &Masker::new())?;
    if !outcome.success() {
        return Err(AppError::new(
            ErrorCode::DockerUnavailable,
            "compose ps --status running --services falhou",
        ));
    }
    Ok(lines
        .iter()
        .filter(|l| l.stream == Stream::Stdout)
        .map(|l| l.text.as_str().trim().to_string())
        .filter(|s| !s.is_empty())
        .collect())
}

fn all_services_running(running: &[String]) -> bool {
    SERVICES.iter().all(|s| running.iter().any(|r| r == s))
}

/// Primeiro serviço que não está `running` e saudável (mesmo critério de `stack/up.sh`, saída 5),
/// por `ps --all --format '{{.Service}} {{.State}} {{.Health}}'`.
fn unhealthy_service(env: &DockerEnv) -> Option<String> {
    let spec = env.compose_spec(PS_TIMEOUT).args([
        "ps",
        "--all",
        "--format",
        "{{.Service}} {{.State}} {{.Health}}",
    ]);
    let (outcome, lines) = exec::run_collect(spec, &Masker::new()).ok()?;
    if !outcome.success() {
        return None;
    }
    let texts: Vec<String> = lines
        .iter()
        .filter(|l| l.stream == Stream::Stdout)
        .map(|l| l.text.as_str().trim().to_string())
        .collect();
    SERVICES.iter().find_map(|svc| {
        let info = texts.iter().find(|t| t.starts_with(&format!("{svc} ")))?;
        let ok = info == &format!("{svc} running healthy") || info == &format!("{svc} running");
        (!ok).then(|| (*svc).to_string())
    })
}

/// Serviço (ainda não em execução) cuja porta do `.env` já aceita conexão em `127.0.0.1`: é a
/// porta ocupada por outro programa (saída 4). Mesma ordem do `up.sh`.
fn busy_port(env_file: &EnvFile, running: &HashSet<String>) -> Option<(String, u16)> {
    [
        ("postgres", KEY_POSTGRES_PORT),
        ("rabbitmq", KEY_RABBITMQ_PORT),
        ("api", KEY_API_PORT),
    ]
    .into_iter()
    .find_map(|(svc, key)| {
        if running.contains(svc) {
            return None;
        }
        let port = env_file.port(key)?;
        let addr = SocketAddr::from(([127, 0, 0, 1], port));
        TcpStream::connect_timeout(&addr, PORT_PROBE_TIMEOUT)
            .is_ok()
            .then(|| (svc.to_string(), port))
    })
}

fn run_script(
    env: &DockerEnv,
    script: &str,
    timeout: Duration,
    masker: &Masker,
    reporter: &mut dyn Reporter,
) -> AppResult<ExecOutcome> {
    // Sem argumentos: nenhuma opção destrutiva ou de painel passa por aqui (FR-008).
    let spec = ExecSpec::new("/bin/sh", timeout)
        .arg(env.stack_dir().join(script))
        .current_dir(&env.repo)
        .path_dirs(env.path_dirs.clone());
    exec::run(spec, masker, |line| reporter.line(line.text))
}

fn describe(outcome: &ExecOutcome, script: &str) -> String {
    if outcome.timed_out {
        format!("{script} excedeu o tempo máximo")
    } else if let Some(signal) = outcome.signal {
        format!("{script} terminou pelo sinal {signal}")
    } else {
        format!("{script} saiu com código {}", outcome.code.unwrap_or(-1))
    }
}

/// `start_stack`: garante o `.env`, valida o `.env`, confere imagens e só então chama o `up.sh`.
/// `data_dir` é a pasta de dados de `StackSettings`, usada só para criar o `.env` quando ausente.
pub fn start_stack(
    env: &DockerEnv,
    data_dir: &str,
    reporter: &mut dyn Reporter,
) -> Result<LifecycleOutcome, LifecycleError> {
    let env_path = env_file::env_path(&env.repo);

    reporter.stage(OperationStage::EnsuringParams);
    env_file::ensure(&env_path, data_dir)?;

    reporter.stage(OperationStage::ValidatingParams);
    let env_text = env_file::load(&env_path)?.ok_or_else(|| {
        LifecycleError::new(
            ErrorCode::InvalidParams,
            "stack/.env ausente após a criação",
        )
    })?;
    env_text
        .validate()
        .map_err(|issue: EnvIssue| issue.into_error())?;
    let mut masker = Masker::new();
    env_text.register_secrets(&mut masker);

    reporter.stage(OperationStage::CheckingImages);
    let running = running_services(env)?;
    if all_services_running(&running) {
        return Ok(LifecycleOutcome {
            already_in_state: true,
        });
    }
    if !images::check(env)?.all_present() {
        return Err(LifecycleError::new(
            ErrorCode::ImagesMissing,
            "há imagens da stack ausentes; o up.sh não foi chamado",
        ));
    }

    reporter.stage(OperationStage::RunningUp);
    let outcome = run_script(env, "up.sh", UP_TIMEOUT, &masker, reporter)?;
    translate_up(env, &env_text, &running, &outcome)
}

/// Traduz o resultado do `up.sh` (contracts/external-processes.md).
fn translate_up(
    env: &DockerEnv,
    env_text: &EnvFile,
    running_before: &[String],
    outcome: &ExecOutcome,
) -> Result<LifecycleOutcome, LifecycleError> {
    let detail = describe(outcome, "up.sh");
    if outcome.timed_out {
        return Err(LifecycleError::new(ErrorCode::ScriptFailed, &detail));
    }
    match outcome.code {
        Some(0) => Ok(LifecycleOutcome {
            already_in_state: false,
        }),
        Some(2) => Err(LifecycleError::new(ErrorCode::InvalidParams, &detail)),
        Some(3) => Err(LifecycleError::new(ErrorCode::DockerUnavailable, &detail)),
        Some(4) => {
            let running: HashSet<String> = running_before.iter().cloned().collect();
            let found = busy_port(env_text, &running);
            Err(LifecycleError {
                error: AppError::new(ErrorCode::PortInUse, detail),
                service: found.as_ref().map(|(s, _)| s.clone()),
                port: found.map(|(_, p)| p),
            })
        }
        Some(5) => Err(LifecycleError {
            error: AppError::new(ErrorCode::ServiceUnhealthy, detail),
            service: unhealthy_service(env),
            port: None,
        }),
        // 1 (falha no `cd` do script), código não documentado ou término por sinal.
        _ => Err(LifecycleError::new(ErrorCode::ScriptFailed, &detail)),
    }
}

/// `stop_stack`: confere se a stack já está parada e, se não, chama o `down.sh`. O `down.sh` não
/// exige um `.env` completo, então uma consulta que falha (por exemplo, `.env` quebrado) não
/// impede a tentativa de derrubar.
pub fn stop_stack(
    env: &DockerEnv,
    reporter: &mut dyn Reporter,
) -> Result<LifecycleOutcome, LifecycleError> {
    let env_path = env_file::env_path(&env.repo);
    let mut masker = Masker::new();
    if let Ok(Some(parsed)) = env_file::load(&env_path) {
        parsed.register_secrets(&mut masker);
    }

    reporter.stage(OperationStage::CheckingImages);
    if let Ok(running) = running_services(env) {
        if running.is_empty() {
            return Ok(LifecycleOutcome {
                already_in_state: true,
            });
        }
    }

    reporter.stage(OperationStage::RunningDown);
    let outcome = run_script(env, "down.sh", DOWN_TIMEOUT, &masker, reporter)?;
    translate_down(&outcome)
}

/// Traduz o resultado do `down.sh` (contracts/tauri-commands.md, "Mapeamento de saída do down.sh").
fn translate_down(outcome: &ExecOutcome) -> Result<LifecycleOutcome, LifecycleError> {
    let detail = describe(outcome, "down.sh");
    if outcome.timed_out {
        return Err(LifecycleError::new(ErrorCode::ScriptFailed, &detail));
    }
    match outcome.code {
        Some(0) => Ok(LifecycleOutcome {
            already_in_state: false,
        }),
        Some(1) => Err(LifecycleError::new(ErrorCode::StopFailed, &detail)),
        Some(3) => Err(LifecycleError::new(ErrorCode::DockerUnavailable, &detail)),
        // 2 (opções inválidas, que o app não passa) e qualquer código não documentado.
        _ => Err(LifecycleError::new(ErrorCode::ScriptFailed, &detail)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stack::data_dir::DataDirStatus;
    use crate::stack::repo::RepoStatus;
    use crate::stack::runtime::{RuntimeEntry, RuntimeKind, RuntimeOverall, RuntimeState};
    use std::fs;
    use std::net::TcpListener;
    use std::os::unix::fs::PermissionsExt;
    use std::path::{Path, PathBuf};

    const FAKE_DOCKER: &str = r#"#!/bin/sh
d=$(dirname "$0")
echo "$@" >> "$d/calls.log"
for a in "$@"; do last=$a; done
case " $* " in
  *" ps --status running --services "*) cat "$d/running.txt" 2>/dev/null; exit 0;;
  *" ps --all --format "*) cat "$d/psall.txt" 2>/dev/null; exit 0;;
  *" config --images "*) cat "$d/images.txt" 2>/dev/null; exit 0;;
  *" image inspect "*) grep -qx "$last" "$d/present.txt" 2>/dev/null; exit $?;;
esac
exit 99
"#;

    const FAKE_UP: &str = r#"#!/bin/sh
d=$(dirname "$0")
{ echo "args=$#"; pwd; } > "$d/up.marker"
echo "linha do up"
echo "senha=$(sed -n 's/^POSTGRES_PASSWORD=//p' "$d/.env")"
code=$(cat "$d/up.code" 2>/dev/null || echo 0)
exit "$code"
"#;

    const FAKE_DOWN: &str = r#"#!/bin/sh
d=$(dirname "$0")
{ echo "args=$#"; pwd; } > "$d/down.marker"
echo "linha do down"
code=$(cat "$d/down.code" 2>/dev/null || echo 0)
exit "$code"
"#;

    const PG_PASSWORD: &str = "pg-Segredo-Muito-Longo-123";

    #[derive(Default)]
    struct Rec {
        stages: Vec<OperationStage>,
        lines: Vec<String>,
    }

    impl Reporter for Rec {
        fn stage(&mut self, stage: OperationStage) {
            self.stages.push(stage);
        }
        fn line(&mut self, line: Masked) {
            self.lines.push(line.as_str().to_string());
        }
    }

    struct Fx {
        _tmp: tempfile::TempDir,
        repo: PathBuf,
        bin: PathBuf,
        data: PathBuf,
        env: DockerEnv,
    }

    fn script(path: &Path, body: &str) {
        fs::write(path, body).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
    }

    impl Fx {
        fn new() -> Fx {
            let tmp = tempfile::tempdir().unwrap();
            let root = fs::canonicalize(tmp.path()).unwrap();
            // Pasta com espaço: os argumentos têm de chegar intactos.
            let repo = root.join("repo com espaço");
            let stack = repo.join("stack");
            let bin = root.join("bin");
            let data = root.join("dados");
            for d in [&stack, &bin, &data] {
                fs::create_dir_all(d).unwrap();
            }
            script(&bin.join("docker"), FAKE_DOCKER);
            script(&stack.join("up.sh"), FAKE_UP);
            script(&stack.join("down.sh"), FAKE_DOWN);
            fs::write(
                bin.join("images.txt"),
                "artigo2md-api:local\npostgres:18-trixie\nrabbitmq:4.4-management\n",
            )
            .unwrap();
            let fx = Fx {
                env: DockerEnv {
                    docker: bin.join("docker"),
                    context: "desktop-linux".to_string(),
                    path_dirs: vec![bin.clone()],
                    repo: repo.clone(),
                },
                _tmp: tmp,
                repo,
                bin,
                data,
            };
            fx.set(
                "present.txt",
                "artigo2md-api:local\npostgres:18-trixie\nrabbitmq:4.4-management\n",
            );
            fx
        }

        fn set(&self, name: &str, text: &str) {
            fs::write(self.bin.join(name), text).unwrap();
        }

        fn stack_file(&self, name: &str) -> PathBuf {
            self.repo.join("stack").join(name)
        }

        fn write_env_with(&self, postgres_port: u16, password: &str) {
            let text = format!(
                "ARTIGO2MD_DATA_DIR={}\nARTIGO2MD_POSTGRES_PORT={postgres_port}\nARTIGO2MD_RABBITMQ_PORT=5672\n\
                 ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT=15672\nARTIGO2MD_API_PORT=8778\nPOSTGRES_USER=artigo2md\n\
                 POSTGRES_PASSWORD={password}\nPOSTGRES_DB=artigo2md\nRABBITMQ_DEFAULT_USER=artigo2md\n\
                 RABBITMQ_DEFAULT_PASS=mq-Segredo-Muito-Longo-456\n",
                self.data.display()
            );
            let path = self.stack_file(".env");
            fs::write(&path, text).unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        }

        fn write_env(&self) {
            self.write_env_with(5432, PG_PASSWORD);
        }

        fn data(&self) -> String {
            self.data.to_str().unwrap().to_string()
        }

        fn calls(&self) -> String {
            fs::read_to_string(self.bin.join("calls.log")).unwrap_or_default()
        }

        fn marker(&self, name: &str) -> Option<String> {
            fs::read_to_string(self.stack_file(name)).ok()
        }
    }

    fn start(fx: &Fx, rec: &mut Rec) -> Result<LifecycleOutcome, LifecycleError> {
        start_stack(&fx.env, &fx.data(), rec)
    }

    fn report(overall: RuntimeOverall) -> RuntimeReport {
        let ready = overall == RuntimeOverall::Ready;
        RuntimeReport {
            runtimes: RuntimeKind::ALL
                .iter()
                .map(|k| RuntimeEntry {
                    kind: *k,
                    state: if ready && *k == RuntimeKind::Orbstack {
                        RuntimeState::Running
                    } else {
                        RuntimeState::NotInstalled
                    },
                    context: (*k == RuntimeKind::Orbstack).then(|| "orbstack".to_string()),
                })
                .collect(),
            in_use: Some(RuntimeKind::Orbstack),
            overall,
            docker_path: None,
            checked_at: "2026-01-01T00:00:00Z".to_string(),
        }
    }

    fn repo_check(status: RepoStatus) -> RepoCheck {
        RepoCheck {
            status,
            uv_path: None,
            missing: Vec::new(),
        }
    }

    fn data_check(status: DataDirStatus) -> DataDirCheck {
        DataDirCheck {
            path: "/x".to_string(),
            status,
            canonical_path: None,
        }
    }

    #[test]
    fn preconditions_are_checked_in_order() {
        let ok = (
            report(RuntimeOverall::Ready),
            repo_check(RepoStatus::Ok),
            data_check(DataDirStatus::Ok),
        );
        assert!(check_start_preconditions(&ok.0, &ok.1, &ok.2).is_ok());
        assert!(check_stop_preconditions(&ok.0, &ok.1).is_ok());

        let code = |r: &RuntimeReport, p: &RepoCheck, d: &DataDirCheck| {
            check_start_preconditions(r, p, d).unwrap_err().code
        };
        assert_eq!(
            code(
                &report(RuntimeOverall::Stopped),
                &repo_check(RepoStatus::NotSet),
                &data_check(DataDirStatus::Missing)
            ),
            ErrorCode::DockerNotReady
        );
        assert_eq!(
            code(
                &ok.0,
                &repo_check(RepoStatus::MissingStackFiles),
                &data_check(DataDirStatus::Missing)
            ),
            ErrorCode::RepoInvalid
        );
        assert_eq!(
            code(&ok.0, &ok.1, &data_check(DataDirStatus::NotWritable)),
            ErrorCode::DataDirInvalid
        );
        // Parar não depende da pasta de dados.
        assert!(check_stop_preconditions(&ok.0, &ok.1).is_ok());
        assert_eq!(
            check_stop_preconditions(&report(RuntimeOverall::Ambiguous), &ok.1)
                .unwrap_err()
                .code,
            ErrorCode::DockerNotReady
        );
    }

    #[test]
    fn start_runs_up_sh_without_options_in_the_repo_dir() {
        let fx = Fx::new();
        fx.write_env();
        let mut rec = Rec::default();
        let out = start(&fx, &mut rec).unwrap();
        assert!(!out.already_in_state);
        assert_eq!(
            rec.stages,
            [
                OperationStage::EnsuringParams,
                OperationStage::ValidatingParams,
                OperationStage::CheckingImages,
                OperationStage::RunningUp
            ]
        );
        let marker = fx.marker("up.marker").unwrap();
        // Nenhuma opção (nem --management-panel, nem --atualizar-imagens) e cwd = repositório.
        assert!(marker.starts_with("args=0\n"), "{marker}");
        assert!(marker.contains(fx.repo.to_str().unwrap()), "{marker}");
        assert!(rec.lines.iter().any(|l| l == "linha do up"));
        // Toda chamada ao Compose leva o contexto e os três caminhos, com a pasta com espaço intacta.
        let calls = fx.calls();
        assert!(calls.contains("--context=desktop-linux compose --project-directory"));
        assert!(calls.contains(&format!("{}/stack/.env", fx.repo.display())));
    }

    #[test]
    fn start_never_leaks_the_password_to_the_technical_log() {
        let fx = Fx::new();
        fx.write_env();
        let mut rec = Rec::default();
        start(&fx, &mut rec).unwrap();
        let all = rec.lines.join("\n");
        assert!(!all.contains(PG_PASSWORD), "{all}");
        assert!(all.contains("senha=***"), "{all}");
    }

    #[test]
    fn start_creates_the_env_when_absent_before_anything_else() {
        let fx = Fx::new();
        assert!(!fx.stack_file(".env").exists());
        let mut rec = Rec::default();
        start(&fx, &mut rec).unwrap();
        let meta = fs::metadata(fx.stack_file(".env")).unwrap();
        assert_eq!(meta.permissions().mode() & 0o777, 0o600);
        assert!(fx.marker("up.marker").is_some());
    }

    #[test]
    fn start_is_idempotent_when_the_stack_is_already_up() {
        let fx = Fx::new();
        fx.write_env();
        fx.set("running.txt", "postgres\nrabbitmq\napi\n");
        let mut rec = Rec::default();
        let out = start(&fx, &mut rec).unwrap();
        assert!(out.already_in_state);
        assert!(fx.marker("up.marker").is_none());
        assert!(!rec.stages.contains(&OperationStage::RunningUp));
    }

    #[test]
    fn start_with_missing_images_never_calls_up_sh() {
        let fx = Fx::new();
        fx.write_env();
        fx.set("present.txt", "postgres:18-trixie\n");
        let mut rec = Rec::default();
        let err = start(&fx, &mut rec).unwrap_err();
        assert_eq!(err.error.code, ErrorCode::ImagesMissing);
        assert!(fx.marker("up.marker").is_none());
        assert!(!rec.stages.contains(&OperationStage::RunningUp));
    }

    #[test]
    fn start_with_invalid_params_names_the_variable_and_skips_the_script() {
        let fx = Fx::new();
        fx.write_env_with(5432, "curta");
        let mut rec = Rec::default();
        let err = start(&fx, &mut rec).unwrap_err();
        assert_eq!(err.error.code, ErrorCode::InvalidParams);
        assert!(err.error.detail().contains("POSTGRES_PASSWORD"));
        assert!(!err.error.detail().contains("curta"));
        assert!(fx.marker("up.marker").is_none());
        assert!(!fx.calls().contains("compose"));
    }

    fn up_with_code(fx: &Fx, code: i32) -> LifecycleError {
        fs::write(fx.stack_file("up.code"), code.to_string()).unwrap();
        start(fx, &mut Rec::default()).unwrap_err()
    }

    #[test]
    fn up_exit_codes_map_to_ipc_codes() {
        let fx = Fx::new();
        fx.write_env();
        for (code, expected) in [
            (1, ErrorCode::ScriptFailed),
            (2, ErrorCode::InvalidParams),
            (3, ErrorCode::DockerUnavailable),
            (9, ErrorCode::ScriptFailed),
            (127, ErrorCode::ScriptFailed),
        ] {
            let err = up_with_code(&fx, code);
            assert_eq!(err.error.code, expected, "código {code}");
            assert!(err.error.detail().contains(&format!("código {code}")));
            assert_eq!((err.service, err.port), (None, None));
        }
    }

    #[test]
    fn up_exit_4_finds_the_busy_port_by_its_own_probe() {
        let fx = Fx::new();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        fx.write_env_with(port, PG_PASSWORD);
        let err = up_with_code(&fx, 4);
        assert_eq!(err.error.code, ErrorCode::PortInUse);
        assert_eq!(err.service.as_deref(), Some("postgres"));
        assert_eq!(err.port, Some(port));

        // Um serviço já em execução (a porta é dele) não conta como porta ocupada por outro.
        fx.set("running.txt", "postgres\n");
        let err = up_with_code(&fx, 4);
        assert_eq!(err.error.code, ErrorCode::PortInUse);
        assert_eq!((err.service, err.port), (None, None));
    }

    #[test]
    fn up_exit_5_names_the_unhealthy_service_by_compose_state() {
        let fx = Fx::new();
        fx.write_env();
        fx.set(
            "psall.txt",
            "postgres running healthy\nrabbitmq running starting\napi running \n",
        );
        let err = up_with_code(&fx, 5);
        assert_eq!(err.error.code, ErrorCode::ServiceUnhealthy);
        assert_eq!(err.service.as_deref(), Some("rabbitmq"));

        fx.set(
            "psall.txt",
            "postgres running healthy\nrabbitmq running healthy\napi exited \n",
        );
        assert_eq!(up_with_code(&fx, 5).service.as_deref(), Some("api"));

        // `running` sem saúde declarada é aceito (mesmo critério do script).
        fx.set(
            "psall.txt",
            "postgres running healthy\nrabbitmq running healthy\napi running \n",
        );
        assert_eq!(up_with_code(&fx, 5).service, None);
    }

    #[test]
    fn stop_runs_down_sh_without_options() {
        let fx = Fx::new();
        fx.write_env();
        fx.set("running.txt", "postgres\nrabbitmq\napi\n");
        let mut rec = Rec::default();
        let out = stop_stack(&fx.env, &mut rec).unwrap();
        assert!(!out.already_in_state);
        let marker = fx.marker("down.marker").unwrap();
        assert!(marker.starts_with("args=0\n"), "{marker}");
        assert!(marker.contains(fx.repo.to_str().unwrap()));
        assert!(rec.stages.contains(&OperationStage::RunningDown));
        assert!(rec.lines.iter().any(|l| l == "linha do down"));
    }

    #[test]
    fn stop_is_idempotent_when_nothing_is_running() {
        let fx = Fx::new();
        fx.write_env();
        let out = stop_stack(&fx.env, &mut Rec::default()).unwrap();
        assert!(out.already_in_state);
        assert!(fx.marker("down.marker").is_none());
    }

    #[test]
    fn stop_without_an_env_file_still_tries_down_sh() {
        let fx = Fx::new();
        fx.set("running.txt", "rabbitmq\n");
        let out = stop_stack(&fx.env, &mut Rec::default()).unwrap();
        assert!(!out.already_in_state);
        assert!(fx.marker("down.marker").is_some());
    }

    #[test]
    fn down_exit_codes_map_to_ipc_codes() {
        let fx = Fx::new();
        fx.write_env();
        fx.set("running.txt", "postgres\n");
        for (code, expected) in [
            (0, None),
            (1, Some(ErrorCode::StopFailed)),
            (2, Some(ErrorCode::ScriptFailed)),
            (3, Some(ErrorCode::DockerUnavailable)),
            (7, Some(ErrorCode::ScriptFailed)),
        ] {
            fs::write(fx.stack_file("down.code"), code.to_string()).unwrap();
            let result = stop_stack(&fx.env, &mut Rec::default());
            match expected {
                None => assert!(result.is_ok(), "código {code}"),
                Some(c) => assert_eq!(result.unwrap_err().error.code, c, "código {code}"),
            }
        }
    }

    #[test]
    fn terminated_by_signal_is_a_script_failure() {
        let outcome = ExecOutcome {
            code: None,
            signal: Some(9),
            timed_out: false,
        };
        let err = translate_down(&outcome).unwrap_err();
        assert_eq!(err.error.code, ErrorCode::ScriptFailed);
        assert!(err.error.detail().contains("sinal 9"));
        let timed_out = ExecOutcome {
            code: None,
            signal: Some(15),
            timed_out: true,
        };
        assert!(translate_down(&timed_out)
            .unwrap_err()
            .error
            .detail()
            .contains("tempo máximo"));
    }

    #[test]
    fn no_destructive_option_exists_in_the_source() {
        let source = include_str!("lifecycle.rs");
        let code_part = source.split("#[cfg(test)]").next().unwrap();
        for forbidden in [
            "--management-panel",
            "--apagar-dados",
            "--atualizar-imagens",
        ] {
            // Só os comentários podem citar as opções; nenhum literal de argumento.
            let in_code = code_part
                .lines()
                .filter(|l| !l.trim_start().starts_with("//"))
                .any(|l| l.contains(forbidden));
            assert!(!in_code, "{forbidden} apareceu em código");
        }
    }
}
