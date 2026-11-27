//! Etapa "modelos de conversão" (spec FR-016A, FR-036; contracts/models-cli.md e
//! contracts/tauri-commands.md, `prepare_models`; plan.md M16; research Decision 9).
//!
//! O app inicia `<uv> run --no-sync --project <repo> artigo2md models <verbo> --json` como processo
//! próprio (nunca baixa nada por conexão do app, FR-037). Cada linha de stdout é um evento JSON com
//! `schema_version`; evento sem o campo ou com versão desconhecida torna o repositório incompatível
//! (`RepoStatus::ModelsUnsupported`) e o resto da linha não é interpretado.

use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Duration;

use serde_json::Value;

use crate::error::{AppError, ErrorCode};
use crate::stack::env_file;
use crate::stack::exec::{self, ExecOutcome, ExecSpec, Stream};
use crate::stack::first_run::{
    Confirmer, DownloadFailure, FirstRunStep, PrepareError, StepKind, StepState,
    MODELS_APPROX_TEXT, MODELS_LOCATION,
};
use crate::stack::lifecycle::{OperationStage, Reporter};
use crate::stack::locate::Located;
use crate::stack::secret::Masker;

/// Versão do contrato que este app conhece (`contracts/models-cli.md`).
pub const SCHEMA_VERSION: u64 = 1;

/// Teto do `models status` (um processo Python que só importa `huggingface_hub`).
pub const STATUS_TIMEOUT: Duration = Duration::from_secs(60);
/// Teto do `models download`: dois modelos de alguns GB (`README.md:34`) em conexão lenta.
pub const DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(4 * 3600);

/// Ambiente para chamar o subcomando `models` pelo ambiente `uv` do repositório.
#[derive(Debug, Clone)]
pub struct ModelsEnv {
    pub uv: PathBuf,
    pub repo: PathBuf,
    pub path_dirs: Vec<PathBuf>,
}

impl ModelsEnv {
    /// `None` se o `uv` não foi resolvido.
    pub fn from_located(located: &Located, repo: &std::path::Path) -> Option<Self> {
        Some(Self {
            uv: located.uv.clone()?,
            repo: repo.to_path_buf(),
            path_dirs: located.path_dirs(),
        })
    }

