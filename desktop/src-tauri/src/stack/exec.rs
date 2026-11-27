//! Execução de processos externos com argumentos isolados (spec FR-015, FR-017; plan.md M5, M6,
//! M10, M15, M20; research Decisions 1 e 3).
//!
//! Regras desta camada:
//! - `std::process::Command` sem shell intermediário: cada argumento chega intacto.
//! - `env_clear()` e lista permitida de variáveis; `PATH` com os diretórios do sistema primeiro.
//! - `process_group(0)`, stdin nulo, tempo máximo com encerramento do grupo inteiro.
//! - Saída lida linha a linha, limitada a 4 KiB, e mascarada ANTES de qualquer buffer ou callback.
//! - Nunca formatar `Command` nem erro de spawn com `{:?}` (o `Debug` de `Command` mostra o
//!   ambiente, inclusive credenciais).

use std::collections::VecDeque;
use std::ffi::{OsStr, OsString};
use std::io::Read;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use crate::error::{AppError, AppResult, ErrorCode};
use crate::stack::secret::{Masked, Masker, Secret};

/// Tamanho máximo de uma linha de saída guardada (plan.md M20).
pub const MAX_LINE_BYTES: usize = 4096;

/// Folga lida além do limite antes de mascarar, para que um valor secreto que cruza o byte 4096
/// seja mascarado por inteiro antes do corte. Credenciais têm bem menos que isto.
const SECRET_WINDOW_BYTES: usize = 256;

/// Linhas guardadas por `run_collect` (as mais recentes); evita crescimento sem limite.
pub const MAX_COLLECTED_LINES: usize = 2000;

/// `PATH` do sistema, sempre primeiro nos filhos (plan.md M6).
pub const SYSTEM_PATH: &str = "/usr/bin:/bin:/usr/sbin:/sbin";

/// Tempo para o grupo sair após `SIGTERM` antes do `SIGKILL`.
const KILL_GRACE: Duration = Duration::from_millis(1500);

/// Tempo para esvaziar os pipes depois que o processo principal terminou (um neto pode manter o
/// pipe aberto; não esperamos por ele).
const DRAIN_GRACE: Duration = Duration::from_millis(400);

const POLL_INTERVAL: Duration = Duration::from_millis(25);

/// Variáveis do ambiente herdado que passam para os filhos (plan.md M5). `LC_*` é tratado à parte.
const INHERITED_ALLOWED: [&str; 5] = ["HOME", "USER", "LOGNAME", "TMPDIR", "LANG"];

/// Nunca chegam a um filho, nem por pedido explícito (research Decision 3, tarefa 5.3.3).
const FORBIDDEN_ENV: [&str; 2] = ["DOCKER_HOST", "DOCKER_CONTEXT"];

/// Ambiente fixo das chamadas ao `uv` (plan.md M4) e do `huggingface_hub`.
const UV_ENV: [(&str, &str); 3] = [
    ("UV_OFFLINE", "1"),
    ("UV_PYTHON_DOWNLOADS", "never"),
    ("HF_HUB_DISABLE_TELEMETRY", "1"),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stream {
    Stdout,
    Stderr,
}

/// Linha de saída já mascarada.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecLine {
    pub stream: Stream,
    pub text: Masked,
}

/// Descrição de um processo a executar. Sem `Debug` de propósito (ver cabeçalho do módulo).
pub struct ExecSpec {
    program: PathBuf,
    args: Vec<OsString>,
    cwd: Option<PathBuf>,
    extra_path_dirs: Vec<PathBuf>,
    env: Vec<(String, String)>,
    secret_env: Vec<(String, Secret)>,
    uv: bool,
    timeout: Duration,
}

impl ExecSpec {
    /// `program` precisa ser caminho absoluto já validado por `locate.rs` (ou de sistema, como
    /// `/bin/sh`); `timeout` é obrigatório: nenhum filho roda sem tempo máximo (M20).
    pub fn new(program: impl Into<PathBuf>, timeout: Duration) -> Self {
        Self {
            program: program.into(),
            args: Vec::new(),
            cwd: None,
            extra_path_dirs: Vec::new(),
            env: Vec::new(),
            secret_env: Vec::new(),
            uv: false,
            timeout,
        }
    }

