//! Resolução de `docker`, `uv` e `colima` num app aberto pelo Finder (research Decision 3;
//! plan.md M6, M7).
//!
//! Ordem: (1) `/bin/zsh -l -c 'whence -p <nome>'` (nome fixo no código, stdin nulo, 5 s, só a
//! última linha da saída); (2) lista fixa de candidatos. Um caminho só é aceito se for absoluto,
//! arquivo regular executável, do uid corrente ou de root e sem escrita para grupo/outros.
//! Nenhum valor do usuário entra nos comandos desta camada.

use std::ffi::CString;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::MetadataExt;
use std::path::{Component, Path, PathBuf};
use std::time::Duration;

use crate::stack::exec::{self, ExecSpec, Stream};
use crate::stack::secret::Masker;

/// Tempo máximo do shell de login (M7).
pub const SHELL_TIMEOUT: Duration = Duration::from_secs(5);

/// Shell de login usado para resolver o `PATH` do usuário (Decision 3).
const LOGIN_SHELL: &str = "/bin/zsh";

/// Executáveis que o app procura. Os nomes são fixos no código.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tool {
    Docker,
    Uv,
    Colima,
}

impl Tool {
    pub fn name(self) -> &'static str {
        match self {
            Tool::Docker => "docker",
            Tool::Uv => "uv",
            Tool::Colima => "colima",
        }
    }
}

/// Lista fixa de candidatos da Decision 3, na ordem de preferência.
pub fn candidate_paths(tool: Tool, home: &Path) -> Vec<PathBuf> {
    match tool {
        Tool::Docker => vec![
            home.join(".docker/bin/docker"),
            PathBuf::from("/usr/local/bin/docker"),
            home.join(".orbstack/bin/docker"),
            PathBuf::from("/opt/homebrew/bin/docker"),
        ],
        Tool::Uv => vec![PathBuf::from("/opt/homebrew/bin/uv")],
        Tool::Colima => vec![
            PathBuf::from("/usr/local/bin/colima"),
            PathBuf::from("/opt/homebrew/bin/colima"),
        ],
    }
}

pub fn current_uid() -> u32 {
    // SAFETY: `geteuid` não tem argumentos nem efeitos colaterais.
    unsafe { libc::geteuid() }
}

fn is_executable_by_current_user(path: &Path) -> bool {
    match CString::new(path.as_os_str().as_bytes()) {
        // SAFETY: `c_path` é uma string C válida e termina em NUL.
        Ok(c_path) => unsafe { libc::access(c_path.as_ptr(), libc::X_OK) == 0 },
        Err(_) => false,
    }
}

/// Aceita o caminho só se for absoluto, sem `..`, arquivo regular executável (seguindo links, o
/// alvo é que conta), do `uid` informado ou de root, sem escrita para grupo/outros (M6).
pub fn is_trusted_executable(path: &Path, uid: u32) -> bool {
    if !path.is_absolute() || path.components().any(|c| c == Component::ParentDir) {
        return false;
    }
    let Ok(meta) = std::fs::metadata(path) else {
        return false;
    };
    meta.is_file()
        && (meta.uid() == uid || meta.uid() == 0)
        && meta.mode() & 0o022 == 0
        && is_executable_by_current_user(path)
}

/// Última linha não vazia da saída padrão do shell, como caminho.
fn last_stdout_line(lines: &[exec::ExecLine]) -> Option<PathBuf> {
    lines
        .iter()
        .rev()
        .filter(|l| l.stream == Stream::Stdout)
        .map(|l| l.text.as_str().trim())
        .find(|t| !t.is_empty())
        .map(PathBuf::from)
}

