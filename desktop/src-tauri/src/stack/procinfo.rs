//! Inspeção de processos do macOS para conferir PID/PGID antes de qualquer sinal (plan.md M8;
//! research Decision 12; tarefa 11.3).
//!
//! Fontes das chamadas (conferidas no `libc` 0.2.191 e no SDK do macOS, 2026-11-27):
//! - `proc_listpids`, `proc_pidinfo` e `proc_pidpath`: `libc-0.2.191/src/unix/bsd/apple/mod.rs`
//!   (blocos `extern "C"` de `libproc.h`, somente `target_os = "macos"`), com `PROC_PIDTBSDINFO` e
//!   `proc_bsdinfo` (campos `pbi_pgid`, `pbi_start_tvsec`) do mesmo arquivo.
//! - `PROC_PGRP_ONLY`: o `libc` 0.2.191 NÃO a declara; o valor 2 vem de `sys/proc_info.h` do SDK
//!   (`#define PROC_PGRP_ONLY 2`), declarado aqui como constante com essa fonte.
//! - Argumentos de um processo: `sysctl({CTL_KERN, KERN_PROCARGS2, pid})` (`CTL_KERN` e
//!   `KERN_PROCARGS2` no `libc`); o buffer começa com `argc` (int), o caminho do executável, NULs de
//!   preenchimento e os `argc` argumentos terminados em NUL.
//!
//! Qualquer falha devolve `None`: quem chama trata como "não confirmado" e NÃO envia sinal
//! (falha fechada de M8, `plan.md`).

use std::path::PathBuf;

/// `PROC_PGRP_ONLY` de `sys/proc_info.h` (o `libc` 0.2.191 não declara).
#[cfg(target_os = "macos")]
const PROC_PGRP_ONLY: u32 = 2;

/// Consultas de processo usadas pelo pool. Um valor `None` significa "não foi possível saber".
pub trait ProcInspector: Send + Sync {
    /// PIDs do grupo; `Some(vazio)` quando o grupo não tem membros.
    fn group_members(&self, pgid: i32) -> Option<Vec<i32>>;
    /// Início do processo, em segundos Unix; `None` se o processo não existe ou a chamada falhou.
    fn start_time(&self, pid: i32) -> Option<u64>;
    /// Grupo de processos do `pid`.
    fn group_of(&self, pid: i32) -> Option<i32>;
    /// Caminho do executável (`proc_pidpath`).
    fn exe_path(&self, pid: i32) -> Option<PathBuf>;
    /// Argumentos (`argv`), do primeiro ao último.
    fn args(&self, pid: i32) -> Option<Vec<String>>;
}

/// Implementação real, sobre o sistema operacional.
#[derive(Debug, Clone, Copy, Default)]
pub struct SystemProc;

/// Extrai `argv` do buffer de `KERN_PROCARGS2`: `int argc`, caminho do executável (NUL),
/// preenchimento de NULs e `argc` argumentos NUL-terminados. Função pura (testável em qualquer SO).
pub fn parse_procargs2(buf: &[u8]) -> Option<Vec<String>> {
    let header: [u8; 4] = buf.get(..4)?.try_into().ok()?;
    let argc = i32::from_ne_bytes(header);
    if argc < 0 {
        return None;
    }
    let mut pos = 4;
    // Caminho do executável.
    while *buf.get(pos)? != 0 {
        pos += 1;
    }
    // Preenchimento de NULs entre o caminho e o primeiro argumento.
    while pos < buf.len() && buf[pos] == 0 {
        pos += 1;
    }
    let mut args = Vec::with_capacity(argc as usize);
    for _ in 0..argc {
        if pos >= buf.len() {
            return None;
        }
        let end = buf[pos..].iter().position(|b| *b == 0)? + pos;
        args.push(String::from_utf8_lossy(&buf[pos..end]).into_owned());
        pos = end + 1;
    }
    Some(args)
}

#[cfg(target_os = "macos")]
mod mac {
    use super::*;
    use std::ffi::c_void;
    use std::mem::size_of;