    pub fn arg(mut self, arg: impl AsRef<OsStr>) -> Self {
        self.args.push(arg.as_ref().to_os_string());
        self
    }

    pub fn args<I, S>(mut self, args: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: AsRef<OsStr>,
    {
        self.args
            .extend(args.into_iter().map(|a| a.as_ref().to_os_string()));
        self
    }

    pub fn current_dir(mut self, dir: impl Into<PathBuf>) -> Self {
        self.cwd = Some(dir.into());
        self
    }

    /// Diretórios acrescentados ao `PATH` DEPOIS dos do sistema (diretórios do `docker` e do `uv`).
    pub fn path_dirs<I, P>(mut self, dirs: I) -> Self
    where
        I: IntoIterator<Item = P>,
        P: Into<PathBuf>,
    {
        self.extra_path_dirs
            .extend(dirs.into_iter().map(Into::into));
        self
    }

    /// Variável explícita (valores não secretos). `DOCKER_HOST` e `DOCKER_CONTEXT` são ignoradas.
    pub fn env(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.env.push((key.into(), value.into()));
        self
    }

    /// Variável com valor secreto: só existe no `Command::env` deste filho (plan.md M5, M19).
    pub fn secret_env(mut self, key: impl Into<String>, value: Secret) -> Self {
        self.secret_env.push((key.into(), value));
        self
    }

    /// Chamada ao `uv`: `UV_OFFLINE=1`, `UV_PYTHON_DOWNLOADS=never`, `HF_HUB_DISABLE_TELEMETRY=1`.
    pub fn for_uv(mut self) -> Self {
        self.uv = true;
        self
    }
}

/// Resultado de uma execução.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecOutcome {
    /// Código de saída; `None` se terminou por sinal.
    pub code: Option<i32>,
    pub signal: Option<i32>,
    /// O tempo máximo estourou e o grupo foi encerrado.
    pub timed_out: bool,
}

impl ExecOutcome {
    pub fn success(&self) -> bool {
        self.code == Some(0) && !self.timed_out
    }
}

/// Nome de contexto do Docker aceito em `--context=<c>` (plan.md M15): `[A-Za-z0-9._-]`.
pub fn is_valid_context_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 128
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'_' || b == b'-')
}

/// Argumento `--context=<c>`; `None` se o nome não passa na validação.
pub fn context_arg(name: &str) -> Option<OsString> {
    is_valid_context_name(name).then(|| OsString::from(format!("--context={name}")))
}

/// Argumentos de nomes de imagem, precedidos de `--` (plan.md M15): um nome que comece com `-`
/// nunca vira opção do `docker`.
pub fn images_after_separator<S: AsRef<str>>(images: &[S]) -> Vec<OsString> {
    let mut out = Vec::with_capacity(images.len() + 1);
    out.push(OsString::from("--"));
    out.extend(images.iter().map(|i| OsString::from(i.as_ref())));
    out
}

fn is_inherited_allowed(key: &OsStr) -> bool {
    match key.to_str() {
        Some(k) => INHERITED_ALLOWED.contains(&k) || k.starts_with("LC_"),
        None => false,
    }
}

/// `PATH` dos filhos: diretórios do sistema primeiro, depois os extras.
pub fn build_path(extra_dirs: &[PathBuf]) -> OsString {
    let mut path = OsString::from(SYSTEM_PATH);
    for dir in extra_dirs {
        path.push(":");
        path.push(dir);
    }
    path
}