/// Núcleo da resolução, com todos os insumos explícitos (testável com shell e candidatos falsos).
pub fn resolve_tool(
    shell: &Path,
    tool: Tool,
    candidates: &[PathBuf],
    uid: u32,
    timeout: Duration,
) -> Option<PathBuf> {
    let spec = ExecSpec::new(shell, timeout)
        .arg("-l")
        .arg("-c")
        .arg(format!("whence -p {}", tool.name()));
    if let Ok((outcome, lines)) = exec::run_collect(spec, &Masker::new()) {
        if outcome.success() {
            if let Some(found) = last_stdout_line(&lines) {
                if is_trusted_executable(&found, uid) {
                    return Some(found);
                }
            }
        }
    }
    candidates
        .iter()
        .find(|c| is_trusted_executable(c, uid))
        .cloned()
}

/// Caminhos absolutos resolvidos; `None` onde o executável não foi encontrado ou não é confiável.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Located {
    pub docker: Option<PathBuf>,
    pub uv: Option<PathBuf>,
    pub colima: Option<PathBuf>,
}

impl Located {
    /// Diretórios do `docker` e do `uv` resolvidos, sem repetição, para o `PATH` dos filhos.
    pub fn path_dirs(&self) -> Vec<PathBuf> {
        let mut dirs: Vec<PathBuf> = Vec::new();
        for path in [&self.docker, &self.uv].into_iter().flatten() {
            if let Some(dir) = path.parent() {
                let dir = dir.to_path_buf();
                if !dirs.contains(&dir) {
                    dirs.push(dir);
                }
            }
        }
        dirs
    }
}

/// Resolve os três executáveis do ambiente real (`/bin/zsh`, `HOME` e uid do processo).
pub fn locate_all(home: &Path) -> Located {
    let uid = current_uid();
    let shell = Path::new(LOGIN_SHELL);
    let find = |tool: Tool| {
        resolve_tool(
            shell,
            tool,
            &candidate_paths(tool, home),
            uid,
            SHELL_TIMEOUT,
        )
    };
    Located {
        docker: find(Tool::Docker),
        uv: find(Tool::Uv),
        colima: find(Tool::Colima),
    }
}