    fn spec(&self, verb: &str, timeout: Duration) -> ExecSpec {
        ExecSpec::new(&self.uv, timeout)
            .for_uv()
            .args(["run", "--no-sync", "--project"])
            .arg(&self.repo)
            .args(["artigo2md", "models", verb, "--json"])
            .current_dir(&self.repo)
            .path_dirs(self.path_dirs.clone())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelPresence {
    pub id: String,
    pub present: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelsStatus {
    pub cache_dir: String,
    pub models: Vec<ModelPresence>,
}

impl ModelsStatus {
    pub fn all_present(&self) -> bool {
        !self.models.is_empty() && self.models.iter().all(|m| m.present)
    }

    /// Etapa `models` do retrato: `done` só com todos presentes; senão `pending` (nunca persistida).
    pub fn to_step(&self) -> FirstRunStep {
        let mut step = FirstRunStep::new(StepKind::Models);
        step.items_total = self.models.len() as u32;
        step.items_done = self.models.iter().filter(|m| m.present).count() as u32;
        if self.all_present() {
            step.state = StepState::Done;
        }
        step
    }
}

/// Evento do `models` já interpretado.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ModelsEvent {
    Status(ModelsStatus),
    Plan {
        total_bytes: Option<u64>,
        missing: Vec<String>,
    },
    Disk {
        free_bytes: u64,
        required_bytes: u64,
        ok: bool,
    },
    Progress {
        id: String,
        downloaded_bytes: u64,
        total_bytes: Option<u64>,
    },
    ModelDone {
        id: String,
    },
    Result {
        done: bool,
        reason: Option<String>,
        message: Option<String>,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParsedLine {
    Event(ModelsEvent),
    /// Objeto JSON sem `schema_version` ou com versão desconhecida: repositório incompatível.
    Unsupported,
    /// Texto livre, JSON que não é objeto ou evento de tipo desconhecido (tolerado).
    Ignored,
}

fn string_of(object: &serde_json::Map<String, Value>, key: &str) -> Option<String> {
    object.get(key)?.as_str().map(str::to_string)
}

fn u64_of(object: &serde_json::Map<String, Value>, key: &str) -> Option<u64> {
    object.get(key)?.as_u64()
}

/// Interpreta uma linha de stdout. A versão é conferida ANTES de qualquer outro campo.
pub fn parse_event(line: &str) -> ParsedLine {
    let text = line.trim();
    if !text.starts_with('{') {
        return ParsedLine::Ignored;
    }
    let Ok(Value::Object(object)) = serde_json::from_str::<Value>(text) else {
        return ParsedLine::Ignored;
    };
    if object.get("schema_version").and_then(Value::as_u64) != Some(SCHEMA_VERSION) {
        return ParsedLine::Unsupported;
    }
    let Some(kind) = string_of(&object, "event") else {
        return ParsedLine::Ignored;
    };
    let event = match kind.as_str() {
        "status" => {
            let Some(cache_dir) = string_of(&object, "cache_dir") else {
                return ParsedLine::Ignored;
            };
            let models = object
                .get("models")
                .and_then(Value::as_array)
                .map(|list| {
                    list.iter()
                        .filter_map(|m| {
                            Some(ModelPresence {
                                id: m.get("id")?.as_str()?.to_string(),
                                present: m.get("present")?.as_bool()?,
                            })
                        })
                        .collect()
                })
                .unwrap_or_default();
            ModelsEvent::Status(ModelsStatus { cache_dir, models })
        }
        "plan" => ModelsEvent::Plan {
            total_bytes: u64_of(&object, "total_bytes"),
            missing: object
                .get("missing")
                .and_then(Value::as_array)
                .map(|l| {
                    l.iter()
                        .filter_map(|v| v.as_str().map(str::to_string))
                        .collect()
                })
                .unwrap_or_default(),
        },
        "disk" => ModelsEvent::Disk {
            free_bytes: u64_of(&object, "free_bytes").unwrap_or(0),
            required_bytes: u64_of(&object, "required_bytes").unwrap_or(0),
            ok: object.get("ok").and_then(Value::as_bool).unwrap_or(false),
        },
        "progress" => {
            let Some(id) = string_of(&object, "id") else {
                return ParsedLine::Ignored;
            };
            ModelsEvent::Progress {
                id,
                downloaded_bytes: u64_of(&object, "downloaded_bytes").unwrap_or(0),
                total_bytes: u64_of(&object, "total_bytes"),
            }
        }
        "model_done" => {
            let Some(id) = string_of(&object, "id") else {
                return ParsedLine::Ignored;
            };
            ModelsEvent::ModelDone { id }
        }
        "result" => ModelsEvent::Result {
            done: string_of(&object, "status").as_deref() == Some("done"),
            reason: string_of(&object, "reason"),
            message: string_of(&object, "message"),
        },
        _ => return ParsedLine::Ignored,
    };
    ParsedLine::Event(event)
}

/// Resultado da verificação (`models status --json`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ModelsCheck {
    Checked(ModelsStatus),
    /// Subcomando ausente (saída 2), evento sem versão ou de versão desconhecida, ou saída 0 sem
    /// nenhum evento reconhecível: `RepoStatus::ModelsUnsupported` (FR-016A).
    Unsupported,
    /// Saída 6: plataforma fora do macOS.
    PlatformUnsupported,
    /// O processo falhou por outro motivo (`uv` quebrado, tempo esgotado, código inesperado).
    Failed,
}

fn masker_from_env(env: &ModelsEnv) -> Masker {
    let mut masker = Masker::new();
    if let Ok(Some(parsed)) = env_file::load(&env_file::env_path(&env.repo)) {
        parsed.register_secrets(&mut masker);
    }
    masker
}

/// `models status --json`: sem rede; usado na abertura, em "Verificar novamente", antes de iniciar
/// workers e ao fim de cada preparação.
pub fn check_models(env: &ModelsEnv) -> ModelsCheck {
    let masker = masker_from_env(env);
    let Ok((outcome, lines)) = exec::run_collect(env.spec("status", STATUS_TIMEOUT), &masker)
    else {
        return ModelsCheck::Failed;
    };
    if outcome.timed_out || outcome.signal.is_some() {
        return ModelsCheck::Failed;
    }
    match outcome.code {
        Some(2) => return ModelsCheck::Unsupported,
        Some(6) => return ModelsCheck::PlatformUnsupported,
        Some(0) => {}
        _ => return ModelsCheck::Failed,
    }
    for line in lines.iter().filter(|l| l.stream == Stream::Stdout) {
        match parse_event(line.text.as_str()) {
            ParsedLine::Event(ModelsEvent::Status(status)) => return ModelsCheck::Checked(status),
            ParsedLine::Unsupported => return ModelsCheck::Unsupported,
            _ => {}
        }
    }
    ModelsCheck::Unsupported
}

/// Texto do diálogo nativo de confirmação (FR-031/FR-036): tamanho aproximado, internet e destino.
pub fn confirmation_text() -> (&'static str, String) {
    (
        "Baixar os modelos de conversão",
        format!(
            "Serão baixados os modelos de conversão ({}). O download usa a internet e os arquivos \
             ficam no {}. Deseja continuar?",
            MODELS_APPROX_TEXT, MODELS_LOCATION
        ),
    )
}

fn failure_from_reason(reason: Option<&str>) -> DownloadFailure {
    match reason {
        Some("no_network") => DownloadFailure::NoNetwork,
        Some("disk_full") => DownloadFailure::DiskFull,
        Some("interrupted") => DownloadFailure::Interrupted,
        _ => DownloadFailure::Other,
    }
}

/// Acompanhamento do `download`: bytes somados por modelo, modelos concluídos e o que importa do
/// resultado.
struct Tracker {
    step: FirstRunStep,
    per_model: HashMap<String, u64>,
    done_models: u32,
    disk: Option<(u64, u64, bool)>,
    result: Option<(bool, Option<String>)>,
    unsupported: bool,
}

impl Tracker {
    fn on_event(&mut self, event: ModelsEvent, reporter: &mut dyn Reporter) {
        match event {
            ModelsEvent::Plan {
                total_bytes,
                missing,
            } => {
                self.step.total_bytes = total_bytes;
                self.step.items_total = missing.len() as u32;
            }
            ModelsEvent::Disk {
                free_bytes,
                required_bytes,
                ok,
            } => {
                self.disk = Some((free_bytes, required_bytes, ok));
                if ok {
                    reporter.stage(OperationStage::DownloadingModels);
                }
            }
            ModelsEvent::Progress {
                id,
                downloaded_bytes,
                ..
            } => {
                self.per_model.insert(id, downloaded_bytes);
                self.step.downloaded_bytes = Some(self.per_model.values().sum());
            }
            ModelsEvent::ModelDone { .. } => self.done_models += 1,
            ModelsEvent::Result { done, reason, .. } => self.result = Some((done, reason)),
            ModelsEvent::Status(_) => {}
        }
        self.step.items_done = self.done_models;
        self.step.touch();
        reporter.step(&self.step);
    }
}

/// `prepare_models`: confirma e baixa só o que falta, consumindo os eventos JSON Lines. O estado
/// final vem da verificação (`models status`) depois do download, nunca só do processo.
pub fn prepare_models(
    env: &ModelsEnv,
    confirmer: &dyn Confirmer,
    reporter: &mut dyn Reporter,
) -> Result<FirstRunStep, PrepareError> {
    let (title, message) = confirmation_text();
    if !confirmer.confirm(title, &message) {
        return Err(AppError::new(
            ErrorCode::ConfirmationRequired,
            "a pessoa não confirmou o download dos modelos",
        )
        .into());
    }

    reporter.stage(OperationStage::CheckingDisk);
    let masker = masker_from_env(env);
    let mut step = FirstRunStep::new(StepKind::Models);
    step.state = StepState::Downloading;
    let mut tracker = Tracker {
        step,
        per_model: HashMap::new(),
        done_models: 0,
        disk: None,
        result: None,
        unsupported: false,
    };
    reporter.step(&tracker.step);

    let outcome = exec::run(env.spec("download", DOWNLOAD_TIMEOUT), &masker, |line| {
        let text = line.text;
        if line.stream == Stream::Stdout && !tracker.unsupported {
            match parse_event(text.as_str()) {
                ParsedLine::Event(event) => tracker.on_event(event, reporter),
                // Daqui em diante nada mais é interpretado (FR-016A).
                ParsedLine::Unsupported => tracker.unsupported = true,
                ParsedLine::Ignored => {}
            }
        }
        reporter.line(text);
    })?;
    finish_download(env, &outcome, tracker, reporter)
}

fn failed(
    mut tracker: Tracker,
    code: ErrorCode,
    failure: DownloadFailure,
    detail: String,
    shortfall: Option<u64>,
) -> PrepareError {
    tracker.step.finish_failed(failure);
    PrepareError {
        error: AppError::new(code, detail),
        failure: Some(failure),
        shortfall_bytes: shortfall,
        step: Some(Box::new(tracker.step)),
    }
}

/// Mapeia o fim do `download` (contracts/tauri-commands.md, `prepare_models`): 7 → espaço
/// insuficiente; 1 → motivo do `result`; 2 e 6 → `other`; término sem `result` → `interrupted`.
fn finish_download(
    env: &ModelsEnv,
    outcome: &ExecOutcome,
    tracker: Tracker,
    reporter: &mut dyn Reporter,
) -> Result<FirstRunStep, PrepareError> {
    if tracker.unsupported {
        return Err(failed(
            tracker,
            ErrorCode::RepoInvalid,
            DownloadFailure::Other,
            "evento do models sem versão conhecida (modelsUnsupported)".to_string(),
            None,
        ));
    }
    if outcome.timed_out || outcome.signal.is_some() {
        return Err(failed(
            tracker,
            ErrorCode::DownloadFailed,
            DownloadFailure::Interrupted,
            "o download dos modelos foi interrompido".to_string(),
            None,
        ));
    }
    let code = outcome.code.unwrap_or(-1);
    match code {
        7 => {
            let shortfall = tracker
                .disk
                .map(|(free, required, _)| required.saturating_sub(free));
            Err(failed(
                tracker,
                ErrorCode::InsufficientDisk,
                DownloadFailure::DiskFull,
                "espaço em disco insuficiente para os modelos".to_string(),
                shortfall,
            ))
        }
        2 | 6 => Err(failed(
            tracker,
            ErrorCode::DownloadFailed,
            DownloadFailure::Other,
            format!("models download saiu com código {code}"),
            None,
        )),
        _ => match tracker.result.clone() {
            Some((false, reason)) => Err(failed(
                tracker,
                ErrorCode::DownloadFailed,
                failure_from_reason(reason.as_deref()),
                format!("models download falhou (código {code})"),
                None,
            )),
            Some((true, _)) if code == 0 => verify_after_download(env, tracker, reporter),
            // Sem `result` (ou código incoerente com ele): o término é tratado como interrupção.
            _ => Err(failed(
                tracker,
                ErrorCode::DownloadFailed,
                DownloadFailure::Interrupted,
                format!("models download terminou (código {code}) sem resultado"),
                None,
            )),
        },
    }
}

/// Confirma o resultado com `models status`: só `done` se todos os modelos estão presentes.
fn verify_after_download(
    env: &ModelsEnv,
    mut tracker: Tracker,
    reporter: &mut dyn Reporter,
) -> Result<FirstRunStep, PrepareError> {
    match check_models(env) {
        ModelsCheck::Checked(status) if status.all_present() => {
            tracker.step.items_total = status.models.len() as u32;
            tracker.step.items_done = tracker.step.items_total;
            tracker.step.finish_ok();
            reporter.step(&tracker.step);
            Ok(tracker.step)
        }
        ModelsCheck::Unsupported => Err(failed(
            tracker,
            ErrorCode::RepoInvalid,
            DownloadFailure::Other,
            "a verificação final do models não é compatível (modelsUnsupported)".to_string(),
            None,
        )),
        _ => Err(failed(
            tracker,
            ErrorCode::DownloadFailed,
            DownloadFailure::Other,
            "modelos ainda ausentes após o download".to_string(),
            None,
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stack::secret::Masked;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    const FAKE_UV: &str = r#"#!/bin/sh
d=$(dirname "$0")
echo "$@" >> "$d/calls.log"
case " $* " in
  *" models status --json "*)
    cat "$d/status.out" 2>/dev/null
    exit $(cat "$d/status.code" 2>/dev/null || echo 0);;
  *" models download --json "*)
    cat "$d/download.out" 2>/dev/null
    [ -e "$d/download.kill" ] && kill -9 $$
    code=$(cat "$d/download.code" 2>/dev/null || echo 0)
    [ "$code" = 0 ] && [ -e "$d/status.after" ] && cp "$d/status.after" "$d/status.out"
    exit "$code";;
esac
exit 99
"#;

    fn status_line(layout: bool, tableformer: bool) -> String {
        format!(
            "{{\"schema_version\":1,\"event\":\"status\",\"cache_dir\":\"/cache/hf\",\"models\":[{{\"id\":\"layout\",\"present\":{layout}}},{{\"id\":\"tableformer\",\"present\":{tableformer}}}]}}\n"
        )
    }

    const ALL_PRESENT: (bool, bool) = (true, true);

    #[derive(Default)]
    struct Rec {
        stages: Vec<OperationStage>,
        lines: Vec<String>,
        steps: Vec<FirstRunStep>,
    }

    impl Reporter for Rec {
        fn stage(&mut self, stage: OperationStage) {
            self.stages.push(stage);
        }
        fn line(&mut self, line: Masked) {
            self.lines.push(line.as_str().to_string());
        }
        fn step(&mut self, step: &FirstRunStep) {
            self.steps.push(step.clone());
        }
    }

    struct Fixed(bool);
    impl Confirmer for Fixed {
        fn confirm(&self, _t: &str, _m: &str) -> bool {
            self.0
        }
    }

    struct Fx {
        _tmp: tempfile::TempDir,
        bin: PathBuf,
        env: ModelsEnv,
    }

    impl Fx {
        fn new() -> Fx {
            let tmp = tempfile::tempdir().unwrap();
            let root = fs::canonicalize(tmp.path()).unwrap();
            let bin = root.join("bin");
            let repo = root.join("repo com espaço");
            fs::create_dir_all(&bin).unwrap();
            fs::create_dir_all(&repo).unwrap();
            let uv = bin.join("uv");
            fs::write(&uv, FAKE_UV).unwrap();
            fs::set_permissions(&uv, fs::Permissions::from_mode(0o755)).unwrap();
            let fx = Fx {
                env: ModelsEnv {
                    uv,
                    repo,
                    path_dirs: vec![bin.clone()],
                },
                _tmp: tmp,
                bin,
            };
            let (a, b) = ALL_PRESENT;
            fx.set("status.out", &status_line(a, b));
            fx
        }

        fn set(&self, name: &str, text: &str) {
            fs::write(self.bin.join(name), text).unwrap();
        }

        fn calls(&self) -> String {
            fs::read_to_string(self.bin.join("calls.log")).unwrap_or_default()
        }

        fn download(&self, rec: &mut Rec) -> Result<FirstRunStep, PrepareError> {
            prepare_models(&self.env, &Fixed(true), rec)
        }
    }

    /// Eventos do contrato de um download completo.
    fn full_download() -> String {
        [
            r#"{"schema_version":1,"event":"plan","total_bytes":3000,"missing":["layout","tableformer"]}"#,
            r#"{"schema_version":1,"event":"disk","free_bytes":900000,"required_bytes":3000,"ok":true}"#,
            r#"{"schema_version":1,"event":"progress","id":"layout","downloaded_bytes":500,"total_bytes":1000}"#,
            r#"{"schema_version":1,"event":"progress","id":"tableformer","downloaded_bytes":700,"total_bytes":null}"#,
            r#"{"schema_version":1,"event":"model_done","id":"layout"}"#,
            r#"{"schema_version":1,"event":"progress","id":"tableformer","downloaded_bytes":2000,"total_bytes":2000}"#,
            r#"{"schema_version":1,"event":"model_done","id":"tableformer"}"#,
            r#"{"schema_version":1,"event":"result","status":"done","reason":null,"message":"Modelos de conversão prontos."}"#,
        ]
        .join("\n")
            + "\n"
    }

    #[test]
    fn status_runs_through_uv_with_the_repo_project_and_no_sync() {
        let fx = Fx::new();
        fx.set("status.out", &status_line(true, false));
        let ModelsCheck::Checked(status) = check_models(&fx.env) else {
            panic!("esperava Checked");
        };
        assert!(!status.all_present());
        assert_eq!(status.cache_dir, "/cache/hf");
        let step = status.to_step();
        assert_eq!(step.state, StepState::Pending);
        assert_eq!((step.items_done, step.items_total), (1, 2));
        assert_eq!(step.failure, None);
        let calls = fx.calls();
        assert!(
            calls.contains(&format!(
                "run --no-sync --project {} artigo2md models status --json",
                fx.env.repo.display()
            )),
            "{calls}"
        );

        fx.set("status.out", &status_line(true, true));
        let ModelsCheck::Checked(status) = check_models(&fx.env) else {
            panic!("esperava Checked");
        };
        assert_eq!(status.to_step().state, StepState::Done);
    }

    #[test]
    fn status_unsupported_cases_become_models_unsupported() {
        let fx = Fx::new();
        // Subcomando ausente: uso inválido.
        fx.set("status.code", "2");
        assert_eq!(check_models(&fx.env), ModelsCheck::Unsupported);
        fx.set("status.code", "0");
        // Sem schema_version e com versão desconhecida: o resto da linha não é interpretado.
        fx.set(
            "status.out",
            "{\"event\":\"status\",\"cache_dir\":\"/c\",\"models\":[{\"id\":\"a\",\"present\":true}]}\n",
        );
        assert_eq!(check_models(&fx.env), ModelsCheck::Unsupported);
        fx.set(
            "status.out",
            "{\"schema_version\":2,\"event\":\"status\",\"cache_dir\":\"/c\",\"models\":[]}\n",
        );
        assert_eq!(check_models(&fx.env), ModelsCheck::Unsupported);
        // Saída 0 sem nenhum evento reconhecível.
        fx.set("status.out", "lixo\n");
        assert_eq!(check_models(&fx.env), ModelsCheck::Unsupported);
    }

    #[test]
    fn status_other_exit_codes_and_missing_uv_are_not_unsupported() {
        let fx = Fx::new();
        fx.set("status.code", "6");
        assert_eq!(check_models(&fx.env), ModelsCheck::PlatformUnsupported);
        for code in ["1", "3", "127"] {
            fx.set("status.code", code);
            assert_eq!(check_models(&fx.env), ModelsCheck::Failed, "código {code}");
        }
        let mut env = fx.env.clone();
        env.uv = fx.bin.join("nao-existe");
        assert_eq!(check_models(&env), ModelsCheck::Failed);
    }

    #[test]
    fn without_confirmation_nothing_executes() {
        let fx = Fx::new();
        let mut rec = Rec::default();
        let err = prepare_models(&fx.env, &Fixed(false), &mut rec).unwrap_err();
        assert_eq!(err.error.code, ErrorCode::ConfirmationRequired);
        assert_eq!(fx.calls(), "");
        assert!(rec.stages.is_empty() && rec.steps.is_empty());
    }

    #[test]
    fn download_consumes_events_in_order_and_confirms_by_status() {
        let fx = Fx::new();
        fx.set("status.out", &status_line(false, false));
        fx.set("status.after", &status_line(true, true));
        fx.set("download.out", &full_download());
        let mut rec = Rec::default();
        let step = fx.download(&mut rec).unwrap();
        assert_eq!(step.state, StepState::Done);
        assert_eq!((step.items_done, step.items_total), (2, 2));
        assert_eq!(
            rec.stages,
            [
                OperationStage::CheckingDisk,
                OperationStage::DownloadingModels
            ]
        );
        // A ordem: o `download` roda e só depois o `status` de confirmação.
        let calls = fx.calls();
        let download = calls.find("models download --json").unwrap();
        let status = calls.find("models status --json").unwrap();
        assert!(download < status, "{calls}");

        // Bytes somados por modelo (500 + 700, depois 500 + 2000) e total do plano.
        let downloading: Vec<&FirstRunStep> = rec
            .steps
            .iter()
            .filter(|s| s.state == StepState::Downloading)
            .collect();
        let sums: Vec<u64> = downloading
            .iter()
            .filter_map(|s| s.downloaded_bytes)
            .collect();
        assert!(sums.contains(&1200) && sums.contains(&2500), "{sums:?}");
        assert!(downloading.iter().any(|s| s.total_bytes == Some(3000)));
        assert!(downloading
            .iter()
            .all(|s| s.updated_at.is_some() || s.items_done == 0));
        assert!(downloading.iter().any(|s| s.items_done == 1));
        assert_eq!(rec.steps.last().unwrap().state, StepState::Done);
    }

    #[test]
    fn unknown_events_and_noise_are_tolerated() {
        let fx = Fx::new();
        fx.set("status.after", &status_line(true, true));
        fx.set(
            "download.out",
            &format!(
                "ruído em stdout\n{{\"schema_version\":1,\"event\":\"novo\",\"x\":1}}\n{}",
                full_download()
            ),
        );
        let step = fx.download(&mut Rec::default()).unwrap();
        assert_eq!(step.state, StepState::Done);
    }

    #[test]
    fn insufficient_disk_exit_7_reports_the_shortfall_and_never_done() {
        let fx = Fx::new();
        fx.set(
            "download.out",
            concat!(
                "{\"schema_version\":1,\"event\":\"plan\",\"total_bytes\":3000,\"missing\":[\"layout\"]}\n",
                "{\"schema_version\":1,\"event\":\"disk\",\"free_bytes\":1000,\"required_bytes\":3000,\"ok\":false}\n",
                "{\"schema_version\":1,\"event\":\"result\",\"status\":\"failed\",\"reason\":\"disk_full\",\"message\":\"x\"}\n"
            ),
        );
        fx.set("download.code", "7");
        let mut rec = Rec::default();
        let err = fx.download(&mut rec).unwrap_err();
        assert_eq!(err.error.code, ErrorCode::InsufficientDisk);
        assert_eq!(err.shortfall_bytes, Some(2000));
        assert_eq!(err.step.as_ref().unwrap().state, StepState::Failed);
        // O estado "baixando modelos" nunca foi anunciado: o disco não passou.
        assert!(!rec.stages.contains(&OperationStage::DownloadingModels));
    }

    #[test]
    fn exit_1_uses_the_reason_of_the_result_event() {
        for (reason, expected) in [
            ("no_network", DownloadFailure::NoNetwork),
            ("disk_full", DownloadFailure::DiskFull),
            ("interrupted", DownloadFailure::Interrupted),
            ("other", DownloadFailure::Other),
            ("motivo-desconhecido", DownloadFailure::Other),
        ] {
            let fx = Fx::new();
            fx.set(
                "download.out",
                &format!(
                    "{{\"schema_version\":1,\"event\":\"result\",\"status\":\"failed\",\"reason\":\"{reason}\",\"message\":\"m\"}}\n"
                ),
            );
            fx.set("download.code", "1");
            let err = fx.download(&mut Rec::default()).unwrap_err();
            assert_eq!(err.error.code, ErrorCode::DownloadFailed, "{reason}");
            assert_eq!(err.failure, Some(expected), "{reason}");
            assert_eq!(err.step.unwrap().state, StepState::Failed);
        }
    }

    #[test]
    fn exits_2_and_6_are_other_and_missing_result_is_interrupted() {
        for code in ["2", "6"] {
            let fx = Fx::new();
            fx.set("download.code", code);
            let err = fx.download(&mut Rec::default()).unwrap_err();
            assert_eq!(err.failure, Some(DownloadFailure::Other), "código {code}");
        }
        // Término sem `result`, com qualquer código (inclusive 0).
        for code in ["0", "1", "5"] {
            let fx = Fx::new();
            fx.set("download.code", code);
            fx.set(
                "download.out",
                "{\"schema_version\":1,\"event\":\"plan\",\"total_bytes\":1,\"missing\":[\"layout\"]}\n",
            );
            let err = fx.download(&mut Rec::default()).unwrap_err();
            assert_eq!(
                err.failure,
                Some(DownloadFailure::Interrupted),
                "código {code}"
            );
            assert_eq!(err.step.unwrap().state, StepState::Failed);
        }
        // Processo morto por sinal: nunca `done`.
        let fx = Fx::new();
        fx.set("download.kill", "");
        let err = fx.download(&mut Rec::default()).unwrap_err();
        assert_eq!(err.failure, Some(DownloadFailure::Interrupted));
    }

    #[test]
    fn result_done_is_not_trusted_without_the_status_check() {
        let fx = Fx::new();
        // O processo disse `done`, mas o status continua mostrando um modelo ausente.
        fx.set("status.out", &status_line(true, false));
        fx.set("download.out", &full_download());
        let err = fx.download(&mut Rec::default()).unwrap_err();
        assert_eq!(err.failure, Some(DownloadFailure::Other));
        assert_eq!(err.step.unwrap().state, StepState::Failed);
    }

    #[test]
    fn event_with_unknown_version_mid_download_is_repo_invalid() {
        let fx = Fx::new();
        fx.set("status.after", &status_line(true, true));
        fx.set(
            "download.out",
            concat!(
                "{\"schema_version\":1,\"event\":\"plan\",\"total_bytes\":1,\"missing\":[\"layout\"]}\n",
                "{\"schema_version\":9,\"event\":\"progress\",\"id\":\"layout\",\"downloaded_bytes\":1}\n",
                "{\"schema_version\":1,\"event\":\"result\",\"status\":\"done\",\"reason\":null,\"message\":\"m\"}\n"
            ),
        );
        let mut rec = Rec::default();
        let err = fx.download(&mut rec).unwrap_err();
        assert_eq!(err.error.code, ErrorCode::RepoInvalid);
        assert_ne!(err.step.unwrap().state, StepState::Done);
        // Nada depois da linha incompatível foi interpretado: o `result` não chegou ao estado.
        assert!(rec.steps.iter().all(|s| s.downloaded_bytes.is_none()));
    }

    #[test]
    fn parser_checks_the_version_before_anything_else() {
        assert_eq!(parse_event("texto"), ParsedLine::Ignored);
        assert_eq!(parse_event("[1]"), ParsedLine::Ignored);
        assert_eq!(parse_event("{quebrado"), ParsedLine::Ignored);
        assert_eq!(
            parse_event(r#"{"event":"status"}"#),
            ParsedLine::Unsupported
        );
        assert_eq!(
            parse_event(r#"{"schema_version":"1","event":"status"}"#),
            ParsedLine::Unsupported
        );
        assert_eq!(
            parse_event(r#"{"schema_version":2}"#),
            ParsedLine::Unsupported
        );
        assert_eq!(parse_event(r#"{"schema_version":1}"#), ParsedLine::Ignored);
        assert_eq!(
            parse_event(r#"{"schema_version":1,"event":"model_done"}"#),
            ParsedLine::Ignored
        );
        assert_eq!(
            parse_event(
                r#"{"schema_version":1,"event":"progress","id":"layout","downloaded_bytes":5,"total_bytes":null}"#
            ),
            ParsedLine::Event(ModelsEvent::Progress {
                id: "layout".to_string(),
                downloaded_bytes: 5,
                total_bytes: None
            })
        );
        assert_eq!(
            parse_event(
                r#"{"schema_version":1,"event":"result","status":"failed","reason":null,"message":"x"}"#
            ),
            ParsedLine::Event(ModelsEvent::Result {
                done: false,
                reason: None,
                message: Some("x".to_string())
            })
        );
    }

    #[test]
    fn confirmation_text_states_size_internet_and_location() {
        let (_, message) = confirmation_text();
        assert!(message.contains("cerca de 3 GB livres"));
        assert!(message.contains("internet"));
        assert!(message.contains("cache do Hugging Face"));
    }
}