/// Monta o ambiente completo do filho a partir do ambiente herdado informado (função pura, testável).
fn build_env(
    inherited: impl Iterator<Item = (OsString, OsString)>,
    spec: &ExecSpec,
) -> Vec<(OsString, OsString)> {
    let mut env: Vec<(OsString, OsString)> = Vec::new();
    for (key, value) in inherited {
        if is_inherited_allowed(&key) {
            env.push((key, value));
        }
    }
    env.push((OsString::from("PATH"), build_path(&spec.extra_path_dirs)));
    if spec.uv {
        for (k, v) in UV_ENV {
            env.push((OsString::from(k), OsString::from(v)));
        }
    }
    for (k, v) in &spec.env {
        env.push((OsString::from(k), OsString::from(v)));
    }
    for (k, v) in &spec.secret_env {
        env.push((OsString::from(k), OsString::from(v.expose())));
    }
    // Remoção final: vale também contra pedido explícito e contra herança.
    env.retain(|(k, _)| match k.to_str() {
        Some(name) => !FORBIDDEN_ENV.contains(&name),
        None => false,
    });
    // O último valor de cada chave vence (variável explícita sobrepõe herdada).
    let mut seen: Vec<OsString> = Vec::new();
    let mut unique: Vec<(OsString, OsString)> = Vec::new();
    for (k, v) in env.into_iter().rev() {
        if !seen.contains(&k) {
            seen.push(k.clone());
            unique.push((k, v));
        }
    }
    unique.reverse();
    unique
}

/// Lê `reader` por linhas, mascara e entrega. Cada linha guardada tem no máximo `MAX_LINE_BYTES`;
/// o que passa disso é descartado, e o mascaramento acontece antes do corte.
fn read_lines<R: Read>(mut reader: R, masker: &Masker, mut emit: impl FnMut(Masked)) {
    let cap = MAX_LINE_BYTES + SECRET_WINDOW_BYTES;
    let mut chunk = [0u8; 8192];
    let mut line: Vec<u8> = Vec::with_capacity(cap);
    let finish = |line: &mut Vec<u8>, emit: &mut dyn FnMut(Masked)| {
        let text = String::from_utf8_lossy(line);
        let text = text.trim_end_matches('\r');
        emit(masker.mask(text).truncated(MAX_LINE_BYTES));
        line.clear();
    };
    loop {
        match reader.read(&mut chunk) {
            Ok(0) => break,
            Ok(n) => {
                for &byte in &chunk[..n] {
                    if byte == b'\n' {
                        finish(&mut line, &mut emit);
                    } else if line.len() < cap {
                        line.push(byte);
                    }
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) => break,
        }
    }
    if !line.is_empty() {
        finish(&mut line, &mut emit);
    }
}

enum Message {
    Line(Stream, Masked),
    Eof,
}

fn spawn_reader<R: Read + Send + 'static>(
    reader: R,
    stream: Stream,
    masker: Arc<Masker>,
    tx: mpsc::Sender<Message>,
) {
    thread::spawn(move || {
        read_lines(reader, &masker, |line| {
            let _ = tx.send(Message::Line(stream, line));
        });
        let _ = tx.send(Message::Eof);
    });
}

fn signal_group(pgid: libc::pid_t, signal: libc::c_int) {
    // SAFETY: `killpg` só recebe inteiros; o grupo foi criado por este módulo (`process_group(0)`).
    unsafe {
        libc::killpg(pgid, signal);
    }
}

/// Encerra o grupo: `SIGTERM`, espera `KILL_GRACE` e, se preciso, `SIGKILL`. Sempre colhe o filho.
fn terminate_group(child: &mut Child) {
    let pgid = child.id() as libc::pid_t;
    signal_group(pgid, libc::SIGTERM);
    let deadline = Instant::now() + KILL_GRACE;
    while Instant::now() < deadline {
        if matches!(child.try_wait(), Ok(Some(_))) {
            break;
        }
        thread::sleep(POLL_INTERVAL);
    }
    // Netos que ignoraram o SIGTERM: o grupo inteiro cai, mesmo que o líder já tenha saído.
    signal_group(pgid, libc::SIGKILL);
    let _ = child.wait();
}

/// Executa o processo, entregando cada linha (já mascarada) a `on_line`. Bloqueia até o fim ou até
/// o tempo máximo; nunca devolve credencial em erro ou linha.
pub fn run(
    spec: ExecSpec,
    masker: &Masker,
    on_line: impl FnMut(ExecLine),
) -> AppResult<ExecOutcome> {
    let registry = CURRENT_REGISTRY.with(|c| c.borrow().clone());
    let Some(registry) = registry else {
        return run_observed(spec, masker, |_pid| {}, on_line);
    };
    let pid = std::cell::Cell::new(None);
    let result = run_observed(
        spec,
        masker,
        |p| {
            pid.set(Some(p as i32));
            registry.add(p as i32);
        },
        on_line,
    );
    // O processo já foi colhido: o grupo some do registro.
    if let Some(p) = pid.get() {
        registry.remove(p);
    }
    result
}