    pub fn group_members(pgid: i32) -> Option<Vec<i32>> {
        if pgid <= 0 {
            return None;
        }
        // SAFETY: `proc_listpids` só escreve em `buf` até `buffersize` bytes; com buffer nulo e
        // tamanho 0 devolve o tamanho necessário.
        let need =
            unsafe { libc::proc_listpids(PROC_PGRP_ONLY, pgid as u32, std::ptr::null_mut(), 0) };
        if need < 0 {
            return None;
        }
        // Folga: membros podem surgir entre as duas chamadas.
        let cap = need as usize / size_of::<i32>() + 32;
        let mut buf = vec![0i32; cap];
        let got = unsafe {
            libc::proc_listpids(
                PROC_PGRP_ONLY,
                pgid as u32,
                buf.as_mut_ptr() as *mut c_void,
                (cap * size_of::<i32>()) as libc::c_int,
            )
        };
        if got < 0 {
            return None;
        }
        let count = got as usize / size_of::<i32>();
        Some(
            buf[..count.min(cap)]
                .iter()
                .copied()
                .filter(|p| *p > 0)
                .collect(),
        )
    }

    fn bsd_info(pid: i32) -> Option<libc::proc_bsdinfo> {
        if pid <= 0 {
            return None;
        }
        // SAFETY: `proc_bsdinfo` é uma estrutura de inteiros e arrays, válida zerada; `proc_pidinfo`
        // escreve no máximo `size_of::<proc_bsdinfo>()` bytes.
        let mut info: libc::proc_bsdinfo = unsafe { std::mem::zeroed() };
        let size = size_of::<libc::proc_bsdinfo>() as libc::c_int;
        let got = unsafe {
            libc::proc_pidinfo(
                pid,
                libc::PROC_PIDTBSDINFO,
                0,
                &mut info as *mut _ as *mut c_void,
                size,
            )
        };
        (got == size).then_some(info)
    }

    pub fn start_time(pid: i32) -> Option<u64> {
        bsd_info(pid).map(|i| i.pbi_start_tvsec)
    }

    pub fn group_of(pid: i32) -> Option<i32> {
        bsd_info(pid).map(|i| i.pbi_pgid as i32)
    }

    pub fn exe_path(pid: i32) -> Option<PathBuf> {
        use std::os::unix::ffi::OsStringExt;
        if pid <= 0 {
            return None;
        }
        let mut buf = vec![0u8; libc::PROC_PIDPATHINFO_MAXSIZE as usize];
        // SAFETY: `proc_pidpath` escreve no máximo `buffersize` bytes em `buf`.
        let len =
            unsafe { libc::proc_pidpath(pid, buf.as_mut_ptr() as *mut c_void, buf.len() as u32) };
        if len <= 0 {
            return None;
        }
        buf.truncate(len as usize);
        Some(PathBuf::from(std::ffi::OsString::from_vec(buf)))
    }

    pub fn args(pid: i32) -> Option<Vec<String>> {
        if pid <= 0 {
            return None;
        }
        let mut mib = [libc::CTL_KERN, libc::KERN_PROCARGS2, pid];
        let mut size: libc::size_t = 0;
        // SAFETY: primeira chamada só pergunta o tamanho (`oldp` nulo); a segunda escreve até
        // `size` bytes em `buf`.
        let rc = unsafe {
            libc::sysctl(
                mib.as_mut_ptr(),
                3,
                std::ptr::null_mut(),
                &mut size,
                std::ptr::null_mut(),
                0,
            )
        };
        if rc != 0 || size == 0 {
            return None;
        }
        let mut buf = vec![0u8; size + 4096];
        let mut len: libc::size_t = buf.len();
        let rc = unsafe {
            libc::sysctl(
                mib.as_mut_ptr(),
                3,
                buf.as_mut_ptr() as *mut c_void,
                &mut len,
                std::ptr::null_mut(),
                0,
            )
        };
        if rc != 0 {
            return None;
        }
        buf.truncate(len);
        parse_procargs2(&buf)
    }
}

#[cfg(target_os = "macos")]
impl ProcInspector for SystemProc {
    fn group_members(&self, pgid: i32) -> Option<Vec<i32>> {
        mac::group_members(pgid)
    }
    fn start_time(&self, pid: i32) -> Option<u64> {
        mac::start_time(pid)
    }
    fn group_of(&self, pid: i32) -> Option<i32> {
        mac::group_of(pid)
    }
    fn exe_path(&self, pid: i32) -> Option<PathBuf> {
        mac::exe_path(pid)
    }
    fn args(&self, pid: i32) -> Option<Vec<String>> {
        mac::args(pid)
    }
}

