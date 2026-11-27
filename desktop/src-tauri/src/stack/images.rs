//! Etapa "imagens da infraestrutura" (spec FR-030 a FR-035; research Decision 8; plan.md M15, M16).
//!
//! A verificação (`check`) lista as imagens da stack e decide o estado por `docker image inspect`
//! e não baixa nada; só `prepare_images`, depois da confirmação, baixa e constrói.

use std::collections::{HashMap, HashSet};
use std::time::Duration;

use crate::error::{AppError, AppResult, ErrorCode};
use crate::stack::env_file;
use crate::stack::exec::{self, images_after_separator, ExecOutcome, Stream};
use crate::stack::first_run::{
    Confirmer, DownloadFailure, FirstRunStep, StepKind, StepState, IMAGES_APPROX_BYTES,
    IMAGES_APPROX_TEXT, IMAGES_LOCATION,
};
use crate::stack::lifecycle::{OperationStage, Reporter};
use crate::stack::runtime::DockerEnv;
use crate::stack::secret::Masker;

/// Tempo máximo de `compose config --images` e de `image inspect`.
pub const IMAGE_QUERY_TIMEOUT: Duration = Duration::from_secs(15);

/// Estado das imagens da stack: concluída quando todas estão presentes (research Decision 8).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImagesStatus {
    pub images: Vec<String>,
    pub missing: Vec<String>,
}

impl ImagesStatus {
    pub fn all_present(&self) -> bool {
        self.missing.is_empty()
    }
}

/// Nome de imagem aceito: não vazio, sem espaço nem controle (vem da saída do Compose, que lê o
/// `compose.yaml` do repositório confiável; a validação é defesa em profundidade, M15).
fn is_plausible_image_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 256
        && name
            .chars()
            .all(|c| c.is_ascii_graphic() && !c.is_ascii_control())
}

/// `docker compose … config --images`: uma imagem por linha (nunca `config` sem filtro, que
/// imprimiria o arquivo com as credenciais interpoladas). Só a saída padrão conta.
pub fn list_images(env: &DockerEnv) -> AppResult<Vec<String>> {
    let spec = env
        .compose_spec(IMAGE_QUERY_TIMEOUT)
        .args(["config", "--images"]);
    let (outcome, lines) = exec::run_collect(spec, &Masker::new())?;
    if !outcome.success() {
        return Err(AppError::new(
            ErrorCode::ScriptFailed,
            format!(
                "compose config --images falhou (código {:?}, tempo esgotado: {})",
                outcome.code, outcome.timed_out
            ),
        ));
    }
    let mut images: Vec<String> = Vec::new();
    for line in lines.iter().filter(|l| l.stream == Stream::Stdout) {
        let name = line.text.as_str().trim();
        if is_plausible_image_name(name) && !images.iter().any(|i| i == name) {
            images.push(name.to_string());
        }
    }
    Ok(images)
}

/// `docker image inspect -- <imagem>` com saída 0 = imagem presente (`stack/up.sh`, mesma checagem).
pub fn image_present(env: &DockerEnv, image: &str) -> AppResult<bool> {
    let spec = env
        .docker_spec(IMAGE_QUERY_TIMEOUT)
        .args(["image", "inspect"])
        .args(images_after_separator(&[image]));
    let (outcome, _) = exec::run_collect(spec, &Masker::new())?;
    Ok(outcome.success())
}

/// Lista as imagens da stack e separa as ausentes. Sem nenhuma imagem listada o resultado é um
/// erro (o `compose.yaml` não tem o que baixar: algo está errado com o repositório).
pub fn check(env: &DockerEnv) -> AppResult<ImagesStatus> {
    let images = list_images(env)?;
    if images.is_empty() {
        return Err(AppError::new(
            ErrorCode::ScriptFailed,
            "compose config --images não listou nenhuma imagem",
        ));
    }
    let mut missing = Vec::new();
    for image in &images {
        if !image_present(env, image)? {
            missing.push(image.clone());
        }
    }
    Ok(ImagesStatus { images, missing })
}

// ---------------------------------------------------------------------------------------------
// Preparação das imagens (tarefas 7.3.2 a 7.3.4)
// ---------------------------------------------------------------------------------------------