/// Grupos de processos das operações em andamento de UMA instância do app (iniciar/parar a stack,
/// pull/build, `models download`): ao fechar o app, cada um recebe `SIGTERM` e, passado o prazo,
/// `SIGKILL` (research Decision 12; tarefa 8.4.2). O registro é por thread (`with_registry`): o
/// código que roda a operação o instala e `run` registra cada processo que a operação inicia.
#[derive(Debug, Default)]
pub struct GroupRegistry {
    groups: std::sync::Mutex<Vec<i32>>,
}

thread_local! {
    static CURRENT_REGISTRY: std::cell::RefCell<Option<Arc<GroupRegistry>>> =
        const { std::cell::RefCell::new(None) };
}

/// Executa `f` com `registry` instalado na thread atual: todo `exec::run` dentro de `f` se registra.
pub fn with_registry<T>(registry: &Arc<GroupRegistry>, f: impl FnOnce() -> T) -> T {
    struct Restore(Option<Arc<GroupRegistry>>);
    impl Drop for Restore {
        fn drop(&mut self) {
            let previous = self.0.take();
            CURRENT_REGISTRY.with(|c| *c.borrow_mut() = previous);
        }
    }
    let previous = CURRENT_REGISTRY.with(|c| c.borrow_mut().replace(Arc::clone(registry)));
    let _restore = Restore(previous);
    f()
}

impl GroupRegistry {
    fn lock(&self) -> std::sync::MutexGuard<'_, Vec<i32>> {
        self.groups.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn add(&self, pgid: i32) {
        self.lock().push(pgid);
    }

    fn remove(&self, pgid: i32) {
        self.lock().retain(|g| *g != pgid);
    }

    /// Grupos registrados agora (ainda não colhidos).
    pub fn ids(&self) -> Vec<i32> {
        self.lock().clone()
    }

    pub fn is_empty(&self) -> bool {
        self.lock().is_empty()
    }

    /// Encerra todos os grupos registrados: `SIGTERM` a cada grupo, espera até `grace` pelo registro
    /// esvaziar (o `run` de cada operação colhe o filho e remove o grupo) e `SIGKILL` nos que
    /// restarem. Os grupos foram criados por `run` (`process_group(0)`) e continuam registrados só
    /// enquanto o filho não foi colhido, então o PID não pode ter sido reutilizado.
    pub fn terminate_all(&self, grace: Duration) {
        for pgid in self.ids() {
            signal_group(pgid, libc::SIGTERM);
        }
        let deadline = Instant::now() + grace;
        while Instant::now() < deadline && !self.is_empty() {
            thread::sleep(POLL_INTERVAL);
        }
        for pgid in self.ids() {
            signal_group(pgid, libc::SIGKILL);
        }
    }
}

