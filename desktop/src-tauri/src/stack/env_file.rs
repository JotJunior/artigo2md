//! Leitura, criação e edição parcial do `<repo>/stack/.env` (spec FR-010, FR-011, FR-013;
//! research Decision 5; data-model.md StackParams; plan.md M9, M10).
//!
//! - Leitura com as regras do parser do `up.sh` (`stack/up.sh:40-80`: sem `eval`, remove
//!   `export `, aspas externas, ignora chaves fora de `[A-Za-z0-9_]`). Credenciais ficam em
//!   `Secret`; nenhuma mensagem de erro traz valor, só o nome da variável.
//! - Criação só quando o arquivo não existe: temporário `.env.<aleatório>.tmp` (`create_new`,
//!   `0600`, `O_NOFOLLOW`, `fsync`) publicado sem sobrescrever (`link` + `unlink`); se o destino
//!   surgir no meio, usa-se o existente. Senhas: 32 hexadecimais de 16 bytes de `/dev/urandom`.
//! - Edição parcial: reescreve só as linhas de `ARTIGO2MD_DATA_DIR` e das três portas editáveis,
//!   preservando byte a byte as demais, com modo `0600`.
//! - Validação antes de chamar o `up.sh`, com as mesmas regras dele.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

use crate::error::{AppError, AppResult, ErrorCode};
use crate::stack::locate::current_uid;
use crate::stack::secret::{random_hex, Masker, Secret};

pub const KEY_DATA_DIR: &str = "ARTIGO2MD_DATA_DIR";
pub const KEY_POSTGRES_PORT: &str = "ARTIGO2MD_POSTGRES_PORT";
pub const KEY_RABBITMQ_PORT: &str = "ARTIGO2MD_RABBITMQ_PORT";
pub const KEY_RABBITMQ_MANAGEMENT_PORT: &str = "ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT";
pub const KEY_API_PORT: &str = "ARTIGO2MD_API_PORT";
pub const KEY_POSTGRES_USER: &str = "POSTGRES_USER";
pub const KEY_POSTGRES_PASSWORD: &str = "POSTGRES_PASSWORD";
pub const KEY_POSTGRES_DB: &str = "POSTGRES_DB";
pub const KEY_RABBITMQ_USER: &str = "RABBITMQ_DEFAULT_USER";
pub const KEY_RABBITMQ_PASS: &str = "RABBITMQ_DEFAULT_PASS";
const KEY_HOST_AUTH_METHOD: &str = "POSTGRES_HOST_AUTH_METHOD";

/// Chaves cujo valor é credencial (ficam em `Secret`).
const SECRET_KEYS: [&str; 2] = [KEY_POSTGRES_PASSWORD, KEY_RABBITMQ_PASS];
const EXAMPLE_PASSWORD: &str = "troque-esta-senha";
const MIN_PASSWORD_CHARS: usize = 12;

/// Padrões ao criar o arquivo (`stack/.env.example:22-37`; um teste confere contra o exemplo).
pub const DEFAULT_POSTGRES_PORT: u16 = 5432;
pub const DEFAULT_RABBITMQ_PORT: u16 = 5672;
pub const DEFAULT_RABBITMQ_MANAGEMENT_PORT: u16 = 15672;
pub const DEFAULT_API_PORT: u16 = 8778;
pub const DEFAULT_SERVICE_NAME: &str = "artigo2md";

/// Tamanho máximo lido de um `.env` (o real tem menos de 1 KiB).
const MAX_ENV_BYTES: u64 = 64 * 1024;

/// Caminho do `.env` dentro do repositório (fixo, `stack/up.sh:15`).
pub fn env_path(repo: &Path) -> PathBuf {
    repo.join("stack").join(".env")
}

// ── Leitura ──────────────────────────────────────────────────────────────────────

/// Problema encontrado no `.env`. Só carrega o NOME da variável, nunca um valor.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EnvIssue {
    Duplicate(String),
    DollarInValue(String),
    HostAuthMethodDefined,
    Empty(String),
    ExamplePassword(String),
    ShortPassword(String),
    BadPort(String),
    BadDataDir(String),
}

impl EnvIssue {
    pub fn variable(&self) -> &str {
        match self {
            EnvIssue::HostAuthMethodDefined => KEY_HOST_AUTH_METHOD,
            EnvIssue::Duplicate(v)
            | EnvIssue::DollarInValue(v)
            | EnvIssue::Empty(v)
            | EnvIssue::ExamplePassword(v)
            | EnvIssue::ShortPassword(v)
            | EnvIssue::BadPort(v)
            | EnvIssue::BadDataDir(v) => v,
        }
    }

    fn reason(&self) -> &'static str {
        match self {
            EnvIssue::Duplicate(_) => "aparece mais de uma vez",
            EnvIssue::DollarInValue(_) => "contém o caractere $",
            EnvIssue::HostAuthMethodDefined => "não pode ser definida",
            EnvIssue::Empty(_) => "vazia ou ausente",
            EnvIssue::ExamplePassword(_) => "ainda tem o valor de exemplo",
            EnvIssue::ShortPassword(_) => "tem menos de 12 caracteres",
            EnvIssue::BadPort(_) => "precisa ser um número de 1 a 65535",
            EnvIssue::BadDataDir(_) => "precisa ser um caminho absoluto, existente e canônico",
        }
    }

    /// `invalidParams` com o nome da variável (nunca o valor), como o contrato exige.
    pub fn into_error(self) -> AppError {
        AppError::new(
            ErrorCode::InvalidParams,
            format!("{}: {}", self.variable(), self.reason()),
        )
    }
}

