//! Dono do socket em escuta na porta do RabbitMQ (plan.md M2; tarefa 11.5).
//!
//! Antes de passar credenciais a um worker, o app confere que quem escuta em `127.0.0.1:<porta>` é o
//! processo de publicação de porta do runtime em uso, e não outro programa que tomou a porta com a stack
//! parada. A checagem não usa credenciais nem rede externa: pergunta ao sistema (`lsof`, só leitura) quem
//! escuta na porta e confere o executável do processo (`proc_pidpath`).
//!
//! Resultado por runtime (observado em 2026-11-27, ver plan.md, "Resultado de 11.5"):
//! - Docker Desktop: o dono é `/Applications/Docker.app/Contents/MacOS/com.docker.backend` => checagem viável.
//! - OrbStack e colima: o dono do socket NÃO foi observado (runtimes não instalados nesta máquina) => nenhum
//!   critério é presumido; o resultado é "indeterminado" e o app segue só com as duas conferências já
//!   exigidas (RabbitMQ em execução no Compose e sondagem AMQP saudável).

use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::stack::exec::{self, ExecSpec, Stream};
use crate::stack::procinfo::{ProcInspector, SystemProc};
use crate::stack::runtime::RuntimeKind;
use crate::stack::secret::Masker;

/// `lsof` de sistema: caminho absoluto, nunca resolvido pelo `PATH`.
const LSOF: &str = "/usr/sbin/lsof";
const LSOF_TIMEOUT: Duration = Duration::from_secs(3);
/// Nome do executável de publicação de porta do Docker Desktop (observado).
const DOCKER_DESKTOP_BACKEND: &str = "com.docker.backend";
const DOCKER_DESKTOP_DIR: &str = "/Docker.app/Contents/MacOS/";

/// Resultado da checagem (plan.md M2: conferido, divergente ou indeterminado).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SocketOwner {
    /// Quem escuta é o processo de publicação de porta do runtime em uso.
    Confirmed,
    /// Outro processo escuta na porta; guarda só o nome do comando (nunca argumentos).
    Divergent(String),
    /// A checagem não é possível; o motivo vai para o registro técnico.
    Indeterminate(&'static str),
}

/// Pares (pid, comando) das linhas `p<pid>` / `c<comando>` da saída `lsof -F pcn`.
fn parse_lsof(output: &str) -> Vec<(i32, String)> {
    let mut found: Vec<(i32, String)> = Vec::new();
    for line in output.lines() {
        if let Some(pid) = line.strip_prefix('p').and_then(|v| v.parse::<i32>().ok()) {
            found.push((pid, String::new()));
        } else if let (Some(command), Some(last)) = (line.strip_prefix('c'), found.last_mut()) {
            last.1 = command.to_string();
        }
    }
    found
}

fn is_docker_desktop_backend(path: &Path) -> bool {
    path.file_name().and_then(|n| n.to_str()) == Some(DOCKER_DESKTOP_BACKEND)
        && path
            .to_str()
            .is_some_and(|p| p.contains(DOCKER_DESKTOP_DIR))
}

/// Decisão pura: `output` é a saída de `lsof -nP -iTCP:<porta> -sTCP:LISTEN -Fpcn` e `exe_of` resolve o
/// executável de um PID (`proc_pidpath`).
pub fn classify(
    kind: Option<RuntimeKind>,
    output: &str,
    exe_of: &dyn Fn(i32) -> Option<PathBuf>,
) -> SocketOwner {
    if kind != Some(RuntimeKind::DockerDesktop) {
        return SocketOwner::Indeterminate("o dono do socket não foi observado para este runtime");
    }
    let listeners = parse_lsof(output);
    if listeners.is_empty() {
        return SocketOwner::Indeterminate("nenhum processo em escuta foi listado pelo sistema");
    }
    for (pid, command) in &listeners {
        match exe_of(*pid) {
            None => {
                return SocketOwner::Indeterminate(
                    "o executável do processo em escuta não pôde ser lido",
                )
            }
            Some(path) if !is_docker_desktop_backend(&path) => {
                let name = if command.is_empty() {
                    path.file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or("desconhecido")
                        .to_string()
                } else {
                    command.clone()
                };
                return SocketOwner::Divergent(name);
            }
            Some(_) => {}
        }
    }
    SocketOwner::Confirmed
}