/// Imagem construída localmente a partir do `compose.yaml` (`stack/up.sh`, variável `API_IMAGE`);
/// o `pull --ignore-buildable` não a baixa, o `build api` a cria.
pub const API_IMAGE: &str = "artigo2md-api:local";

/// Tetos de `pull` e `build` (M20): são operações longas e interrompíveis, sem prazo curto.
pub const PULL_TIMEOUT: Duration = Duration::from_secs(3600);
pub const BUILD_TIMEOUT: Duration = Duration::from_secs(3600);

/// Trechos de mensagem de erro (em minúsculas) que indicam falta de rede. São as mensagens
/// padrão de erros de rede do Go e do sistema operacional (`no such host`, `i/o timeout`, …); o
/// texto real do Compose é conferido na tarefa 11.1.
const NETWORK_MARKERS: [&str; 8] = [
    "no such host",
    "i/o timeout",
    "network is unreachable",
    "connection refused",
    "tls handshake timeout",
    "temporary failure in name resolution",
    "dial tcp",
    "no route to host",
];

/// Uma linha JSON de `--progress json`. Tolerante: campos ausentes viram `None`/`false` e campos
/// desconhecidos são ignorados (contracts/external-processes.md). `current`/`total` (bytes) só
/// aparecem nas linhas "Downloading" de uma camada (observado em 11.1, Compose v5.6.1).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ProgressLine {
    pub id: Option<String>,
    /// `Image <nome>` na linha de uma camada.
    pub parent_id: Option<String>,
    pub status: Option<String>,
    pub text: Option<String>,
    pub error: bool,
    pub message: Option<String>,
    pub current: Option<u64>,
    pub total: Option<u64>,
}

impl ProgressLine {
    /// Nome da imagem quando o `id` é `Image <nome>`.
    pub fn image(&self) -> Option<&str> {
        self.id.as_deref()?.strip_prefix("Image ")
    }
}

/// `None` para linha que não é um objeto JSON (texto livre do Compose ou do Docker).
pub fn parse_progress_line(line: &str) -> Option<ProgressLine> {
    let trimmed = line.trim();
    if !trimmed.starts_with('{') {
        return None;
    }
    let value: serde_json::Value = serde_json::from_str(trimmed).ok()?;
    let object = value.as_object()?;
    let text_of = |key: &str| object.get(key).and_then(|v| v.as_str()).map(str::to_string);
    let number_of = |key: &str| object.get(key).and_then(|v| v.as_u64());
    Some(ProgressLine {
        id: text_of("id"),
        parent_id: text_of("parent_id"),
        status: text_of("status"),
        text: text_of("text"),
        error: object
            .get("error")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        message: text_of("message"),
        current: number_of("current"),
        total: number_of("total"),
    })
}

/// Traduz o fim de um `pull`/`build` com falha: tempo esgotado, sinal ou interrupção = `interrupted`;
/// `no space left on device` = `diskFull`; marcadores de rede = `noNetwork`; o resto = `other`.
pub fn classify_failure(outcome: &ExecOutcome, messages: &[String]) -> DownloadFailure {
    if outcome.timed_out || outcome.signal.is_some() || outcome.code == Some(130) {
        return DownloadFailure::Interrupted;
    }
    let text = messages.join("\n").to_lowercase();
    if text.contains("no space left on device") {
        DownloadFailure::DiskFull
    } else if NETWORK_MARKERS.iter().any(|m| text.contains(m)) {
        DownloadFailure::NoNetwork
    } else {
        DownloadFailure::Other
    }
}

/// Falha de `prepare_images` (mesmo tipo de `prepare_models`).
pub use crate::stack::first_run::PrepareError as ImagesError;

/// Texto do diálogo nativo de confirmação (FR-031): tamanho aproximado, uso da internet e destino.
pub fn confirmation_text() -> (&'static str, String) {
    (
        "Baixar as imagens da infraestrutura",
        format!(
            "Serão baixadas as imagens da infraestrutura ({}). O download usa a internet e os \
             arquivos ficam no {}. Deseja continuar?",
            IMAGES_APPROX_TEXT, IMAGES_LOCATION
        ),
    )
}