/// Como `run`, mas avisa o PID do processo assim que ele existe (antes de qualquer linha). O pool de
/// workers precisa do PID para registrar o grupo (`pgid` = PID, por `process_group(0)`) e sinalizá-lo.
pub fn run_observed(
    spec: ExecSpec,
    masker: &Masker,
    on_spawn: impl FnOnce(u32),
    mut on_line: impl FnMut(ExecLine),
) -> AppResult<ExecOutcome> {
    if !spec.program.is_absolute() {
        return Err(AppError::new(
            ErrorCode::Io,
            "programa a executar precisa ser caminho absoluto",
        ));
    }
    // Credenciais do próprio filho entram no mascaramento, mesmo que o chamador as esqueça.
    let mut masker = masker.clone();
    for (_, secret) in &spec.secret_env {
        masker.register(secret);
    }
    let masker = Arc::new(masker);

    let env = build_env(std::env::vars_os(), &spec);
    let mut command = Command::new(&spec.program);
    command
        .args(&spec.args)
        .env_clear()
        .envs(env)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0);
    if let Some(cwd) = &spec.cwd {
        command.current_dir(cwd);
    }

    let mut child = command.spawn().map_err(|e| {
        // Texto do erro de E/S (kind + errno), sem `Debug` do `Command` (M10).
        let text = format!("falha ao iniciar {}: {e}", spec.program.display());
        AppError::from_masked(ErrorCode::from_spawn_error(&e), &masker.mask(&text))
    })?;
    drop(command);
    on_spawn(child.id());

    let (tx, rx) = mpsc::channel::<Message>();
    if let Some(out) = child.stdout.take() {
        spawn_reader(out, Stream::Stdout, Arc::clone(&masker), tx.clone());
    }
    if let Some(err) = child.stderr.take() {
        spawn_reader(err, Stream::Stderr, Arc::clone(&masker), tx.clone());
    }
    drop(tx);

    let started = Instant::now();
    let deadline = started + spec.timeout;
    let mut eof_count = 0;
    let mut timed_out = false;
    let mut exited_at: Option<Instant> = None;
    let mut killed_at: Option<Instant> = None;

    loop {
        match rx.recv_timeout(POLL_INTERVAL) {
            Ok(Message::Line(stream, text)) => on_line(ExecLine { stream, text }),
            Ok(Message::Eof) => eof_count += 1,
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => break,
        }
        if eof_count >= 2 {
            break;
        }
        let now = Instant::now();
        if exited_at.is_none() && matches!(child.try_wait(), Ok(Some(_))) {
            exited_at = Some(now);
        }
        if let Some(at) = exited_at {
            if now.duration_since(at) >= DRAIN_GRACE {
                break;
            }
        } else if killed_at.is_none() && now >= deadline {
            timed_out = true;
            terminate_group(&mut child);
            killed_at = Some(Instant::now());
            exited_at = killed_at;
        }
    }
    // Linhas já enfileiradas no canal antes do fim.
    while let Ok(msg) = rx.try_recv() {
        if let Message::Line(stream, text) = msg {
            on_line(ExecLine { stream, text });
        }
    }

    let status = child.wait();
    let status = status.map_err(|e| {
        AppError::from_masked(
            ErrorCode::Io,
            &masker.mask(&format!("falha ao aguardar o processo: {e}")),
        )
    })?;
    Ok(ExecOutcome {
        code: status.code(),
        signal: status.signal(),
        timed_out,
    })
}

/// Variante que guarda as linhas (as `MAX_COLLECTED_LINES` mais recentes) junto do resultado.
pub fn run_collect(spec: ExecSpec, masker: &Masker) -> AppResult<(ExecOutcome, Vec<ExecLine>)> {
    let mut lines: VecDeque<ExecLine> = VecDeque::new();
    let outcome = run(spec, masker, |line| {
        if lines.len() == MAX_COLLECTED_LINES {
            lines.pop_front();
        }
        lines.push_back(line);
    })?;
    Ok((outcome, lines.into_iter().collect()))
}