enum Value {
    Plain(String),
    Secret(Secret),
}

/// Conteúdo lido do `.env`: pares na ordem do arquivo, credenciais em `Secret`. Sem `Debug`.
pub struct EnvFile {
    entries: Vec<(String, Value)>,
}

/// Espaço como o `[[:space:]]` do `sed` do `up.sh`.
fn sh_trim(s: &str) -> &str {
    s.trim_matches(|c: char| matches!(c, ' ' | '\t' | '\n' | '\r' | '\x0b' | '\x0c'))
}

/// Uma linha do `.env` como o laço do `up.sh`: `None` para linha ignorada.
fn parse_line(raw: &str) -> Option<(String, String)> {
    let mut line = sh_trim(raw);
    if line.is_empty() || line.starts_with('#') {
        return None;
    }
    if line.starts_with("export ") || line.starts_with("export\t") {
        line = sh_trim(&line["export".len()..]);
    }
    let eq = line.find('=')?;
    let key = sh_trim(&line[..eq]);
    let mut value = sh_trim(&line[eq + 1..]);
    for quote in ['"', '\''] {
        if value.len() >= 2 && value.starts_with(quote) && value.ends_with(quote) {
            value = &value[1..value.len() - 1];
            break;
        }
    }
    if key.is_empty() || !key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
        return None;
    }
    Some((key.to_string(), value.to_string()))
}

impl EnvFile {
    /// Parser do `up.sh`: para no primeiro problema, na ordem das linhas (chave repetida ou `$`).
    pub fn parse(text: &str) -> Result<EnvFile, EnvIssue> {
        let mut entries: Vec<(String, Value)> = Vec::new();
        for raw in text.lines() {
            let Some((key, value)) = parse_line(raw) else {
                continue;
            };
            if entries.iter().any(|(k, _)| *k == key) {
                return Err(EnvIssue::Duplicate(key));
            }
            if value.contains('$') {
                return Err(EnvIssue::DollarInValue(key));
            }
            let value = if SECRET_KEYS.contains(&key.as_str()) {
                Value::Secret(Secret::new(value))
            } else {
                Value::Plain(value)
            };
            entries.push((key, value));
        }
        Ok(EnvFile { entries })
    }

    fn find(&self, key: &str) -> Option<&Value> {
        self.entries.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    /// A chave está definida (mesmo com valor vazio)?
    pub fn has(&self, key: &str) -> bool {
        self.find(key).is_some()
    }

    /// Valor de uma chave não secreta (`None` para credencial: use `secret`).
    pub fn plain(&self, key: &str) -> Option<&str> {
        match self.find(key) {
            Some(Value::Plain(v)) => Some(v),
            _ => None,
        }
    }

    pub fn secret(&self, key: &str) -> Option<&Secret> {
        match self.find(key) {
            Some(Value::Secret(s)) => Some(s),
            _ => None,
        }
    }

    /// Porta de uma chave (1 a 65535); `None` se ausente ou inválida.
    pub fn port(&self, key: &str) -> Option<u16> {
        parse_port(self.plain(key)?)
    }

    pub fn data_dir(&self) -> Option<&str> {
        self.plain(KEY_DATA_DIR).filter(|v| !v.is_empty())
    }

    /// Registra as credenciais lidas no mascaramento (plan.md M10).
    pub fn register_secrets(&self, masker: &mut Masker) {
        for (_, v) in &self.entries {
            if let Value::Secret(s) = v {
                masker.register(s);
            }
        }
    }

    /// Regras do `up.sh` (`stack/up.sh:97-139`), na mesma ordem; nomeia só a variável.
    pub fn validate(&self) -> Result<(), EnvIssue> {
        if self.has(KEY_HOST_AUTH_METHOD) {
            return Err(EnvIssue::HostAuthMethodDefined);
        }
        for name in [KEY_POSTGRES_USER, KEY_POSTGRES_DB, KEY_RABBITMQ_USER] {
            if self.plain(name).is_none_or(str::is_empty) {
                return Err(EnvIssue::Empty(name.to_string()));
            }
        }
        for name in SECRET_KEYS {
            let pass = match self.secret(name) {
                Some(s) if !s.is_empty() => s.expose(),
                _ => return Err(EnvIssue::Empty(name.to_string())),
            };
            if pass == EXAMPLE_PASSWORD {
                return Err(EnvIssue::ExamplePassword(name.to_string()));
            }
            if pass.chars().count() < MIN_PASSWORD_CHARS {
                return Err(EnvIssue::ShortPassword(name.to_string()));
            }
        }
        // A porta do painel só é exigida com `--management-panel`, que o app nunca usa.
        for name in [KEY_POSTGRES_PORT, KEY_RABBITMQ_PORT, KEY_API_PORT] {
            if self.port(name).is_none() {
                return Err(EnvIssue::BadPort(name.to_string()));
            }
        }
        match self.data_dir() {
            Some(dir) if data_dir_is_valid(dir) => Ok(()),
            Some(_) => Err(EnvIssue::BadDataDir(KEY_DATA_DIR.to_string())),
            None => Err(EnvIssue::Empty(KEY_DATA_DIR.to_string())),
        }
    }
}

fn parse_port(value: &str) -> Option<u16> {
    if value.is_empty() || !value.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    value.parse::<u16>().ok().filter(|p| *p >= 1)
}

/// `ARTIGO2MD_DATA_DIR` como o `up.sh` exige: absoluto, existente, diretório e canônico.
fn data_dir_is_valid(dir: &str) -> bool {
    let path = Path::new(dir);
    path.is_absolute()
        && fs::metadata(path).map(|m| m.is_dir()).unwrap_or(false)
        && fs::canonicalize(path).is_ok_and(|c| c == path)
}

fn unsafe_env_error(why: &str) -> AppError {
    AppError::new(
        ErrorCode::InvalidParams,
        format!("stack/.env recusado: {why}"),
    )
}

/// `lstat` do `.env` (M9): `None` se não existe; erro se for link, não regular ou de outro uid.
fn lstat_regular_owned(path: &Path, uid: u32) -> AppResult<Option<fs::Metadata>> {
    match fs::symlink_metadata(path) {
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e.into()),
        Ok(m) if m.file_type().is_symlink() => Err(unsafe_env_error("é um link simbólico")),
        Ok(m) if !m.is_file() => Err(unsafe_env_error("não é um arquivo regular")),
        Ok(m) if m.uid() != uid => Err(unsafe_env_error("pertence a outro usuário")),
        Ok(m) => Ok(Some(m)),
    }
}