fn masker_from_env(env: &DockerEnv) -> Masker {
    let mut masker = Masker::new();
    if let Ok(Some(parsed)) = env_file::load(&env_file::env_path(&env.repo)) {
        parsed.register_secrets(&mut masker);
    }
    masker
}

/// Estado do acompanhamento de um `pull`/`build` (imagens concluídas e atividade).
struct Tracker {
    expected: Vec<String>,
    done: HashSet<String>,
    built: u32,
    step: FirstRunStep,
    /// Bytes observados por camada (`id` da camada → (baixados, total)), só das linhas "Downloading".
    layers: HashMap<String, (u64, u64)>,
}

impl Tracker {
    fn items_done(&self) -> u32 {
        self.done.len() as u32 + self.built
    }

    fn on_progress(&mut self, line: &ProgressLine, reporter: &mut dyn Reporter) {
        if let (Some(image), Some("Done")) = (line.image(), line.status.as_deref()) {
            if self.expected.iter().any(|e| e == image) {
                self.done.insert(image.to_string());
            }
        }
        self.track_layer_bytes(line);
        // Toda linha de progresso é sinal de atividade (FR-032), mesmo sem bytes.
        self.step.items_done = self.items_done();
        self.step.touch();
        reporter.step(&self.step);
    }
}

impl Tracker {
    /// Soma os bytes que o Compose informou por camada. Só as linhas "Downloading" trazem `current` e
    /// `total`; uma camada que termina sem ter sido reportada não soma nada, então o valor é um mínimo
    /// observado e o total geral fica desconhecido (a interface mostra o volume, não um percentual).
    fn track_layer_bytes(&mut self, line: &ProgressLine) {
        let (Some(layer), Some(_parent)) = (line.id.as_deref(), line.parent_id.as_deref()) else {
            return;
        };
        match (line.text.as_deref(), line.current, line.total) {
            (Some("Downloading"), Some(current), total) => {
                let entry = self.layers.entry(layer.to_string()).or_insert((0, 0));
                entry.1 = entry.1.max(total.unwrap_or(0));
                entry.0 = entry.0.max(current);
            }
            (Some("Download complete" | "Pull complete"), _, _) => {
                if let Some(entry) = self.layers.get_mut(layer) {
                    entry.0 = entry.0.max(entry.1);
                }
            }
            _ => return,
        }
        self.step.downloaded_bytes = Some(self.layers.values().map(|(done, _)| *done).sum());
    }
}

fn run_compose_step(
    env: &DockerEnv,
    args: &[&str],
    timeout: Duration,
    masker: &Masker,
    tracker: &mut Tracker,
    reporter: &mut dyn Reporter,
) -> AppResult<(ExecOutcome, Vec<String>)> {
    let spec = env.compose_spec(timeout).args(args);
    let mut messages: Vec<String> = Vec::new();
    let outcome = exec::run(spec, masker, |line| {
        let text = line.text;
        match parse_progress_line(text.as_str()) {
            Some(progress) => {
                if progress.error {
                    if let Some(message) = &progress.message {
                        messages.push(message.clone());
                    }
                }
                tracker.on_progress(&progress, reporter);
            }
            None => messages.push(text.as_str().to_string()),
        }
        if messages.len() > 200 {
            messages.remove(0);
        }
        reporter.line(text);
    })?;
    Ok((outcome, messages))
}