impl ErrorCode {
    /// Código para falha de `spawn`: sem permissão ou sem arquivo têm código próprio; o resto é `io`.
    fn from_spawn_error(e: &std::io::Error) -> ErrorCode {
        match e.kind() {
            std::io::ErrorKind::NotFound => ErrorCode::NotFound,
            std::io::ErrorKind::PermissionDenied => ErrorCode::PermissionDenied,
            _ => ErrorCode::Io,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::path::Path;

    fn script(dir: &Path, name: &str, body: &str) -> PathBuf {
        let path = dir.join(name);
        fs::write(&path, format!("#!/bin/sh\n{body}\n")).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        path
    }

    fn texts(lines: &[ExecLine]) -> Vec<String> {
        lines.iter().map(|l| l.text.as_str().to_string()).collect()
    }

    const T: Duration = Duration::from_secs(20);

    #[test]
    fn arguments_with_spaces_and_special_characters_arrive_intact() {
        let tmp = tempfile::tempdir().unwrap();
        let prog = script(
            tmp.path(),
            "args.sh",
            "for a in \"$@\"; do printf '%s\\n' \"$a\"; done",
        );
        let args = [
            "com espaço",
            "$(touch /tmp/artigo2md-nao-deve-existir)",
            ";rm -rf /",
            "'aspas simples'",
            "\"aspas duplas\"",
            "*",
            "`id`",
            "--opcao=a b",
        ];
        let spec = ExecSpec::new(&prog, T).args(args);
        let (outcome, lines) = run_collect(spec, &Masker::new()).unwrap();
        assert!(outcome.success());
        assert_eq!(texts(&lines), args);
        assert!(!Path::new("/tmp/artigo2md-nao-deve-existir").exists());
    }

    #[test]
    fn inherited_environment_does_not_leak() {
        std::env::set_var("ARTIGO2MD_EXEC_TEST_LEAK", "vazou");
        std::env::set_var("PYTHONPATH", "/tmp/evil");
        std::env::set_var("HF_ENDPOINT", "https://evil.example");
        std::env::set_var("DOCKER_HOST", "tcp://evil:2375");
        let tmp = tempfile::tempdir().unwrap();
        let prog = script(tmp.path(), "env.sh", "/usr/bin/env");
        let spec = ExecSpec::new(&prog, T).env("DOCKER_CONTEXT", "evil");
        let result = run_collect(spec, &Masker::new());
        for name in [
            "ARTIGO2MD_EXEC_TEST_LEAK",
            "PYTHONPATH",
            "HF_ENDPOINT",
            "DOCKER_HOST",
        ] {
            std::env::remove_var(name);
        }
        let (outcome, lines) = result.unwrap();
        assert!(outcome.success());
        let all = texts(&lines).join("\n");
        for forbidden in [
            "ARTIGO2MD_EXEC_TEST_LEAK",
            "PYTHONPATH",
            "HF_ENDPOINT",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
        ] {
            assert!(!all.contains(forbidden), "{forbidden} vazou:\n{all}");
        }
        assert!(all.contains("PATH=/usr/bin:/bin:/usr/sbin:/sbin"));
    }

    #[test]
    fn build_env_keeps_only_the_allowed_inherited_variables() {
        let inherited = [
            ("HOME", "/Users/x"),
            ("USER", "x"),
            ("LOGNAME", "x"),
            ("TMPDIR", "/tmp/"),
            ("LANG", "pt_BR.UTF-8"),
            ("LC_ALL", "pt_BR.UTF-8"),
            ("PATH", "/opt/evil"),
            ("PYTHONPATH", "/evil"),
            ("UV_INDEX_URL", "https://evil"),
            ("HTTPS_PROXY", "http://evil"),
            ("DOCKER_HOST", "tcp://evil"),
            ("DOCKER_CONTEXT", "evil"),
        ]
        .into_iter()
        .map(|(k, v)| (OsString::from(k), OsString::from(v)));
        let spec = ExecSpec::new("/bin/sh", T)
            .path_dirs(["/opt/homebrew/bin"])
            .for_uv();
        let env = build_env(inherited, &spec);
        let get = |name: &str| {
            env.iter()
                .find(|(k, _)| k == name)
                .map(|(_, v)| v.to_str().unwrap().to_string())
        };
        assert_eq!(get("HOME").as_deref(), Some("/Users/x"));
        assert_eq!(get("LC_ALL").as_deref(), Some("pt_BR.UTF-8"));
        assert_eq!(
            get("PATH").as_deref(),
            Some("/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin")
        );
        assert_eq!(get("UV_OFFLINE").as_deref(), Some("1"));
        assert_eq!(get("UV_PYTHON_DOWNLOADS").as_deref(), Some("never"));
        assert_eq!(get("HF_HUB_DISABLE_TELEMETRY").as_deref(), Some("1"));
        for gone in [
            "PYTHONPATH",
            "UV_INDEX_URL",
            "HTTPS_PROXY",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
        ] {
            assert!(get(gone).is_none(), "{gone}");
        }
        // Sem uv, as três variáveis fixas não existem.
        let plain = build_env(std::iter::empty(), &ExecSpec::new("/bin/sh", T));
        assert!(plain.iter().all(|(k, _)| k != "UV_OFFLINE"));
    }

    #[test]
    fn uv_calls_get_the_offline_variables() {
        let tmp = tempfile::tempdir().unwrap();
        let prog = script(tmp.path(), "env.sh", "/usr/bin/env");
        let (_, lines) = run_collect(ExecSpec::new(&prog, T).for_uv(), &Masker::new()).unwrap();
        let all = texts(&lines);
        assert!(all.contains(&"UV_OFFLINE=1".to_string()));
        assert!(all.contains(&"UV_PYTHON_DOWNLOADS=never".to_string()));
        assert!(all.contains(&"HF_HUB_DISABLE_TELEMETRY=1".to_string()));
    }

    #[test]
    fn extra_path_dirs_come_after_the_system_ones() {
        let tmp = tempfile::tempdir().unwrap();
        let prog = script(tmp.path(), "path.sh", "printf '%s\\n' \"$PATH\"");
        let spec = ExecSpec::new(&prog, T).path_dirs(["/opt/homebrew/bin", "/Users/x/.docker/bin"]);
        let (_, lines) = run_collect(spec, &Masker::new()).unwrap();
        assert_eq!(
            texts(&lines),
            ["/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/Users/x/.docker/bin"]
        );
    }

    fn alive(pid: libc::pid_t) -> bool {
        // SAFETY: sinal 0 só verifica a existência do processo.
        unsafe { libc::kill(pid, 0) == 0 }
    }

    #[test]
    fn timeout_terminates_the_whole_process_group() {
        let tmp = tempfile::tempdir().unwrap();
        let pidfile = tmp.path().join("grandchild.pid");
        let prog = script(tmp.path(), "slow.sh", "sleep 60 &\necho $! > \"$1\"\nwait");
        // Folga para o script gravar o pid do neto mesmo com a suíte inteira rodando em paralelo.
        let spec = ExecSpec::new(&prog, Duration::from_millis(2000)).arg(&pidfile);
        let started = Instant::now();
        let outcome = run(spec, &Masker::new(), |_| {}).unwrap();
        assert!(outcome.timed_out);
        assert!(!outcome.success());
        assert!(started.elapsed() < Duration::from_secs(10));
        let grandchild: libc::pid_t = fs::read_to_string(&pidfile)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        let mut gone = false;
        for _ in 0..40 {
            if !alive(grandchild) {
                gone = true;
                break;
            }
            thread::sleep(Duration::from_millis(50));
        }
        assert!(
            gone,
            "o neto {grandchild} sobreviveu ao encerramento do grupo"
        );
    }

    #[test]
    fn long_line_is_truncated_and_the_next_line_survives() {
        let tmp = tempfile::tempdir().unwrap();
        let prog = script(
            tmp.path(),
            "long.sh",
            "head -c 10000 /dev/zero | tr '\\0' 'a'\nprintf '\\nfim\\n'",
        );
        let (outcome, lines) = run_collect(ExecSpec::new(&prog, T), &Masker::new()).unwrap();
        assert!(outcome.success());
        let t = texts(&lines);
        assert_eq!(t.len(), 2);
        assert_eq!(t[0].len(), MAX_LINE_BYTES);
        assert!(t[0].bytes().all(|b| b == b'a'));
        assert_eq!(t[1], "fim");
    }

    #[test]
    fn secret_crossing_the_truncation_limit_is_masked_before_the_cut() {
        let tmp = tempfile::tempdir().unwrap();
        let secret = "segredo-que-cruza-o-limite";
        // O segredo começa 10 bytes antes do limite de 4096.
        let prefix = MAX_LINE_BYTES - 10;
        let prog = script(
            tmp.path(),
            "cross.sh",
            &format!("head -c {prefix} /dev/zero | tr '\\0' 'a'\nprintf '%s\\n' '{secret}'"),
        );
        let mut masker = Masker::new();
        masker.register(&Secret::new(secret));
        let (_, lines) = run_collect(ExecSpec::new(&prog, T), &masker).unwrap();
        let t = texts(&lines);
        assert_eq!(t.len(), 1);
        assert!(!t[0].contains("segredo"), "sobrou parte do segredo");
        assert!(t[0].ends_with("***"), "o segredo deveria virar ***");
        assert_eq!(t[0].len(), prefix + 3);
    }

    #[test]
    fn secrets_are_masked_in_every_line_and_reach_the_child_only_by_env() {
        let tmp = tempfile::tempdir().unwrap();
        let prog = script(
            tmp.path(),
            "secret.sh",
            "printf 'senha=%s\\n' \"$ARTIGO2MD_TEST_SECRET\"\nprintf 'erro %s\\n' \"$ARTIGO2MD_TEST_SECRET\" >&2",
        );
        let spec = ExecSpec::new(&prog, T)
            .secret_env("ARTIGO2MD_TEST_SECRET", Secret::new("Zq7-valor-secreto"));
        // O masker do chamador NÃO conhece o segredo: o do próprio spec o registra.
        let (outcome, lines) = run_collect(spec, &Masker::new()).unwrap();
        assert!(outcome.success());
        let t = texts(&lines);
        assert_eq!(t.len(), 2);
        assert!(t.contains(&"senha=***".to_string()));
        assert!(t.contains(&"erro ***".to_string()));
        assert!(lines.iter().any(|l| l.stream == Stream::Stderr));
        assert!(lines.iter().any(|l| l.stream == Stream::Stdout));
    }

    #[test]
    fn stdin_is_null_so_a_reader_does_not_block() {
        let tmp = tempfile::tempdir().unwrap();
        let prog = script(tmp.path(), "stdin.sh", "cat\necho fim");
        let (outcome, lines) =
            run_collect(ExecSpec::new(&prog, Duration::from_secs(5)), &Masker::new()).unwrap();
        assert!(outcome.success());
        assert_eq!(texts(&lines), ["fim"]);
    }

    #[test]
    fn exit_code_and_missing_program_are_reported_without_secrets() {
        let tmp = tempfile::tempdir().unwrap();
        let prog = script(tmp.path(), "exit.sh", "echo saindo\nexit 5");
        let (outcome, lines) = run_collect(ExecSpec::new(&prog, T), &Masker::new()).unwrap();
        assert_eq!(outcome.code, Some(5));
        assert!(!outcome.success());
        assert_eq!(texts(&lines), ["saindo"]);

        let spec = ExecSpec::new(tmp.path().join("nao-existe"), T)
            .secret_env("X", Secret::new("senha-do-spawn"));
        let err = run(spec, &Masker::new(), |_| {}).unwrap_err();
        assert_eq!(err.code, ErrorCode::NotFound);
        assert!(!err.detail().contains("senha-do-spawn"));

        let relative = run(ExecSpec::new("docker", T), &Masker::new(), |_| {}).unwrap_err();
        assert_eq!(relative.code, ErrorCode::Io);
    }

    #[test]
    fn grandchild_holding_the_pipe_does_not_hang_the_call() {
        let tmp = tempfile::tempdir().unwrap();
        let prog = script(tmp.path(), "bg.sh", "echo pronto\nsleep 30 &\nexit 0");
        let started = Instant::now();
        let (outcome, lines) = run_collect(ExecSpec::new(&prog, T), &Masker::new()).unwrap();
        assert!(outcome.success());
        assert_eq!(texts(&lines), ["pronto"]);
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn context_names_are_validated_and_images_follow_a_separator() {
        assert!(is_valid_context_name("desktop-linux"));
        assert!(is_valid_context_name("orb.local_1"));
        for bad in [
            "",
            "a b",
            "a/b",
            "a;b",
            "a$b",
            "a\nb",
            "ctx=é",
            &"x".repeat(129),
        ] {
            assert!(!is_valid_context_name(bad), "{bad:?}");
            assert!(context_arg(bad).is_none());
        }
        assert_eq!(
            context_arg("colima").unwrap(),
            OsString::from("--context=colima")
        );
        assert_eq!(
            images_after_separator(&["-evil", "rabbitmq:4"]),
            [
                OsString::from("--"),
                OsString::from("-evil"),
                OsString::from("rabbitmq:4")
            ]
        );
    }
}