/// Lê e analisa o `.env`; `Ok(None)` se não existe. Problemas de conteúdo (chave repetida, `$`)
/// viram `invalidParams` com o nome da variável.
pub fn load(path: &Path) -> AppResult<Option<EnvFile>> {
    load_with_uid(path, current_uid())
}

fn load_with_uid(path: &Path, uid: u32) -> AppResult<Option<EnvFile>> {
    let text = match read_text(path, uid)? {
        Some(t) => t,
        None => return Ok(None),
    };
    EnvFile::parse(&text)
        .map(Some)
        .map_err(EnvIssue::into_error)
}

fn read_text(path: &Path, uid: u32) -> AppResult<Option<String>> {
    if lstat_regular_owned(path, uid)?.is_none() {
        return Ok(None);
    }
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)?;
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take(MAX_ENV_BYTES + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_ENV_BYTES {
        return Err(unsafe_env_error("grande demais"));
    }
    String::from_utf8(bytes)
        .map(Some)
        .map_err(|_| unsafe_env_error("não é UTF-8"))
}

// ── Criação ──────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EnsureOutcome {
    Created,
    /// Já existia: nunca é recriado nem tem credencial tocada.
    Existing,
}

/// O texto de um valor escrito pelo app precisa sobreviver ao parser do `up.sh` sem mudar.
fn value_is_writable(value: &str) -> bool {
    !value.is_empty()
        && !value.contains(['\n', '\r'])
        && sh_trim(value) == value
        && !value.contains('$')
}

fn initial_content(data_dir: &str, postgres_pass: &str, rabbit_pass: &str) -> String {
    format!(
        "# Criado pelo app artigo2md na primeira inicialização da infraestrutura.\n\
         # As credenciais só valem na primeira inicialização do volume; não as compartilhe.\n\
         {KEY_DATA_DIR}={data_dir}\n\
         {KEY_POSTGRES_PORT}={DEFAULT_POSTGRES_PORT}\n\
         {KEY_RABBITMQ_PORT}={DEFAULT_RABBITMQ_PORT}\n\
         {KEY_RABBITMQ_MANAGEMENT_PORT}={DEFAULT_RABBITMQ_MANAGEMENT_PORT}\n\
         {KEY_API_PORT}={DEFAULT_API_PORT}\n\
         {KEY_POSTGRES_USER}={DEFAULT_SERVICE_NAME}\n\
         {KEY_POSTGRES_PASSWORD}={postgres_pass}\n\
         {KEY_POSTGRES_DB}={DEFAULT_SERVICE_NAME}\n\
         {KEY_RABBITMQ_USER}={DEFAULT_SERVICE_NAME}\n\
         {KEY_RABBITMQ_PASS}={rabbit_pass}\n"
    )
}

/// Temporário `.env.<aleatório>.tmp` na pasta de `dest`: `create_new`, `0600`, `O_NOFOLLOW`,
/// conteúdo e `fsync` (M9).
fn write_temp(dest: &Path, content: &str) -> AppResult<PathBuf> {
    let dir = dest
        .parent()
        .ok_or_else(|| AppError::new(ErrorCode::Io, "stack/.env sem pasta"))?;
    let suffix = random_hex(8)?;
    let temp = dir.join(format!(".env.{suffix}.tmp"));
    let result = (|| -> io::Result<()> {
        let mut f: File = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(&temp)?;
        f.write_all(content.as_bytes())?;
        f.sync_all()
    })();
    if let Err(e) = result {
        let _ = fs::remove_file(&temp);
        return Err(e.into());
    }
    Ok(temp)
}

/// Publica o temporário em `dest` sem sobrescrever: `link` + `unlink` do temporário. `Ok(true)`
/// se publicou; `Ok(false)` se o destino surgiu no meio (o temporário é descartado).
fn publish_no_overwrite(temp: &Path, dest: &Path) -> io::Result<bool> {
    let linked = fs::hard_link(temp, dest);
    let _ = fs::remove_file(temp);
    match linked {
        Ok(()) => Ok(true),
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists => Ok(false),
        Err(e) => Err(e),
    }
}