/// `prepare_images`: confirma, confere o espaço, garante o `.env` e baixa/constrói só o que falta.
/// Sem confirmação nada é executado; espaço insuficiente não inicia o download (FR-031, FR-033).
pub fn prepare_images(
    env: &DockerEnv,
    data_dir: &str,
    free_bytes: &dyn Fn() -> Option<u64>,
    confirmer: &dyn Confirmer,
    reporter: &mut dyn Reporter,
) -> Result<FirstRunStep, ImagesError> {
    let (title, message) = confirmation_text();
    if !confirmer.confirm(title, &message) {
        return Err(AppError::new(
            ErrorCode::ConfirmationRequired,
            "a pessoa não confirmou o download das imagens",
        )
        .into());
    }

    reporter.stage(OperationStage::CheckingDisk);
    let free = free_bytes().ok_or_else(|| {
        AppError::new(
            ErrorCode::Io,
            "não foi possível ler o espaço livre do disco",
        )
    })?;
    if free < IMAGES_APPROX_BYTES {
        return Err(ImagesError {
            error: AppError::new(
                ErrorCode::InsufficientDisk,
                format!("espaço livre {free} B, necessário {IMAGES_APPROX_BYTES} B"),
            ),
            failure: None,
            shortfall_bytes: Some(IMAGES_APPROX_BYTES - free),
            step: None,
        });
    }

    reporter.stage(OperationStage::EnsuringParams);
    env_file::ensure(&env_file::env_path(&env.repo), data_dir)?;
    let masker = masker_from_env(env);

    reporter.stage(OperationStage::CheckingImages);
    let status = check(env)?;
    let mut step = FirstRunStep::new(StepKind::Images);
    if status.all_present() {
        step.finish_ok();
        return Ok(step);
    }

    let to_pull: Vec<String> = status
        .missing
        .iter()
        .filter(|i| i.as_str() != API_IMAGE)
        .cloned()
        .collect();
    let build_api = status.missing.iter().any(|i| i == API_IMAGE);
    step.state = StepState::Downloading;
    step.items_total = (to_pull.len() + usize::from(build_api)) as u32;
    let mut tracker = Tracker {
        expected: to_pull.clone(),
        done: HashSet::new(),
        built: 0,
        step,
        layers: HashMap::new(),
    };
    reporter.step(&tracker.step);

    let fail = |tracker: &mut Tracker, failure: DownloadFailure, detail: String| {
        tracker.step.finish_failed(failure);
        ImagesError {
            error: AppError::new(ErrorCode::DownloadFailed, detail),
            failure: Some(failure),
            shortfall_bytes: None,
            step: Some(Box::new(tracker.step.clone())),
        }
    };

    if !to_pull.is_empty() {
        reporter.stage(OperationStage::PullingImages);
        let (outcome, messages) = run_compose_step(
            env,
            &["--progress", "json", "pull", "--ignore-buildable"],
            PULL_TIMEOUT,
            &masker,
            &mut tracker,
            reporter,
        )?;
        if !outcome.success() {
            let failure = classify_failure(&outcome, &messages);
            return Err(fail(
                &mut tracker,
                failure,
                format!("pull das imagens falhou (código {:?})", outcome.code),
            ));
        }
    }
    if build_api {
        reporter.stage(OperationStage::BuildingApi);
        let (outcome, messages) = run_compose_step(
            env,
            &["--progress", "json", "build", "api"],
            BUILD_TIMEOUT,
            &masker,
            &mut tracker,
            reporter,
        )?;
        if !outcome.success() {
            let failure = classify_failure(&outcome, &messages);
            return Err(fail(
                &mut tracker,
                failure,
                format!("build da imagem da API falhou (código {:?})", outcome.code),
            ));
        }
        tracker.built = 1;
    }

    // O resultado final vem da própria verificação, nunca do que o processo disse.
    reporter.stage(OperationStage::CheckingImages);
    if check(env)?.all_present() {
        tracker.step.items_done = tracker.step.items_total;
        tracker.step.finish_ok();
        reporter.step(&tracker.step);
        Ok(tracker.step)
    } else {
        Err(fail(
            &mut tracker,
            DownloadFailure::Other,
            "imagens ainda ausentes após o download".to_string(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stack::secret::Masked;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::path::PathBuf;

    const FAKE_DOCKER: &str = r#"#!/bin/sh
d=$(dirname "$0")
echo "$@" >> "$d/calls.log"
for a in "$@"; do last=$a; done
case " $* " in
  *" config --images "*) cat "$d/images.txt"; exit 0;;
  *" image inspect "*) grep -qx "$last" "$d/present.txt" 2>/dev/null; exit $?;;
  *" pull --ignore-buildable "*)
    cat "$d/pull.out" 2>/dev/null
    [ -e "$d/pull.kill" ] && kill -9 $$
    code=$(cat "$d/pull.code" 2>/dev/null || echo 0)
    [ "$code" = 0 ] && cat "$d/pull.adds" >> "$d/present.txt"
    exit "$code";;
  *" build api "*)
    cat "$d/build.out" 2>/dev/null
    code=$(cat "$d/build.code" 2>/dev/null || echo 0)
    [ "$code" = 0 ] && echo artigo2md-api:local >> "$d/present.txt"
    exit "$code";;
esac
exit 99
"#;

    const ALL: &str = "artigo2md-api:local\npostgres:18-trixie\nrabbitmq:4.4-management\n";
    const PASSWORD: &str = "pg-Segredo-Muito-Longo-123";

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
        fn confirm(&self, _title: &str, _message: &str) -> bool {
            self.0
        }
    }

    struct Fx {
        _tmp: tempfile::TempDir,
        bin: PathBuf,
        repo: PathBuf,
        data: String,
        env: DockerEnv,
    }

    impl Fx {
        fn new() -> Fx {
            let tmp = tempfile::tempdir().unwrap();
            let root = fs::canonicalize(tmp.path()).unwrap();
            let bin = root.join("bin");
            let repo = root.join("repo");
            let data = root.join("dados");
            for d in [&bin, &repo.join("stack"), &data] {
                fs::create_dir_all(d).unwrap();
            }
            let docker = bin.join("docker");
            fs::write(&docker, FAKE_DOCKER).unwrap();
            fs::set_permissions(&docker, fs::Permissions::from_mode(0o755)).unwrap();
            let fx = Fx {
                env: DockerEnv {
                    docker,
                    context: "desktop-linux".to_string(),
                    path_dirs: vec![bin.clone()],
                    repo: repo.clone(),
                },
                _tmp: tmp,
                bin,
                repo,
                data: data.to_str().unwrap().to_string(),
            };
            fx.set("images.txt", ALL);
            fx
        }

        fn set(&self, name: &str, text: &str) {
            fs::write(self.bin.join(name), text).unwrap();
        }

        fn calls(&self) -> String {
            fs::read_to_string(self.bin.join("calls.log")).unwrap_or_default()
        }

        fn prepare(&self, rec: &mut Rec) -> Result<FirstRunStep, ImagesError> {
            prepare_images(
                &self.env,
                &self.data,
                &|| Some(50_000_000_000),
                &Fixed(true),
                rec,
            )
        }

        fn write_env_with_known_password(&self) {
            let text = format!(
                "ARTIGO2MD_DATA_DIR={}\nARTIGO2MD_POSTGRES_PORT=5432\nARTIGO2MD_RABBITMQ_PORT=5672\n\
                 ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT=15672\nARTIGO2MD_API_PORT=8778\nPOSTGRES_USER=artigo2md\n\
                 POSTGRES_PASSWORD={PASSWORD}\nPOSTGRES_DB=artigo2md\nRABBITMQ_DEFAULT_USER=artigo2md\n\
                 RABBITMQ_DEFAULT_PASS=mq-Segredo-Muito-Longo-456\n",
                self.data
            );
            let path = self.repo.join("stack/.env");
            fs::write(&path, text).unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        }
    }

    #[test]
    fn without_confirmation_nothing_is_downloaded_or_touched() {
        let fx = Fx::new();
        let mut rec = Rec::default();
        let err = prepare_images(
            &fx.env,
            &fx.data,
            &|| Some(u64::MAX),
            &Fixed(false),
            &mut rec,
        )
        .unwrap_err();
        assert_eq!(err.error.code, ErrorCode::ConfirmationRequired);
        assert_eq!(fx.calls(), "");
        assert!(!fx.repo.join("stack/.env").exists());
        assert!(rec.stages.is_empty());
    }

    #[test]
    fn insufficient_space_never_starts_and_reports_the_shortfall() {
        let fx = Fx::new();
        let mut rec = Rec::default();
        let err = prepare_images(
            &fx.env,
            &fx.data,
            &|| Some(1_000_000_000),
            &Fixed(true),
            &mut rec,
        )
        .unwrap_err();
        assert_eq!(err.error.code, ErrorCode::InsufficientDisk);
        assert_eq!(err.shortfall_bytes, Some(500_000_000));
        assert_eq!(fx.calls(), "");
        assert!(!fx.repo.join("stack/.env").exists());
        assert_eq!(rec.stages, [OperationStage::CheckingDisk]);

        let err = prepare_images(&fx.env, &fx.data, &|| None, &Fixed(true), &mut rec).unwrap_err();
        assert_eq!(err.error.code, ErrorCode::Io);
        assert_eq!(fx.calls(), "");
    }

    #[test]
    fn already_present_images_are_done_without_pull_or_build() {
        let fx = Fx::new();
        fx.set("present.txt", ALL);
        let step = fx.prepare(&mut Rec::default()).unwrap();
        assert_eq!(step.state, StepState::Done);
        let calls = fx.calls();
        assert!(
            !calls.contains("pull") && !calls.contains("build"),
            "{calls}"
        );
        assert!(fx.repo.join("stack/.env").exists());
    }

    #[test]
    fn missing_registry_images_are_pulled_with_json_progress() {
        let fx = Fx::new();
        fx.set("present.txt", "artigo2md-api:local\n");
        fx.set("pull.adds", "postgres:18-trixie\nrabbitmq:4.4-management\n");
        // Amostras do contrato (campos extras e ausentes são tolerados; linha livre é ignorada).
        fx.set(
            "pull.out",
            concat!(
                "{\"id\":\"Image postgres:18-trixie\",\"status\":\"Working\",\"text\":\"Pulling\"}\n",
                "texto livre do compose\n",
                "{\"id\":\"Image postgres:18-trixie\",\"status\":\"Done\"}\n",
                "{\"status\":\"Working\",\"campo_novo\":1}\n",
                "{\"id\":\"Image rabbitmq:4.4-management\",\"status\":\"Done\",\"text\":\"Pulled\"}\n"
            ),
        );
        let mut rec = Rec::default();
        let step = fx.prepare(&mut rec).unwrap();
        assert_eq!(step.state, StepState::Done);
        assert_eq!((step.items_done, step.items_total), (2, 2));
        assert!(step.updated_at.is_some());
        let calls = fx.calls();
        assert!(
            calls.contains("--progress json pull --ignore-buildable"),
            "{calls}"
        );
        assert!(!calls.contains("build api"));
        assert_eq!(
            rec.stages,
            [
                OperationStage::CheckingDisk,
                OperationStage::EnsuringParams,
                OperationStage::CheckingImages,
                OperationStage::PullingImages,
                OperationStage::CheckingImages
            ]
        );
        // O progresso cresce por imagem concluída e toda linha JSON é atividade.
        let done_counts: Vec<u32> = rec
            .steps
            .iter()
            .filter(|s| s.state == StepState::Downloading)
            .map(|s| s.items_done)
            .collect();
        assert!(done_counts.contains(&1) && done_counts.contains(&2));
        assert!(rec
            .steps
            .iter()
            .any(|s| s.state == StepState::Downloading && s.updated_at.is_some()));
        assert!(rec.lines.iter().any(|l| l == "texto livre do compose"));
    }

    /// 11.1.4: saída REAL de `docker compose --progress json pull alpine:3.20` (Compose v5.6.1, 2026-11-27).
    #[test]
    fn captured_pull_output_reports_downloaded_bytes_and_one_image_done() {
        let raw = include_str!("fixtures/compose-pull-progress.jsonl");
        let mut tracker = Tracker {
            expected: vec!["alpine:3.20".to_string()],
            done: HashSet::new(),
            built: 0,
            step: FirstRunStep::new(StepKind::Images),
            layers: HashMap::new(),
        };
        tracker.step.items_total = 1;
        let mut rec = Rec::default();
        let mut seen = Vec::new();
        for line in raw.lines() {
            let parsed = parse_progress_line(line).expect("toda linha capturada é JSON");
            tracker.on_progress(&parsed, &mut rec);
            seen.push(tracker.step.downloaded_bytes);
        }
        // a linha "Downloading" traz current/total em bytes
        let downloading = parse_progress_line(raw.lines().nth(2).unwrap()).unwrap();
        assert_eq!(downloading.text.as_deref(), Some("Downloading"));
        assert_eq!(
            (downloading.current, downloading.total),
            (Some(2_097_152), Some(4_092_319))
        );
        assert_eq!(downloading.parent_id.as_deref(), Some("Image alpine:3.20"));
        // antes da 1ª linha "Downloading" não há bytes; depois cresce até o total da camada e não regride
        assert_eq!(seen[0], None);
        assert_eq!(seen[1], None);
        assert_eq!(seen[2], Some(2_097_152));
        assert_eq!(*seen.last().unwrap(), Some(4_092_319));
        assert!(seen.windows(2).all(|w| w[0] <= w[1]));
        // o percentual não é derivado: o total geral é desconhecido (camadas sem "Downloading")
        assert_eq!(tracker.step.total_bytes, None);
        assert_eq!(tracker.step.items_done, 1);
    }

    #[test]
    fn missing_api_image_is_built_after_the_pull() {
        let fx = Fx::new();
        fx.set(
            "present.txt",
            "postgres:18-trixie\nrabbitmq:4.4-management\n",
        );
        let step = fx.prepare(&mut Rec::default()).unwrap();
        assert_eq!(step.state, StepState::Done);
        assert_eq!((step.items_done, step.items_total), (1, 1));
        let calls = fx.calls();
        assert!(calls.contains("--progress json build api"), "{calls}");
        assert!(!calls.contains("pull"), "{calls}");

        // Tudo ausente: pull e depois build.
        let fx = Fx::new();
        fx.set("pull.adds", "postgres:18-trixie\nrabbitmq:4.4-management\n");
        let mut rec = Rec::default();
        let step = fx.prepare(&mut rec).unwrap();
        assert_eq!((step.items_done, step.items_total), (3, 3));
        let stages = &rec.stages;
        let pull = stages
            .iter()
            .position(|s| *s == OperationStage::PullingImages);
        let build = stages
            .iter()
            .position(|s| *s == OperationStage::BuildingApi);
        assert!(pull.unwrap() < build.unwrap());
    }

    #[test]
    fn failures_are_translated_to_the_four_reasons() {
        let cases = [
            (
                "{\"error\":true,\"message\":\"Get https://registry-1.docker.io/v2/: dial tcp: lookup registry-1.docker.io: no such host\"}",
                DownloadFailure::NoNetwork,
            ),
            (
                "{\"error\":true,\"message\":\"write /var/lib/docker/x: No space left on device\"}",
                DownloadFailure::DiskFull,
            ),
            ("{\"error\":true,\"message\":\"erro qualquer\"}", DownloadFailure::Other),
            ("falha em texto livre sem marcador", DownloadFailure::Other),
        ];
        for (output, expected) in cases {
            let fx = Fx::new();
            fx.set("present.txt", "artigo2md-api:local\n");
            fx.set("pull.out", &format!("{output}\n"));
            fx.set("pull.code", "1");
            let err = fx.prepare(&mut Rec::default()).unwrap_err();
            assert_eq!(err.error.code, ErrorCode::DownloadFailed, "{output}");
            assert_eq!(err.failure, Some(expected), "{output}");
            let step = err.step.unwrap();
            assert_eq!(step.state, StepState::Failed);
            assert_eq!(step.failure, Some(expected));
        }
    }

    #[test]
    fn a_killed_pull_is_interrupted_and_never_done() {
        let fx = Fx::new();
        fx.set("present.txt", "artigo2md-api:local\n");
        fx.set("pull.kill", "");
        let err = fx.prepare(&mut Rec::default()).unwrap_err();
        assert_eq!(err.failure, Some(DownloadFailure::Interrupted));
        assert_eq!(err.step.unwrap().state, StepState::Failed);
    }

    #[test]
    fn build_failure_and_unverified_success_are_reported() {
        let fx = Fx::new();
        fx.set(
            "present.txt",
            "postgres:18-trixie\nrabbitmq:4.4-management\n",
        );
        fx.set("build.code", "1");
        fx.set(
            "build.out",
            "{\"error\":true,\"message\":\"no space left on device\"}\n",
        );
        let err = fx.prepare(&mut Rec::default()).unwrap_err();
        assert_eq!(err.failure, Some(DownloadFailure::DiskFull));

        // `pull` saiu com 0 mas a imagem continua ausente: o resultado vem da verificação.
        let fx = Fx::new();
        fx.set("present.txt", "artigo2md-api:local\n");
        let err = fx.prepare(&mut Rec::default()).unwrap_err();
        assert_eq!(err.failure, Some(DownloadFailure::Other));
        assert_eq!(err.step.unwrap().state, StepState::Failed);
    }

    #[test]
    fn credentials_in_the_output_are_masked() {
        let fx = Fx::new();
        fx.write_env_with_known_password();
        fx.set("present.txt", "artigo2md-api:local\n");
        fx.set("pull.adds", "postgres:18-trixie\nrabbitmq:4.4-management\n");
        fx.set(
            "pull.out",
            &format!("{{\"id\":\"x\",\"text\":\"senha {PASSWORD}\"}}\n{PASSWORD}\n"),
        );
        let mut rec = Rec::default();
        fx.prepare(&mut rec).unwrap();
        let all = rec.lines.join("\n");
        assert!(!all.contains(PASSWORD), "{all}");
        assert!(all.contains("***"));
    }

    #[test]
    fn progress_parser_is_tolerant() {
        let line = parse_progress_line(
            r#"{"id":"Image postgres:18-trixie","status":"Working","text":"Pulling"}"#,
        )
        .unwrap();
        assert_eq!(line.image(), Some("postgres:18-trixie"));
        assert_eq!(line.status.as_deref(), Some("Working"));
        assert!(!line.error);

        let err = parse_progress_line(r#"{"error":true,"message":"falhou"}"#).unwrap();
        assert!(err.error);
        assert_eq!(err.message.as_deref(), Some("falhou"));
        assert_eq!(err.image(), None);

        let empty = parse_progress_line("{}").unwrap();
        assert_eq!(empty, ProgressLine::default());
        // Tipos inesperados viram ausência, não erro.
        let odd = parse_progress_line(r#"{"id":5,"status":[],"error":"sim"}"#).unwrap();
        assert_eq!(odd, ProgressLine::default());
        for not_json in ["", "texto", "[1,2]", "{quebrado", "  "] {
            assert!(parse_progress_line(not_json).is_none(), "{not_json:?}");
        }
        // `id` de outro tipo de recurso não é imagem.
        let container =
            parse_progress_line(r#"{"id":"Container artigo2md-api-1","status":"Done"}"#).unwrap();
        assert_eq!(container.image(), None);
    }

    #[test]
    fn classify_failure_prefers_interruption_then_disk_then_network() {
        let exit = |code: Option<i32>, signal: Option<i32>, timed_out: bool| ExecOutcome {
            code,
            signal,
            timed_out,
        };
        let msgs = |s: &str| vec![s.to_string()];
        assert_eq!(
            classify_failure(&exit(None, Some(15), false), &msgs("no such host")),
            DownloadFailure::Interrupted
        );
        assert_eq!(
            classify_failure(&exit(Some(130), None, false), &[]),
            DownloadFailure::Interrupted
        );
        assert_eq!(
            classify_failure(&exit(None, None, true), &[]),
            DownloadFailure::Interrupted
        );
        assert_eq!(
            classify_failure(
                &exit(Some(1), None, false),
                &msgs("NO SPACE LEFT ON DEVICE; dial tcp")
            ),
            DownloadFailure::DiskFull
        );
        assert_eq!(
            classify_failure(&exit(Some(1), None, false), &msgs("read tcp: i/o timeout")),
            DownloadFailure::NoNetwork
        );
        assert_eq!(
            classify_failure(&exit(Some(1), None, false), &[]),
            DownloadFailure::Other
        );
    }

    #[test]
    fn confirmation_text_states_size_internet_and_location() {
        let (title, message) = confirmation_text();
        assert!(title.contains("imagens"));
        assert!(message.contains("cerca de 1,5 GB"));
        assert!(message.contains("internet"));
        assert!(message.contains("disco do runtime Docker"));
    }
}