/// `ExecSpec` pré-configurado com o `PATH` dos filhos: sistema primeiro, depois os diretórios do
/// `docker` e do `uv` resolvidos.
pub fn with_child_path(spec: ExecSpec, located: &Located) -> ExecSpec {
    spec.path_dirs(located.path_dirs())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    const T: Duration = Duration::from_secs(10);

    fn write_exec(dir: &Path, name: &str, body: &str, mode: u32) -> PathBuf {
        let path = dir.join(name);
        fs::write(&path, format!("#!/bin/sh\n{body}\n")).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(mode)).unwrap();
        path
    }

    fn uid() -> u32 {
        current_uid()
    }

    #[test]
    fn accepts_the_shell_answer_when_it_is_a_trusted_executable() {
        let tmp = tempfile::tempdir().unwrap();
        let docker = write_exec(tmp.path(), "docker", "exit 0", 0o755);
        let shell = write_exec(
            tmp.path(),
            "shell.sh",
            &format!("echo 'ruído do rc do usuário'\necho {}", docker.display()),
            0o755,
        );
        let found = resolve_tool(&shell, Tool::Docker, &[], uid(), T);
        assert_eq!(found, Some(docker));
    }

    #[test]
    fn shell_receives_the_fixed_whence_command_and_only_the_name() {
        let tmp = tempfile::tempdir().unwrap();
        let log = tmp.path().join("args.log");
        let uv = write_exec(tmp.path(), "uv", "exit 0", 0o755);
        let shell = write_exec(
            tmp.path(),
            "shell.sh",
            &format!(
                "printf '%s|' \"$@\" > {}\necho {}",
                log.display(),
                uv.display()
            ),
            0o755,
        );
        assert_eq!(resolve_tool(&shell, Tool::Uv, &[], uid(), T), Some(uv));
        assert_eq!(fs::read_to_string(log).unwrap(), "-l|-c|whence -p uv|");
    }

    #[test]
    fn alias_text_and_relative_paths_are_refused_and_fall_back() {
        let tmp = tempfile::tempdir().unwrap();
        let candidate = write_exec(tmp.path(), "docker-fixo", "exit 0", 0o755);
        for answer in [
            "docker: aliased to /tmp/evil/docker",
            "bin/docker",
            "./docker",
            "docker",
        ] {
            let shell = write_exec(tmp.path(), "shell.sh", &format!("echo '{answer}'"), 0o755);
            let found = resolve_tool(
                &shell,
                Tool::Docker,
                std::slice::from_ref(&candidate),
                uid(),
                T,
            );
            assert_eq!(found.as_ref(), Some(&candidate), "resposta: {answer}");
        }
        // Sem candidato válido, nada é aceito.
        let shell = write_exec(tmp.path(), "shell2.sh", "echo bin/docker", 0o755);
        assert_eq!(resolve_tool(&shell, Tool::Docker, &[], uid(), T), None);
    }

    #[test]
    fn only_the_last_line_counts() {
        let tmp = tempfile::tempdir().unwrap();
        let docker = write_exec(tmp.path(), "docker", "exit 0", 0o755);
        let candidate = write_exec(tmp.path(), "fixo", "exit 0", 0o755);
        let shell = write_exec(
            tmp.path(),
            "shell.sh",
            &format!("echo {}\necho 'lixo depois'", docker.display()),
            0o755,
        );
        let found = resolve_tool(
            &shell,
            Tool::Docker,
            std::slice::from_ref(&candidate),
            uid(),
            T,
        );
        assert_eq!(found, Some(candidate));
    }

    #[test]
    fn writable_by_group_or_others_is_refused() {
        let tmp = tempfile::tempdir().unwrap();
        for mode in [0o775, 0o757, 0o777, 0o722] {
            let path = write_exec(tmp.path(), &format!("exe-{mode:o}"), "exit 0", mode);
            assert!(!is_trusted_executable(&path, uid()), "modo {mode:o}");
        }
        let ok = write_exec(tmp.path(), "ok", "exit 0", 0o755);
        assert!(is_trusted_executable(&ok, uid()));
        // O mesmo arquivo gravável por outros também some da resolução.
        let bad = write_exec(tmp.path(), "bad", "exit 0", 0o777);
        let shell = write_exec(
            tmp.path(),
            "shell.sh",
            &format!("echo {}", bad.display()),
            0o755,
        );
        assert_eq!(resolve_tool(&shell, Tool::Docker, &[], uid(), T), None);
    }

    #[test]
    fn other_owner_not_executable_not_regular_and_dotdot_are_refused() {
        let tmp = tempfile::tempdir().unwrap();
        let ok = write_exec(tmp.path(), "ok", "exit 0", 0o755);
        // Dono diferente do uid informado (e não root): recusado. Root é aceito (/bin/sh é de root).
        assert!(!is_trusted_executable(&ok, uid().wrapping_add(4242)));
        assert!(is_trusted_executable(
            Path::new("/bin/sh"),
            uid().wrapping_add(4242)
        ));
        // Sem permissão de execução.
        let plain = write_exec(tmp.path(), "plain", "exit 0", 0o644);
        assert!(!is_trusted_executable(&plain, uid()));
        // Diretório não é arquivo regular.
        assert!(!is_trusted_executable(tmp.path(), uid()));
        // Inexistente, relativo e com `..`.
        assert!(!is_trusted_executable(
            &tmp.path().join("nao-existe"),
            uid()
        ));
        assert!(!is_trusted_executable(Path::new("docker"), uid()));
        let dotdot = tmp.path().join("sub/../ok");
        fs::create_dir(tmp.path().join("sub")).unwrap();
        assert!(dotdot.exists());
        assert!(!is_trusted_executable(&dotdot, uid()));
    }

    #[test]
    fn symlink_is_judged_by_its_target() {
        let tmp = tempfile::tempdir().unwrap();
        let target = write_exec(tmp.path(), "target", "exit 0", 0o755);
        let link = tmp.path().join("link");
        std::os::unix::fs::symlink(&target, &link).unwrap();
        assert!(is_trusted_executable(&link, uid()));
        fs::set_permissions(&target, fs::Permissions::from_mode(0o777)).unwrap();
        assert!(!is_trusted_executable(&link, uid()));
    }

    #[test]
    fn falls_back_to_the_fixed_list_when_the_shell_fails_or_times_out() {
        let tmp = tempfile::tempdir().unwrap();
        let first = write_exec(tmp.path(), "primeiro", "exit 0", 0o777); // não confiável
        let second = write_exec(tmp.path(), "segundo", "exit 0", 0o755);
        let candidates = [tmp.path().join("ausente"), first, second.clone()];

        let failing = write_exec(tmp.path(), "falha.sh", "exit 1", 0o755);
        assert_eq!(
            resolve_tool(&failing, Tool::Colima, &candidates, uid(), T),
            Some(second.clone())
        );

        let missing_shell = tmp.path().join("sem-shell");
        assert_eq!(
            resolve_tool(&missing_shell, Tool::Colima, &candidates, uid(), T),
            Some(second.clone())
        );

        let slow = write_exec(tmp.path(), "lento.sh", "sleep 30", 0o755);
        let started = std::time::Instant::now();
        assert_eq!(
            resolve_tool(
                &slow,
                Tool::Colima,
                &candidates,
                uid(),
                Duration::from_millis(300)
            ),
            Some(second)
        );
        assert!(started.elapsed() < Duration::from_secs(10));
    }

    #[test]
    fn fixed_candidates_follow_decision_3() {
        let home = Path::new("/Users/x");
        assert_eq!(
            candidate_paths(Tool::Docker, home),
            [
                "/Users/x/.docker/bin/docker",
                "/usr/local/bin/docker",
                "/Users/x/.orbstack/bin/docker",
                "/opt/homebrew/bin/docker",
            ]
            .map(PathBuf::from)
        );
        assert_eq!(
            candidate_paths(Tool::Uv, home),
            [PathBuf::from("/opt/homebrew/bin/uv")]
        );
        assert_eq!(
            candidate_paths(Tool::Colima, home),
            ["/usr/local/bin/colima", "/opt/homebrew/bin/colima"].map(PathBuf::from)
        );
        assert_eq!(Tool::Docker.name(), "docker");
        assert_eq!(Tool::Uv.name(), "uv");
        assert_eq!(Tool::Colima.name(), "colima");
    }

    #[test]
    fn child_path_lists_docker_and_uv_dirs_after_the_system_ones() {
        let located = Located {
            docker: Some(PathBuf::from("/Users/x/.docker/bin/docker")),
            uv: Some(PathBuf::from("/opt/homebrew/bin/uv")),
            colima: Some(PathBuf::from("/opt/homebrew/bin/colima")),
        };
        assert_eq!(
            located.path_dirs(),
            ["/Users/x/.docker/bin", "/opt/homebrew/bin"].map(PathBuf::from)
        );
        assert_eq!(
            exec::build_path(&located.path_dirs()),
            std::ffi::OsString::from(
                "/usr/bin:/bin:/usr/sbin:/sbin:/Users/x/.docker/bin:/opt/homebrew/bin"
            )
        );
        // Mesmo diretório para docker e uv: aparece uma vez só.
        let same = Located {
            docker: Some(PathBuf::from("/opt/homebrew/bin/docker")),
            uv: Some(PathBuf::from("/opt/homebrew/bin/uv")),
            colima: None,
        };
        assert_eq!(same.path_dirs(), [PathBuf::from("/opt/homebrew/bin")]);
        assert!(Located::default().path_dirs().is_empty());
    }
}