/// Cria `stack/.env` quando ausente. Existente (de qualquer tipo) → `Existing`, sem tocar nele;
/// a validação posterior recusa link ou arquivo estranho.
pub fn ensure(env: &Path, data_dir: &str) -> AppResult<EnsureOutcome> {
    if fs::symlink_metadata(env).is_ok() {
        return Ok(EnsureOutcome::Existing);
    }
    if !value_is_writable(data_dir) || !Path::new(data_dir).is_absolute() {
        return Err(AppError::new(
            ErrorCode::DataDirInvalid,
            "caminho da pasta de dados não pode ser gravado no .env",
        ));
    }
    let postgres_pass = Secret::new(random_hex(16)?);
    let rabbit_pass = Secret::new(random_hex(16)?);
    let content = Secret::new(initial_content(
        data_dir,
        postgres_pass.expose(),
        rabbit_pass.expose(),
    ));
    let temp = write_temp(env, content.expose())?;
    if publish_no_overwrite(&temp, env)? {
        Ok(EnsureOutcome::Created)
    } else {
        Ok(EnsureOutcome::Existing)
    }
}

// ── Edição parcial ───────────────────────────────────────────────────────────────

/// As únicas chaves que o app reescreve (a porta do painel e as credenciais nunca).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct EnvEdits {
    pub postgres_port: Option<u16>,
    pub rabbitmq_port: Option<u16>,
    pub api_port: Option<u16>,
    pub data_dir: Option<String>,
}