/// Fora do macOS nada é conferível: todas as respostas são `None` (falha fechada de M8).
#[cfg(not(target_os = "macos"))]
impl ProcInspector for SystemProc {
    fn group_members(&self, _pgid: i32) -> Option<Vec<i32>> {
        None
    }
    fn start_time(&self, _pid: i32) -> Option<u64> {
        None
    }
    fn group_of(&self, _pid: i32) -> Option<i32> {
        None
    }
    fn exe_path(&self, _pid: i32) -> Option<PathBuf> {
        None
    }
    fn args(&self, _pid: i32) -> Option<Vec<String>> {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn procargs(argc: i32, exe: &str, args: &[&str]) -> Vec<u8> {
        let mut buf = argc.to_ne_bytes().to_vec();
        buf.extend_from_slice(exe.as_bytes());
        buf.extend_from_slice(&[0, 0, 0]);
        for a in args {
            buf.extend_from_slice(a.as_bytes());
            buf.push(0);
        }
        buf.extend_from_slice(b"HOME=/x\0");
        buf
    }

    #[test]
    fn parses_procargs2_layout_and_ignores_environment() {
        let buf = procargs(3, "/u/uv", &["uv", "run", "worker"]);
        assert_eq!(
            parse_procargs2(&buf),
            Some(vec!["uv".into(), "run".into(), "worker".into()])
        );
    }

    #[test]
    fn truncated_or_invalid_procargs2_is_none() {
        assert_eq!(parse_procargs2(&[]), None);
        assert_eq!(parse_procargs2(&[1, 0, 0]), None);
        // argc maior que os argumentos presentes.
        assert_eq!(parse_procargs2(&procargs(1, "/u/uv", &[])[..9]), None);
        assert_eq!(parse_procargs2(&(-1i32).to_ne_bytes()), None);
    }

    #[cfg(target_os = "macos")]
    mod system {
        use super::super::*;
        use std::os::unix::process::CommandExt;
        use std::process::{Command, Stdio};
        use std::time::{Duration, SystemTime, UNIX_EPOCH};

        fn now() -> u64 {
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_secs()
        }

        #[test]
        fn queries_the_current_process() {
            let me = std::process::id() as i32;
            let sys = SystemProc;
            let start = sys.start_time(me).expect("start_time do próprio processo");
            assert!(
                start <= now() && start + 3600 * 24 * 7 > now(),
                "início plausível: {start}"
            );
            assert_eq!(sys.group_of(me), Some(unsafe { libc::getpgrp() }));
            let exe = sys.exe_path(me).expect("proc_pidpath do próprio processo");
            assert_eq!(
                std::fs::canonicalize(exe).unwrap(),
                std::fs::canonicalize(std::env::current_exe().unwrap()).unwrap()
            );
            let args = sys.args(me).expect("argumentos do próprio processo");
            let expected: Vec<String> = std::env::args().collect();
            assert_eq!(args, expected);
        }

        #[test]
        fn lists_the_members_of_a_group_created_by_the_test() {
            let sys = SystemProc;
            let mut child = Command::new("/bin/sleep")
                .arg("30")
                .stdin(Stdio::null())
                .process_group(0)
                .spawn()
                .unwrap();
            let pid = child.id() as i32;
            let members = sys.group_members(pid).expect("proc_listpids do grupo");
            assert!(members.contains(&pid), "membros: {members:?}");
            assert_eq!(sys.group_of(pid), Some(pid));
            assert_eq!(
                sys.args(pid),
                Some(vec!["/bin/sleep".to_string(), "30".to_string()])
            );
            assert_eq!(
                std::fs::canonicalize(sys.exe_path(pid).unwrap()).unwrap(),
                std::fs::canonicalize("/bin/sleep").unwrap()
            );

            child.kill().unwrap();
            child.wait().unwrap();
            // Grupo sem membros: lista vazia (ou falha); nunca o PID antigo.
            std::thread::sleep(Duration::from_millis(50));
            assert!(sys.group_members(pid).is_none_or(|m| !m.contains(&pid)));
            assert_eq!(sys.start_time(pid), None);
        }

        #[test]
        fn invalid_pids_are_not_confirmed() {
            let sys = SystemProc;
            assert_eq!(sys.group_members(0), None);
            assert_eq!(sys.start_time(-1), None);
            assert_eq!(sys.exe_path(0), None);
            assert_eq!(sys.args(0), None);
        }
    }
}