/// Consulta o sistema e classifica. Qualquer falha de `lsof` (ausente, tempo esgotado, saída vazia) vira
/// "indeterminado": a checagem é uma camada a mais, nunca a única proteção.
pub fn probe(port: u16, kind: Option<RuntimeKind>) -> SocketOwner {
    if kind != Some(RuntimeKind::DockerDesktop) {
        return classify(kind, "", &|_| None);
    }
    let spec = ExecSpec::new(LSOF, LSOF_TIMEOUT).args([
        "-nP".to_string(),
        format!("-iTCP:{port}"),
        "-sTCP:LISTEN".to_string(),
        "-Fpcn".to_string(),
    ]);
    let Ok((_, lines)) = exec::run_collect(spec, &Masker::new()) else {
        return SocketOwner::Indeterminate("o lsof não pôde ser executado");
    };
    let output: String = lines
        .iter()
        .filter(|l| l.stream == Stream::Stdout)
        .map(|l| format!("{}\n", l.text.as_str()))
        .collect();
    classify(kind, &output, &|pid| SystemProc.exe_path(pid))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Saída REAL de `lsof -nP -iTCP:54245 -sTCP:LISTEN -Fpcn` com a stack de teste no ar (Docker Desktop,
    /// 2026-11-27).
    const OBSERVED: &str = "p11477\nccom.docker.backend\nf206\nn127.0.0.1:54245\n";
    const BACKEND: &str = "/Applications/Docker.app/Contents/MacOS/com.docker.backend";

    fn exe(path: &'static str) -> impl Fn(i32) -> Option<PathBuf> {
        move |_| Some(PathBuf::from(path))
    }

    #[test]
    fn observed_docker_desktop_listener_is_confirmed() {
        let owner = classify(Some(RuntimeKind::DockerDesktop), OBSERVED, &exe(BACKEND));
        assert_eq!(owner, SocketOwner::Confirmed);
    }

    #[test]
    fn another_process_on_the_port_is_divergent() {
        let output = "p4242\ncpython3\nf5\nn127.0.0.1:5672\n";
        let owner = classify(
            Some(RuntimeKind::DockerDesktop),
            output,
            &exe("/usr/bin/python3"),
        );
        assert_eq!(owner, SocketOwner::Divergent("python3".to_string()));
        // mesmo nome de executável fora do pacote do Docker Desktop não basta
        let owner = classify(
            Some(RuntimeKind::DockerDesktop),
            OBSERVED,
            &exe("/tmp/com.docker.backend"),
        );
        assert!(matches!(owner, SocketOwner::Divergent(_)));
        // todos os processos em escuta precisam ser do runtime
        let two = "p1\nccom.docker.backend\nf1\nn127.0.0.1:5672\np2\nccurl\nf1\nn127.0.0.1:5672\n";
        let owner = classify(Some(RuntimeKind::DockerDesktop), two, &|pid| {
            Some(PathBuf::from(if pid == 1 {
                BACKEND
            } else {
                "/usr/bin/curl"
            }))
        });
        assert_eq!(owner, SocketOwner::Divergent("curl".to_string()));
    }

    #[test]
    fn unobserved_runtimes_and_unreadable_answers_are_indeterminate() {
        for kind in [Some(RuntimeKind::Orbstack), Some(RuntimeKind::Colima), None] {
            assert!(matches!(
                classify(kind, OBSERVED, &exe(BACKEND)),
                SocketOwner::Indeterminate(_)
            ));
        }
        assert!(matches!(
            classify(Some(RuntimeKind::DockerDesktop), "", &exe(BACKEND)),
            SocketOwner::Indeterminate(_)
        ));
        assert!(matches!(
            classify(Some(RuntimeKind::DockerDesktop), OBSERVED, &|_| None),
            SocketOwner::Indeterminate(_)
        ));
        assert!(matches!(
            probe(5672, Some(RuntimeKind::Colima)),
            SocketOwner::Indeterminate(_)
        ));
    }

    #[test]
    fn a_listener_of_this_very_test_process_is_divergent_for_docker_desktop() {
        // Sonda real, sem Docker: um listener deste processo de teste não pode ser o `com.docker.backend`.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        match probe(port, Some(RuntimeKind::DockerDesktop)) {
            SocketOwner::Divergent(name) => assert!(!name.is_empty()),
            // sem `lsof` utilizável (ambiente restrito) a checagem é indeterminada, nunca "conferida"
            SocketOwner::Indeterminate(_) => {}
            SocketOwner::Confirmed => {
                panic!("um listener qualquer foi dado como o do Docker Desktop")
            }
        }
    }
}