impl EnvEdits {
    fn pairs(&self) -> AppResult<Vec<(&'static str, String)>> {
        let invalid = || {
            AppError::new(
                ErrorCode::InvalidStackSettings,
                "valor fora das regras de StackParams",
            )
        };
        let mut out = Vec::new();
        for (key, port) in [
            (KEY_POSTGRES_PORT, self.postgres_port),
            (KEY_RABBITMQ_PORT, self.rabbitmq_port),
            (KEY_API_PORT, self.api_port),
        ] {
            if let Some(p) = port {
                if p == 0 {
                    return Err(invalid());
                }
                out.push((key, p.to_string()));
            }
        }
        if let Some(dir) = &self.data_dir {
            if !value_is_writable(dir) || !Path::new(dir).is_absolute() {
                return Err(invalid());
            }
            out.push((KEY_DATA_DIR, dir.clone()));
        }
        Ok(out)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EditOutcome {
    /// O arquivo foi reescrito (`false` quando nada mudava).
    pub changed: bool,
    /// O modo anterior era mais amplo que `0600` (a interface avisa; o modo volta a `0600`).
    pub wide_mode_before: bool,
}

/// Reescreve só as linhas das chaves editáveis, preservando byte a byte as demais (comentários e
/// credenciais incluídos) e o modo `0600`. Chave ausente é acrescentada no fim. Recusa link,
/// arquivo não regular, de outro uid ou com conteúdo que o `up.sh` recusaria por chave repetida.
pub fn edit(env: &Path, edits: &EnvEdits) -> AppResult<EditOutcome> {
    edit_with_uid(env, edits, current_uid())
}

fn edit_with_uid(env: &Path, edits: &EnvEdits, uid: u32) -> AppResult<EditOutcome> {
    let pairs = edits.pairs()?;
    let meta = lstat_regular_owned(env, uid)?
        .ok_or_else(|| AppError::new(ErrorCode::NotFound, "stack/.env não existe"))?;
    let text = read_text(env, uid)?
        .ok_or_else(|| AppError::new(ErrorCode::NotFound, "stack/.env não existe"))?;
    // Chave repetida/`$` já tornam o arquivo inválido para o `up.sh`: não editar por cima.
    EnvFile::parse(&text).map_err(EnvIssue::into_error)?;
    let wide_mode_before = meta.mode() & 0o077 != 0;

    let mut done = vec![false; pairs.len()];
    let mut out = String::with_capacity(text.len() + 64);
    for segment in text.split_inclusive('\n') {
        let content = segment.trim_end_matches(['\n', '\r']);
        let ending = &segment[content.len()..];
        let replaced = parse_line(content).and_then(|(key, _)| {
            pairs
                .iter()
                .position(|(k, _)| *k == key)
                .map(|i| (i, &pairs[i]))
        });
        match replaced {
            Some((i, (key, value))) => {
                done[i] = true;
                out.push_str(&format!("{key}={value}{ending}"));
            }
            None => out.push_str(segment),
        }
    }
    for (i, (key, value)) in pairs.iter().enumerate() {
        if !done[i] {
            if !out.is_empty() && !out.ends_with('\n') {
                out.push('\n');
            }
            out.push_str(&format!("{key}={value}\n"));
        }
    }

    if out == text && !wide_mode_before {
        return Ok(EditOutcome {
            changed: false,
            wide_mode_before,
        });
    }
    let temp = write_temp(env, &out)?;
    // Reconfere antes de trocar: o destino ainda precisa ser o arquivo regular do usuário.
    if let Err(e) = lstat_regular_owned(env, uid)
        .and_then(|m| m.ok_or_else(|| AppError::new(ErrorCode::NotFound, "stack/.env sumiu")))
    {
        let _ = fs::remove_file(&temp);
        return Err(e);
    }
    if let Err(e) = fs::rename(&temp, env) {
        let _ = fs::remove_file(&temp);
        return Err(e.into());
    }
    Ok(EditOutcome {
        changed: true,
        wide_mode_before,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{symlink, PermissionsExt};
    use std::sync::{Arc, Barrier};

    struct Env {
        _tmp: tempfile::TempDir,
        stack: PathBuf,
        env: PathBuf,
        data: PathBuf,
    }

    fn env_fixture() -> Env {
        let tmp = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(tmp.path()).unwrap();
        let stack = root.join("stack");
        let data = root.join("dados");
        fs::create_dir(&stack).unwrap();
        fs::create_dir(&data).unwrap();
        let env = stack.join(".env");
        Env {
            _tmp: tmp,
            stack,
            env,
            data,
        }
    }

    const PG_PASS: &str = "pg-Segredo-Muito-Longo-123";
    const MQ_PASS: &str = "mq-Segredo-Muito-Longo-456";

    fn valid_text(data: &Path) -> String {
        format!(
            "# comentário que precisa sobreviver\n\
             ARTIGO2MD_DATA_DIR={}\n\
             ARTIGO2MD_POSTGRES_PORT=5432\n\
             ARTIGO2MD_RABBITMQ_PORT=5672\n\
             ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT=15672\n\
             ARTIGO2MD_API_PORT=8778\n\
             \n\
             POSTGRES_USER=artigo2md\n\
             POSTGRES_PASSWORD={PG_PASS}\n\
             POSTGRES_DB=artigo2md\n\
             RABBITMQ_DEFAULT_USER=artigo2md\n\
             RABBITMQ_DEFAULT_PASS={MQ_PASS}\n",
            data.display()
        )
    }

    fn write_env(e: &Env, text: &str, mode: u32) {
        fs::write(&e.env, text).unwrap();
        fs::set_permissions(&e.env, fs::Permissions::from_mode(mode)).unwrap();
    }

    fn issue(text: &str) -> EnvIssue {
        EnvFile::parse(text).err().unwrap_or_else(|| {
            panic!("esperava problema em {text:?}");
        })
    }

    #[test]
    fn parser_follows_the_up_sh_rules() {
        let text = "\n  # comentário\n\
                    export  A_KEY = \"valor com espaço\" \n\
                    \texport\tB=  'aspas simples'\n\
                    sem-igual\n\
                    chave-invalida=1\n\
                    C=\"\n\
                    D=\n\
                    chave com espaço=2\n\
                    E = x=y\n";
        let env = EnvFile::parse(text).unwrap();
        assert_eq!(env.plain("A_KEY"), Some("valor com espaço"));
        assert_eq!(env.plain("B"), Some("aspas simples"));
        assert_eq!(env.plain("C"), Some("\""));
        assert!(env.has("D"));
        assert_eq!(env.plain("D"), Some(""));
        assert_eq!(env.plain("E"), Some("x=y"));
        assert!(!env.has("chave-invalida"));
        assert!(!env.has("chave com espaço"));
        assert!(!env.has("sem-igual"));
    }

    #[test]
    fn parser_stops_at_duplicate_key_and_dollar_in_order() {
        assert_eq!(issue("A=1\nB=2\nA=3\n"), EnvIssue::Duplicate("A".into()));
        assert_eq!(issue("A=ab$cd\nA=2\n"), EnvIssue::DollarInValue("A".into()));
        // `export` conta como a mesma chave; linhas ignoradas não contam como repetidas.
        assert_eq!(issue("A=1\nexport A=2\n"), EnvIssue::Duplicate("A".into()));
        assert!(EnvFile::parse("x-y=1\nx-y=2\n").is_ok());
    }

    #[test]
    fn credentials_are_secrets_and_never_plain() {
        let env = EnvFile::parse("POSTGRES_PASSWORD=abc\nUSER_X=1\n").unwrap();
        assert!(env.plain(KEY_POSTGRES_PASSWORD).is_none());
        assert_eq!(env.secret(KEY_POSTGRES_PASSWORD).unwrap().expose(), "abc");
        assert_eq!(env.plain("USER_X"), Some("1"));
    }

    #[test]
    fn validate_applies_the_up_sh_rules_naming_only_the_variable() {
        let e = env_fixture();
        let ok = valid_text(&e.data);
        EnvFile::parse(&ok).unwrap().validate().unwrap();

        let cases: Vec<(String, EnvIssue)> = vec![
            (
                format!("{ok}POSTGRES_HOST_AUTH_METHOD=trust\n"),
                EnvIssue::HostAuthMethodDefined,
            ),
            (
                ok.replace("POSTGRES_USER=artigo2md", "POSTGRES_USER="),
                EnvIssue::Empty("POSTGRES_USER".into()),
            ),
            (
                ok.replace("POSTGRES_DB=artigo2md\n", ""),
                EnvIssue::Empty("POSTGRES_DB".into()),
            ),
            (
                ok.replace(PG_PASS, "troque-esta-senha"),
                EnvIssue::ExamplePassword("POSTGRES_PASSWORD".into()),
            ),
            (
                ok.replace(MQ_PASS, "curta123"),
                EnvIssue::ShortPassword("RABBITMQ_DEFAULT_PASS".into()),
            ),
            (
                ok.replace("RABBITMQ_DEFAULT_PASS=", "#RABBITMQ_DEFAULT_PASS="),
                EnvIssue::Empty("RABBITMQ_DEFAULT_PASS".into()),
            ),
            (
                ok.replace("ARTIGO2MD_POSTGRES_PORT=5432", "ARTIGO2MD_POSTGRES_PORT=0"),
                EnvIssue::BadPort("ARTIGO2MD_POSTGRES_PORT".into()),
            ),
            (
                ok.replace(
                    "ARTIGO2MD_RABBITMQ_PORT=5672",
                    "ARTIGO2MD_RABBITMQ_PORT=65536",
                ),
                EnvIssue::BadPort("ARTIGO2MD_RABBITMQ_PORT".into()),
            ),
            (
                ok.replace("ARTIGO2MD_API_PORT=8778", "ARTIGO2MD_API_PORT=80a"),
                EnvIssue::BadPort("ARTIGO2MD_API_PORT".into()),
            ),
            (
                ok.replace(&e.data.display().to_string(), "relativa/dados"),
                EnvIssue::BadDataDir("ARTIGO2MD_DATA_DIR".into()),
            ),
            (
                ok.replace(
                    &e.data.display().to_string(),
                    &e.data.join("nao-existe").display().to_string(),
                ),
                EnvIssue::BadDataDir("ARTIGO2MD_DATA_DIR".into()),
            ),
            (
                ok.replace(
                    &e.data.display().to_string(),
                    &format!("{}/../dados", e.data.display()),
                ),
                EnvIssue::BadDataDir("ARTIGO2MD_DATA_DIR".into()),
            ),
        ];
        for (text, expected) in cases {
            let got = EnvFile::parse(&text).unwrap().validate().unwrap_err();
            assert_eq!(got, expected, "{text}");
            // Mensagem e formatação citam só o nome da variável.
            let err = got.clone().into_error();
            for secret in [PG_PASS, MQ_PASS] {
                assert!(!err.detail().contains(secret));
                assert!(!format!("{got:?}").contains(secret));
            }
            assert_eq!(err.code, ErrorCode::InvalidParams);
            assert!(err.detail().starts_with(got.variable()));
        }
    }

    #[test]
    fn management_port_is_not_required() {
        let e = env_fixture();
        let text = valid_text(&e.data).replace("ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT=15672\n", "");
        EnvFile::parse(&text).unwrap().validate().unwrap();
    }

    /// Os padrões ao criar o arquivo são os de `stack/.env.example:22-37`.
    #[test]
    fn creation_defaults_match_the_env_example() {
        let example = EnvFile::parse(include_str!("../../../../stack/.env.example")).unwrap();
        assert_eq!(example.port(KEY_POSTGRES_PORT), Some(DEFAULT_POSTGRES_PORT));
        assert_eq!(example.port(KEY_RABBITMQ_PORT), Some(DEFAULT_RABBITMQ_PORT));
        assert_eq!(
            example.port(KEY_RABBITMQ_MANAGEMENT_PORT),
            Some(DEFAULT_RABBITMQ_MANAGEMENT_PORT)
        );
        assert_eq!(example.port(KEY_API_PORT), Some(DEFAULT_API_PORT));
        for key in [KEY_POSTGRES_USER, KEY_POSTGRES_DB, KEY_RABBITMQ_USER] {
            assert_eq!(example.plain(key), Some(DEFAULT_SERVICE_NAME));
        }
        assert_eq!(
            example.secret(KEY_POSTGRES_PASSWORD).unwrap().expose(),
            EXAMPLE_PASSWORD
        );
    }

    #[test]
    fn ensure_creates_a_valid_file_with_mode_0600_and_random_passwords() {
        let e = env_fixture();
        let data = e.data.to_str().unwrap();
        assert_eq!(ensure(&e.env, data).unwrap(), EnsureOutcome::Created);
        assert_eq!(fs::metadata(&e.env).unwrap().mode() & 0o777, 0o600);

        let env = load(&e.env).unwrap().unwrap();
        env.validate().unwrap();
        assert_eq!(env.data_dir(), Some(data));
        assert_eq!(env.port(KEY_API_PORT), Some(8778));
        let pg = env
            .secret(KEY_POSTGRES_PASSWORD)
            .unwrap()
            .expose()
            .to_string();
        let mq = env.secret(KEY_RABBITMQ_PASS).unwrap().expose().to_string();
        for p in [&pg, &mq] {
            assert_eq!(p.len(), 32);
            assert!(p.bytes().all(|b| b.is_ascii_hexdigit()));
        }
        assert_ne!(pg, mq);
        // Só o `.env` ficou na pasta (nenhum temporário).
        let names: Vec<_> = fs::read_dir(&e.stack)
            .unwrap()
            .map(|d| d.unwrap().file_name())
            .collect();
        assert_eq!(names, vec![std::ffi::OsString::from(".env")]);
    }

    #[test]
    fn ensure_never_overwrites_nor_regenerates_credentials() {
        let e = env_fixture();
        let original = valid_text(&e.data);
        write_env(&e, &original, 0o644);
        assert_eq!(
            ensure(&e.env, e.data.to_str().unwrap()).unwrap(),
            EnsureOutcome::Existing
        );
        assert_eq!(fs::read_to_string(&e.env).unwrap(), original);
        assert_eq!(fs::metadata(&e.env).unwrap().mode() & 0o777, 0o644);

        // Um link no lugar do arquivo também é "existente": nada é criado nem seguido.
        let e2 = env_fixture();
        let target = e2.stack.join("target");
        fs::write(&target, "x").unwrap();
        symlink(&target, &e2.env).unwrap();
        assert_eq!(
            ensure(&e2.env, e2.data.to_str().unwrap()).unwrap(),
            EnsureOutcome::Existing
        );
        assert_eq!(fs::read_to_string(&target).unwrap(), "x");
    }

    #[test]
    fn publish_loses_the_race_gracefully_and_uses_the_existing_file() {
        let e = env_fixture();
        let temp = write_temp(&e.env, "novo\n").unwrap();
        // O destino surge entre a escrita do temporário e a publicação.
        fs::write(&e.env, "do outro processo\n").unwrap();
        assert!(!publish_no_overwrite(&temp, &e.env).unwrap());
        assert_eq!(fs::read_to_string(&e.env).unwrap(), "do outro processo\n");
        assert!(!temp.exists());
    }

    #[test]
    fn concurrent_creation_yields_one_consistent_file() {
        let e = env_fixture();
        let barrier = Arc::new(Barrier::new(8));
        let handles: Vec<_> = (0..8)
            .map(|_| {
                let (env, data, barrier) = (e.env.clone(), e.data.clone(), Arc::clone(&barrier));
                std::thread::spawn(move || {
                    barrier.wait();
                    ensure(&env, data.to_str().unwrap()).unwrap()
                })
            })
            .collect();
        let outcomes: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
        assert_eq!(
            outcomes
                .iter()
                .filter(|o| **o == EnsureOutcome::Created)
                .count(),
            1
        );
        load(&e.env).unwrap().unwrap().validate().unwrap();
        assert_eq!(fs::read_dir(&e.stack).unwrap().count(), 1);
    }

    #[test]
    fn ensure_refuses_a_data_dir_that_would_not_survive_the_parser() {
        let e = env_fixture();
        for bad in ["relativa", "/tmp/a$b", "/tmp/a\nb", "/tmp/a ", ""] {
            let err = ensure(&e.env, bad).unwrap_err();
            assert_eq!(err.code, ErrorCode::DataDirInvalid, "{bad:?}");
        }
        assert!(!e.env.exists());
    }

    #[test]
    fn generated_password_never_shows_in_captured_output() {
        let e = env_fixture();
        let outcome = ensure(&e.env, e.data.to_str().unwrap()).unwrap();
        let env = load(&e.env).unwrap().unwrap();
        let pg = env
            .secret(KEY_POSTGRES_PASSWORD)
            .unwrap()
            .expose()
            .to_string();
        let mq = env.secret(KEY_RABBITMQ_PASS).unwrap().expose().to_string();

        let mut masker = Masker::new();
        env.register_secrets(&mut masker);
        let line = format!("conectando com {pg} e {mq}");
        let masked = masker.mask(&line);

        let err_issue = EnvFile::parse(&format!("POSTGRES_PASSWORD={pg}$\n"))
            .err()
            .unwrap()
            .into_error();
        let mut captured = vec![
            format!("{outcome:?}"),
            format!("{masked:?}"),
            masked.as_str().to_string(),
            format!("{err_issue:?}"),
            err_issue.detail().to_string(),
            serde_json::to_string(&err_issue).unwrap(),
        ];
        // Erros de leitura e edição também.
        for r in [
            load(&e.stack.join("nao-existe")).map(|_| ()),
            edit(
                &e.env,
                &EnvEdits {
                    api_port: Some(0),
                    ..EnvEdits::default()
                },
            )
            .map(|_| ()),
        ] {
            if let Err(err) = r {
                captured.push(format!("{err:?}"));
                captured.push(err.detail().to_string());
            }
        }
        for text in captured {
            assert!(!text.contains(&pg), "vazou: {text}");
            assert!(!text.contains(&mq), "vazou: {text}");
        }
        assert!(masked.as_str().contains("***"));
    }

    #[test]
    fn load_refuses_symlink_non_regular_and_other_owner() {
        let e = env_fixture();
        assert!(load(&e.env).unwrap().is_none());

        let target = e.stack.join("target");
        fs::write(&target, valid_text(&e.data)).unwrap();
        symlink(&target, &e.env).unwrap();
        assert_eq!(load(&e.env).err().unwrap().code, ErrorCode::InvalidParams);
        fs::remove_file(&e.env).unwrap();

        fs::create_dir(&e.env).unwrap();
        assert_eq!(load(&e.env).err().unwrap().code, ErrorCode::InvalidParams);
        fs::remove_dir(&e.env).unwrap();

        write_env(&e, &valid_text(&e.data), 0o600);
        let other = current_uid().wrapping_add(1);
        assert_eq!(
            load_with_uid(&e.env, other).err().unwrap().code,
            ErrorCode::InvalidParams
        );
        assert!(load(&e.env).unwrap().is_some());
    }

    #[test]
    fn edit_rewrites_only_the_editable_keys_preserving_other_bytes() {
        let e = env_fixture();
        let other_dir = e.stack.parent().unwrap().join("other");
        fs::create_dir(&other_dir).unwrap();
        // Linhas com formatos peculiares ficam intactas; as editadas voltam como CHAVE=valor.
        let original = format!(
            "# cabeçalho\r\n{}",
            valid_text(&e.data).replace(
                "ARTIGO2MD_API_PORT=8778\n",
                "export ARTIGO2MD_API_PORT = \"8778\"\r\n"
            )
        );
        write_env(&e, &original, 0o600);

        let out = edit(
            &e.env,
            &EnvEdits {
                postgres_port: Some(15432),
                rabbitmq_port: None,
                api_port: Some(9000),
                data_dir: Some(other_dir.to_str().unwrap().to_string()),
            },
        )
        .unwrap();
        assert!(out.changed && !out.wide_mode_before);

        let after = fs::read_to_string(&e.env).unwrap();
        let expected = original
            .replace(
                "ARTIGO2MD_POSTGRES_PORT=5432",
                "ARTIGO2MD_POSTGRES_PORT=15432",
            )
            .replace(
                "export ARTIGO2MD_API_PORT = \"8778\"\r\n",
                "ARTIGO2MD_API_PORT=9000\r\n",
            )
            .replace(
                &format!("ARTIGO2MD_DATA_DIR={}", e.data.display()),
                &format!("ARTIGO2MD_DATA_DIR={}", other_dir.display()),
            );
        assert_eq!(after, expected);
        assert!(after.contains("# comentário que precisa sobreviver\n"));
        assert!(after.contains(&format!("POSTGRES_PASSWORD={PG_PASS}\n")));
        assert!(after.contains("ARTIGO2MD_RABBITMQ_MANAGEMENT_PORT=15672\n"));
        assert_eq!(fs::metadata(&e.env).unwrap().mode() & 0o777, 0o600);
        // Credenciais e porta do painel são intocáveis pelas edições: continuam lidas iguais.
        let env = load(&e.env).unwrap().unwrap();
        assert_eq!(env.secret(KEY_RABBITMQ_PASS).unwrap().expose(), MQ_PASS);
        env.validate().unwrap();
        assert_eq!(fs::read_dir(&e.stack).unwrap().count(), 1);
    }

    #[test]
    fn edit_without_changes_does_not_rewrite_and_missing_key_is_appended() {
        let e = env_fixture();
        write_env(&e, &valid_text(&e.data), 0o600);
        let before = fs::metadata(&e.env).unwrap().ino();
        let out = edit(
            &e.env,
            &EnvEdits {
                postgres_port: Some(5432),
                ..EnvEdits::default()
            },
        )
        .unwrap();
        assert!(!out.changed);
        assert_eq!(fs::metadata(&e.env).unwrap().ino(), before);

        // Chave ausente e arquivo sem quebra de linha final.
        let text = valid_text(&e.data).replace("ARTIGO2MD_API_PORT=8778\n", "");
        write_env(&e, text.trim_end_matches('\n'), 0o600);
        edit(
            &e.env,
            &EnvEdits {
                api_port: Some(8790),
                ..EnvEdits::default()
            },
        )
        .unwrap();
        let after = fs::read_to_string(&e.env).unwrap();
        assert!(after.ends_with(
            "RABBITMQ_DEFAULT_PASS=mq-Segredo-Muito-Longo-456\nARTIGO2MD_API_PORT=8790\n"
        ));
    }

    #[test]
    fn edit_warns_about_a_wider_mode_and_restores_0600() {
        let e = env_fixture();
        write_env(&e, &valid_text(&e.data), 0o644);
        let out = edit(
            &e.env,
            &EnvEdits {
                api_port: Some(9001),
                ..EnvEdits::default()
            },
        )
        .unwrap();
        assert!(out.changed && out.wide_mode_before);
        assert_eq!(fs::metadata(&e.env).unwrap().mode() & 0o777, 0o600);
    }

    #[test]
    fn edit_refuses_bad_values_links_and_invalid_files() {
        let e = env_fixture();
        write_env(&e, &valid_text(&e.data), 0o600);
        let original = fs::read_to_string(&e.env).unwrap();
        for bad in [
            EnvEdits {
                postgres_port: Some(0),
                ..EnvEdits::default()
            },
            EnvEdits {
                data_dir: Some("relativa".into()),
                ..EnvEdits::default()
            },
            EnvEdits {
                data_dir: Some("/tmp/a$b".into()),
                ..EnvEdits::default()
            },
            EnvEdits {
                data_dir: Some("/tmp/a\nPOSTGRES_PASSWORD=x".into()),
                ..EnvEdits::default()
            },
        ] {
            assert_eq!(
                edit(&e.env, &bad).unwrap_err().code,
                ErrorCode::InvalidStackSettings
            );
        }
        assert_eq!(fs::read_to_string(&e.env).unwrap(), original);

        // Chave repetida: não edita por cima.
        write_env(&e, &format!("{original}ARTIGO2MD_API_PORT=1\n"), 0o600);
        let err = edit(
            &e.env,
            &EnvEdits {
                api_port: Some(2),
                ..EnvEdits::default()
            },
        )
        .unwrap_err();
        assert_eq!(err.code, ErrorCode::InvalidParams);

        // Link simbólico: recusado sem escrever no destino do link.
        let target = e.stack.join("target");
        fs::write(&target, &original).unwrap();
        fs::remove_file(&e.env).unwrap();
        symlink(&target, &e.env).unwrap();
        let err = edit(
            &e.env,
            &EnvEdits {
                api_port: Some(2),
                ..EnvEdits::default()
            },
        )
        .unwrap_err();
        assert_eq!(err.code, ErrorCode::InvalidParams);
        assert_eq!(fs::read_to_string(&target).unwrap(), original);

        // Outro uid e arquivo ausente.
        fs::remove_file(&e.env).unwrap();
        assert_eq!(
            edit(
                &e.env,
                &EnvEdits {
                    api_port: Some(2),
                    ..EnvEdits::default()
                }
            )
            .unwrap_err()
            .code,
            ErrorCode::NotFound
        );
        write_env(&e, &original, 0o600);
        let err = edit_with_uid(
            &e.env,
            &EnvEdits {
                api_port: Some(2),
                ..EnvEdits::default()
            },
            current_uid().wrapping_add(1),
        )
        .unwrap_err();
        assert_eq!(err.code, ErrorCode::InvalidParams);
    }
}
