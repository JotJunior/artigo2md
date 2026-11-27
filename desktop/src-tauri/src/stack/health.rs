//! Sondagens de saúde em loopback (spec FR-026, FR-027, FR-028, FR-037; research Decision 10;
//! plan.md M13). Reimplementa o critério de `src/artigo2md/infra.py` (`check_postgres`,
//! `check_rabbitmq`, `check_api`), sem credenciais e só em `127.0.0.1`.
//!
//! Regras do cliente (M13): `TcpStream` direto para `SocketAddr` `127.0.0.1:<porta>` (sem DNS, sem
//! proxy, sem seguir redirecionamento), resposta limitada a 64 KiB, 1 s para conectar e 1 s para ler,
//! com teto de 2 s por sondagem (`infra.py:22-26`). Nenhuma sondagem devolve erro: falha = `false`.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::stack::clock;
use crate::stack::config::Ports;
use crate::stack::exec::{self, Stream};
use crate::stack::lifecycle::SERVICES;
use crate::stack::runtime::DockerEnv;
use crate::stack::secret::Masker;

pub const CONNECT_TIMEOUT: Duration = Duration::from_secs(1);
pub const READ_TIMEOUT: Duration = Duration::from_secs(1);
/// Teto de cada sondagem (conectar + ler), como `CHECK_DEADLINE` do `infra.py`.
pub const PROBE_DEADLINE: Duration = Duration::from_secs(2);
/// Resposta lida no máximo até aqui (M13).
pub const MAX_RESPONSE_BYTES: usize = 64 * 1024;
/// Tempo máximo de `compose ps --status running --services` na rodada de sondagem (Decision 10).
pub const COMPOSE_PS_TIMEOUT: Duration = Duration::from_secs(3);

/// SSLRequest do Postgres: `!II (8, 80877103)` (`infra.py:38`).
const POSTGRES_SSL_REQUEST: [u8; 8] = [0, 0, 0, 8, 0x04, 0xd2, 0x16, 0x2f];
/// Cabeçalho AMQP 0-9-1 (`infra.py:40`).
const AMQP_HEADER: &[u8; 8] = b"AMQP\x00\x00\x09\x01";

fn loopback(port: u16) -> SocketAddr {
    SocketAddr::from(([127, 0, 0, 1], port))
}

fn connect(port: u16) -> Option<TcpStream> {
    let stream = TcpStream::connect_timeout(&loopback(port), CONNECT_TIMEOUT).ok()?;
    stream.set_nodelay(true).ok()?;
    Some(stream)
}

/// Lê até `min` bytes (ou fim/tempo): cada leitura espera no máximo `READ_TIMEOUT` e o conjunto
/// respeita o prazo. Devolve o que chegou, no máximo `MAX_RESPONSE_BYTES`.
fn read_at_least(stream: &mut TcpStream, min: usize, deadline: Instant) -> Vec<u8> {
    let mut data: Vec<u8> = Vec::new();
    let mut buf = [0u8; 4096];
    while data.len() < min {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            break;
        }
        if stream
            .set_read_timeout(Some(remaining.min(READ_TIMEOUT)))
            .is_err()
        {
            break;
        }
        match stream.read(&mut buf) {
            Ok(0) | Err(_) => break,
            Ok(n) => data.extend_from_slice(&buf[..n]),
        }
    }
    data.truncate(MAX_RESPONSE_BYTES);
    data
}

/// Postgres: SSLRequest e primeiro byte `S`, `N` ou `E` (`infra.py:165-177`). O texto de um
/// `ErrorResponse` nunca é lido.
pub fn probe_postgres(port: u16) -> bool {
    let deadline = Instant::now() + PROBE_DEADLINE;
    let Some(mut stream) = connect(port) else {
        return false;
    };
    if stream.write_all(&POSTGRES_SSL_REQUEST).is_err() {
        return false;
    }
    matches!(
        read_at_least(&mut stream, 1, deadline).first(),
        Some(b'S' | b'N' | b'E')
    )
}

/// RabbitMQ: cabeçalho AMQP e quadro tipo 1 no canal 0 (`infra.py:180-193`). Um eco do cabeçalho
/// (`AMQP…`) é versão de protocolo incompatível, não saúde.
pub fn probe_rabbitmq(port: u16) -> bool {
    let deadline = Instant::now() + PROBE_DEADLINE;
    let Some(mut stream) = connect(port) else {
        return false;
    };
    if stream.write_all(AMQP_HEADER).is_err() {
        return false;
    }
    let data = read_at_least(&mut stream, 4, deadline);
    if data.starts_with(b"AMQP") {
        return false;
    }
    data.len() >= 3 && data[0] == 1 && data[1] == 0 && data[2] == 0
}

/// Lê de `reader` no máximo `limit` bytes e ignora o resto.
fn read_limited<R: Read>(reader: R, limit: usize) -> Vec<u8> {
    let mut data = Vec::new();
    let _ = reader.take(limit as u64).read_to_end(&mut data);
    data
}

/// Lê a resposta HTTP inteira (até o limite e o prazo) com `Connection: close`.
fn http_get(port: u16, path: &str) -> Option<Vec<u8>> {
    let deadline = Instant::now() + PROBE_DEADLINE;
    let mut stream = connect(port)?;
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut data: Vec<u8> = Vec::new();
    let mut buf = [0u8; 8192];
    while data.len() < MAX_RESPONSE_BYTES {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            break;
        }
        stream
            .set_read_timeout(Some(remaining.min(READ_TIMEOUT)))
            .ok()?;
        match stream.read(&mut buf) {
            Ok(0) | Err(_) => break,
            Ok(n) => data.extend_from_slice(&buf[..n]),
        }
    }
    data.truncate(MAX_RESPONSE_BYTES);
    Some(data)
}

/// Decodifica `Transfer-Encoding: chunked`; trecho malformado ou truncado devolve o que já foi lido.
fn decode_chunked(mut body: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    while let Some(eol) = body.windows(2).position(|w| w == b"\r\n") {
        let size_text = String::from_utf8_lossy(&body[..eol]);
        let size_text = size_text.split(';').next().unwrap_or("").trim();
        let Ok(size) = usize::from_str_radix(size_text, 16) else {
            break;
        };
        body = &body[eol + 2..];
        if size == 0 {
            break;
        }
        let take = size.min(body.len());
        out.extend_from_slice(&body[..take]);
        if take < size {
            break;
        }
        body = &body[take..];
        body = body.strip_prefix(b"\r\n").unwrap_or(body);
    }
    out
}

/// Separa a resposta em (código de status, corpo). Redirecionamento nunca é seguido: o código
/// 3xx é devolvido como está e as sondagens exigem 200.
fn parse_http_response(raw: &[u8]) -> Option<(u16, Vec<u8>)> {
    let split = raw.windows(4).position(|w| w == b"\r\n\r\n")?;
    let head = String::from_utf8_lossy(&raw[..split]).to_string();
    let mut lines = head.lines();
    let status_line = lines.next()?;
    let mut parts = status_line.split_whitespace();
    if !parts.next()?.starts_with("HTTP/1.") {
        return None;
    }
    let status: u16 = parts.next()?.parse().ok()?;
    let chunked = lines.any(|l| {
        let lower = l.to_ascii_lowercase();
        lower.starts_with("transfer-encoding:") && lower.contains("chunked")
    });
    let body = &raw[split + 4..];
    let body = if chunked {
        decode_chunked(body)
    } else {
        body.to_vec()
    };
    Some((status, body))
}

fn http_json(port: u16, path: &str) -> Option<serde_json::Value> {
    let raw = http_get(port, path)?;
    let (status, body) = parse_http_response(&raw)?;
    if status != 200 {
        return None;
    }
    serde_json::from_slice(&read_limited(body.as_slice(), MAX_RESPONSE_BYTES)).ok()
}

/// API: `GET /health` com 200 e `status == "ok"` (`infra.py:196-210`).
pub fn probe_api(port: u16) -> bool {
    http_json(port, "/health")
        .and_then(|v| v.get("status").and_then(|s| s.as_str()).map(|s| s == "ok"))
        .unwrap_or(false)
}

/// `GET /v1/status` (`src/artigo2md/api/routes/status.py:17-26`). Só os campos usados são lidos.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct ApiStatus {
    pub consumer: ConsumerStatus,
    pub publisher: PublisherStatus,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct ConsumerStatus {
    pub connected: bool,
    #[serde(default)]
    pub last_event_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct PublisherStatus {
    pub connected: bool,
}

/// `GET /v1/status` desserializado em `ApiStatus`; `None` com a API fora do ar ou resposta fora do
/// formato.
pub fn fetch_api_status(port: u16) -> Option<ApiStatus> {
    serde_json::from_value(http_json(port, "/v1/status")?).ok()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum ServiceName {
    Postgres,
    Rabbitmq,
    Api,
}

impl ServiceName {
    pub const ALL: [ServiceName; 3] = [
        ServiceName::Postgres,
        ServiceName::Rabbitmq,
        ServiceName::Api,
    ];

    /// Nome do serviço no `compose.yaml`.
    pub fn compose_name(self) -> &'static str {
        match self {
            ServiceName::Postgres => SERVICES[0],
            ServiceName::Rabbitmq => SERVICES[1],
            ServiceName::Api => SERVICES[2],
        }
    }
}

/// Os quatro estados de FR-026 ("parado", "iniciando", "saudável", "com problema").
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum ServiceState {
    Stopped,
    Starting,
    Healthy,
    Unhealthy,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ServiceStatus {
    pub service: ServiceName,
    pub state: ServiceState,
    /// `127.0.0.1:<porta do .env>`.
    pub address: String,
    pub checked_at: String,
}

/// Regra da Decision 10: sondagem ok = saudável; falhando, "iniciando" durante a operação de subir
/// e, fora dela, "com problema" se o Compose o lista em execução ou "parado" se não.
pub fn classify(compose_running: bool, probe_ok: bool, start_in_progress: bool) -> ServiceState {
    if probe_ok {
        ServiceState::Healthy
    } else if start_in_progress {
        ServiceState::Starting
    } else if compose_running {
        ServiceState::Unhealthy
    } else {
        ServiceState::Stopped
    }
}

/// Resultado de uma rodada de sondagem.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServicesProbe {
    pub services: Vec<ServiceStatus>,
    /// `consumer.connected` de `GET /v1/status`; `None` com a API fora do ar.
    pub consumer_connected: Option<bool>,
}

fn address(port: u16) -> String {
    format!("127.0.0.1:{port}")
}

/// Rodada completa: as três sondagens em paralelo (até 2 s cada) e `compose ps` (até 3 s). Sem
/// `env` (runtime que não está `ready`) os três aparecem "parado", sem sondar nada: a falha do
/// runtime é mostrada no diagnóstico, não aqui. Serviço parado nunca vira erro.
pub fn probe_services(
    env: Option<&DockerEnv>,
    ports: Ports,
    start_in_progress: bool,
) -> ServicesProbe {
    let checked_at = clock::now_iso8601();
    let port_of = |name: ServiceName| match name {
        ServiceName::Postgres => ports.postgres,
        ServiceName::Rabbitmq => ports.rabbitmq,
        ServiceName::Api => ports.api,
    };
    let stopped = || ServicesProbe {
        services: ServiceName::ALL
            .iter()
            .map(|name| ServiceStatus {
                service: *name,
                state: ServiceState::Stopped,
                address: address(port_of(*name)),
                checked_at: checked_at.clone(),
            })
            .collect(),
        consumer_connected: None,
    };
    let Some(env) = env else {
        return stopped();
    };

    let (running, ok, consumer) = thread::scope(|scope| {
        let ps = scope.spawn(|| running_in_compose(env));
        let pg = scope.spawn(|| probe_postgres(ports.postgres));
        let mq = scope.spawn(|| probe_rabbitmq(ports.rabbitmq));
        let api = scope.spawn(|| {
            let healthy = probe_api(ports.api);
            let consumer = healthy
                .then(|| fetch_api_status(ports.api).map(|s| s.consumer.connected))
                .flatten();
            (healthy, consumer)
        });
        let (api_ok, consumer) = api.join().unwrap_or((false, None));
        (
            ps.join().unwrap_or(None),
            [
                pg.join().unwrap_or(false),
                mq.join().unwrap_or(false),
                api_ok,
            ],
            consumer,
        )
    });
    // Compose que não respondeu (nem a consulta): sem como dizer "em execução"; trata como parado.
    let Some(running) = running else {
        return stopped();
    };
    let services = ServiceName::ALL
        .iter()
        .enumerate()
        .map(|(i, name)| ServiceStatus {
            service: *name,
            state: classify(
                running.iter().any(|r| r == name.compose_name()),
                ok[i],
                start_in_progress,
            ),
            address: address(port_of(*name)),
            checked_at: checked_at.clone(),
        })
        .collect();
    ServicesProbe {
        services,
        consumer_connected: consumer,
    }
}

/// Serviços em execução para o Compose; `None` se a consulta falhou ou estourou o tempo.
fn running_in_compose(env: &DockerEnv) -> Option<Vec<String>> {
    let spec =
        env.compose_spec(COMPOSE_PS_TIMEOUT)
            .args(["ps", "--status", "running", "--services"]);
    let (outcome, lines) = exec::run_collect(spec, &Masker::new()).ok()?;
    outcome.success().then(|| {
        lines
            .iter()
            .filter(|l| l.stream == Stream::Stdout)
            .map(|l| l.text.as_str().trim().to_string())
            .filter(|s| !s.is_empty())
            .collect()
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::net::TcpListener;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use std::thread::JoinHandle;

    /// Servidor falso de uma conexão: lê o que chega (até 4 KiB), responde `response` e fecha.
    /// Devolve a porta e o que o cliente enviou. `hold` mantém a conexão aberta sem responder.
    fn serve(response: Vec<u8>, hold: Option<Duration>) -> (u16, JoinHandle<Vec<u8>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let handle = thread::spawn(move || {
            let (mut conn, _) = listener.accept().unwrap();
            conn.set_read_timeout(Some(Duration::from_millis(500))).ok();
            let mut buf = [0u8; 4096];
            let n = conn.read(&mut buf).unwrap_or(0);
            if let Some(wait) = hold {
                thread::sleep(wait);
            } else {
                let _ = conn.write_all(&response);
            }
            buf[..n].to_vec()
        });
        (port, handle)
    }

    fn http_response(status: &str, extra_headers: &str, body: &str) -> Vec<u8> {
        format!(
            "HTTP/1.1 {status}\r\n{extra_headers}Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        )
        .into_bytes()
    }

    fn closed_port() -> u16 {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        l.local_addr().unwrap().port()
    }

    #[test]
    fn postgres_accepts_s_n_or_e_and_sends_the_ssl_request() {
        for first in *b"SNE" {
            let (port, sent) = serve(vec![first], None);
            assert!(probe_postgres(port), "primeiro byte {}", first as char);
            // SSLRequest: Int32(8) + Int32(80877103) em ordem de rede.
            assert_eq!(sent.join().unwrap(), vec![0, 0, 0, 8, 4, 210, 22, 47]);
        }
        let (port, _h) = serve(b"X".to_vec(), None);
        assert!(!probe_postgres(port));
        let (port, _h) = serve(Vec::new(), None);
        assert!(!probe_postgres(port));
    }

    #[test]
    fn rabbitmq_accepts_a_method_frame_on_channel_zero() {
        let (port, sent) = serve(vec![1, 0, 0, 0, 0, 0, 5], None);
        assert!(probe_rabbitmq(port));
        assert_eq!(sent.join().unwrap(), b"AMQP\x00\x00\x09\x01".to_vec());
        // Eco do cabeçalho = versão incompatível; outro tipo de quadro; canal diferente de 0.
        let (port, _h) = serve(b"AMQP\x00\x00\x09\x01".to_vec(), None);
        assert!(!probe_rabbitmq(port));
        let (port, _h) = serve(vec![2, 0, 0, 0], None);
        assert!(!probe_rabbitmq(port));
        let (port, _h) = serve(vec![1, 0, 1, 0], None);
        assert!(!probe_rabbitmq(port));
    }

    #[test]
    fn api_requires_200_and_status_ok() {
        let ok = r#"{"status":"ok","service":"artigo2md-api","version":"0.1.0"}"#;
        let (port, sent) = serve(
            http_response("200 OK", "Content-Type: application/json\r\n", ok),
            None,
        );
        assert!(probe_api(port));
        let request = String::from_utf8(sent.join().unwrap()).unwrap();
        assert!(request.starts_with("GET /health HTTP/1.1\r\n"), "{request}");
        assert!(request.contains(&format!("Host: 127.0.0.1:{port}")));

        let (port, _h) = serve(
            http_response("200 OK", "", r#"{"status":"degraded"}"#),
            None,
        );
        assert!(!probe_api(port));
        let (port, _h) = serve(http_response("500 Internal Server Error", "", ok), None);
        assert!(!probe_api(port));
        let (port, _h) = serve(http_response("200 OK", "", "não é json"), None);
        assert!(!probe_api(port));
        let (port, _h) = serve(b"lixo qualquer".to_vec(), None);
        assert!(!probe_api(port));
    }

    #[test]
    fn api_accepts_a_chunked_response() {
        let body = r#"{"status":"ok"}"#;
        let raw = format!(
            "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n{:x}\r\n{body}\r\n0\r\n\r\n",
            body.len()
        );
        let (port, _h) = serve(raw.into_bytes(), None);
        assert!(probe_api(port));
    }

    #[test]
    fn api_never_follows_a_redirect() {
        // O alvo do redirecionamento é um segundo servidor: se o cliente o seguisse, ele aceitaria
        // uma conexão e responderia 200 `ok`.
        let hits = Arc::new(AtomicUsize::new(0));
        let target = TcpListener::bind("127.0.0.1:0").unwrap();
        let target_port = target.local_addr().unwrap().port();
        target.set_nonblocking(true).unwrap();
        let counter = Arc::clone(&hits);
        let watcher = thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_millis(1500);
            while Instant::now() < deadline {
                if let Ok((mut c, _)) = target.accept() {
                    counter.fetch_add(1, Ordering::SeqCst);
                    let _ = c.write_all(&http_response("200 OK", "", r#"{"status":"ok"}"#));
                }
                thread::sleep(Duration::from_millis(20));
            }
        });
        let location = format!("Location: http://127.0.0.1:{target_port}/health\r\n");
        let (port, _h) = serve(http_response("301 Moved Permanently", &location, ""), None);
        assert!(!probe_api(port));
        watcher.join().unwrap();
        assert_eq!(hits.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn response_larger_than_64_kib_is_truncated_not_buffered() {
        // Leitura limitada: um fluxo infinito devolve exatamente o limite.
        assert_eq!(
            read_limited(std::io::repeat(b'a'), MAX_RESPONSE_BYTES).len(),
            MAX_RESPONSE_BYTES
        );
        // Ponta a ponta: 1 MiB de corpo não trava nem estoura; o JSON truncado não é "ok".
        let big = format!(r#"{{"status":"ok","lixo":"{}"}}"#, "x".repeat(1 << 20));
        let (port, _h) = serve(http_response("200 OK", "", &big), None);
        let started = Instant::now();
        assert!(!probe_api(port));
        assert!(started.elapsed() < Duration::from_secs(3));
        let raw = http_get(
            {
                let (p, _h) = serve(http_response("200 OK", "", &big), None);
                p
            },
            "/health",
        )
        .unwrap();
        assert!(raw.len() <= MAX_RESPONSE_BYTES);
    }

    #[test]
    fn silent_server_and_closed_port_time_out_quietly() {
        let started = Instant::now();
        let (port, _h) = serve(Vec::new(), Some(Duration::from_millis(2600)));
        assert!(!probe_postgres(port));
        let (port, _h) = serve(Vec::new(), Some(Duration::from_millis(2600)));
        assert!(!probe_rabbitmq(port));
        let (port, _h) = serve(Vec::new(), Some(Duration::from_millis(2600)));
        assert!(!probe_api(port));
        // Cada uma espera no máximo 1 s de leitura (+ folga), nunca os 2,6 s do servidor.
        assert!(
            started.elapsed() < Duration::from_secs(6),
            "{:?}",
            started.elapsed()
        );
        let dead = closed_port();
        assert!(!probe_postgres(dead));
        assert!(!probe_rabbitmq(dead));
        assert!(!probe_api(dead));
        assert!(fetch_api_status(dead).is_none());
    }

    #[test]
    fn api_status_is_deserialized_into_its_own_type() {
        let body = r#"{"consumer":{"connected":true,"last_event_at":"2026-11-26T10:00:00Z"},"publisher":{"connected":false},"extra":1}"#;
        let (port, sent) = serve(http_response("200 OK", "", body), None);
        let status = fetch_api_status(port).unwrap();
        assert!(String::from_utf8(sent.join().unwrap())
            .unwrap()
            .starts_with("GET /v1/status HTTP/1.1"));
        assert!(status.consumer.connected);
        assert_eq!(
            status.consumer.last_event_at.as_deref(),
            Some("2026-11-26T10:00:00Z")
        );
        assert!(!status.publisher.connected);

        let (port, _h) = serve(
            http_response(
                "200 OK",
                "",
                r#"{"consumer":{"connected":false,"last_event_at":null},"publisher":{"connected":true}}"#,
            ),
            None,
        );
        assert!(!fetch_api_status(port).unwrap().consumer.connected);
        let (port, _h) = serve(http_response("200 OK", "", r#"{"other":"thing"}"#), None);
        assert!(fetch_api_status(port).is_none());
    }

    #[test]
    fn classify_covers_the_four_states() {
        // (em execução no Compose, sondagem ok, operação de subir em andamento)
        assert_eq!(classify(true, true, false), ServiceState::Healthy);
        assert_eq!(classify(false, true, false), ServiceState::Healthy);
        assert_eq!(classify(true, false, true), ServiceState::Starting);
        assert_eq!(classify(false, false, true), ServiceState::Starting);
        assert_eq!(classify(true, false, false), ServiceState::Unhealthy);
        assert_eq!(classify(false, false, false), ServiceState::Stopped);
    }

    const FAKE_DOCKER: &str = "#!/bin/sh\nd=$(dirname \"$0\")\ncase \" $* \" in\n  *\" ps --status running --services \"*) cat \"$d/running.txt\" 2>/dev/null; exit $(cat \"$d/ps.code\" 2>/dev/null || echo 0);;\nesac\nexit 99\n";

    fn docker_env(dir: &std::path::Path) -> DockerEnv {
        let bin = dir.join("docker");
        fs::write(&bin, FAKE_DOCKER).unwrap();
        fs::set_permissions(&bin, fs::Permissions::from_mode(0o755)).unwrap();
        DockerEnv {
            docker: bin,
            context: "orbstack".to_string(),
            path_dirs: vec![dir.to_path_buf()],
            repo: dir.join("repo"),
        }
    }

    #[test]
    fn stopped_service_is_never_an_error_and_docker_down_means_all_stopped() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = fs::canonicalize(tmp.path()).unwrap();
        let env = docker_env(&dir);
        let ports = Ports {
            postgres: closed_port(),
            rabbitmq: closed_port(),
            api: closed_port(),
        };
        // Compose sem serviço em execução e nenhuma porta ouvindo: tudo parado.
        let probe = probe_services(Some(&env), ports, false);
        assert_eq!(probe.consumer_connected, None);
        assert_eq!(probe.services.len(), 3);
        assert!(probe
            .services
            .iter()
            .all(|s| s.state == ServiceState::Stopped));
        assert_eq!(
            probe.services[0].address,
            format!("127.0.0.1:{}", ports.postgres)
        );

        // Durante "iniciar infraestrutura", a mesma situação é "iniciando".
        let probe = probe_services(Some(&env), ports, true);
        assert!(probe
            .services
            .iter()
            .all(|s| s.state == ServiceState::Starting));

        // Compose diz "em execução" mas nada responde: com problema.
        fs::write(dir.join("running.txt"), "postgres\nrabbitmq\napi\n").unwrap();
        let probe = probe_services(Some(&env), ports, false);
        assert!(probe
            .services
            .iter()
            .all(|s| s.state == ServiceState::Unhealthy));

        // Consulta ao Compose falhou: nenhum "em execução" a afirmar.
        fs::write(dir.join("ps.code"), "1").unwrap();
        let probe = probe_services(Some(&env), ports, false);
        assert!(probe
            .services
            .iter()
            .all(|s| s.state == ServiceState::Stopped));

        // Runtime que não está pronto: nada é sondado, tudo parado.
        let probe = probe_services(None, ports, false);
        assert!(probe
            .services
            .iter()
            .all(|s| s.state == ServiceState::Stopped));
    }

    #[test]
    fn healthy_services_report_the_consumer_flag() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = fs::canonicalize(tmp.path()).unwrap();
        let env = docker_env(&dir);
        fs::write(dir.join("running.txt"), "postgres\nrabbitmq\napi\n").unwrap();

        // Cada sondagem abre uma conexão; os servidores aceitam várias.
        let pg = TcpListener::bind("127.0.0.1:0").unwrap();
        let mq = TcpListener::bind("127.0.0.1:0").unwrap();
        let api = TcpListener::bind("127.0.0.1:0").unwrap();
        let ports = Ports {
            postgres: pg.local_addr().unwrap().port(),
            rabbitmq: mq.local_addr().unwrap().port(),
            api: api.local_addr().unwrap().port(),
        };
        let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let mut threads = Vec::new();
        for (listener, kind) in [(pg, 0), (mq, 1), (api, 2)] {
            listener.set_nonblocking(true).unwrap();
            let stop = Arc::clone(&stop);
            threads.push(thread::spawn(move || {
                while !stop.load(Ordering::SeqCst) {
                    if let Ok((mut conn, _)) = listener.accept() {
                        conn.set_nonblocking(false).ok();
                        conn.set_read_timeout(Some(Duration::from_millis(500))).ok();
                        let mut buf = [0u8; 4096];
                        let n = conn.read(&mut buf).unwrap_or(0);
                        let request = String::from_utf8_lossy(&buf[..n]).to_string();
                        let reply: Vec<u8> = match kind {
                            0 => b"N".to_vec(),
                            1 => vec![1, 0, 0, 0, 0, 0, 0],
                            _ if request.starts_with("GET /v1/status") => http_response(
                                "200 OK",
                                "",
                                r#"{"consumer":{"connected":false,"last_event_at":null},"publisher":{"connected":true}}"#,
                            ),
                            _ => http_response("200 OK", "", r#"{"status":"ok"}"#),
                        };
                        let _ = conn.write_all(&reply);
                    }
                    thread::sleep(Duration::from_millis(10));
                }
            }));
        }
        let probe = probe_services(Some(&env), ports, false);
        stop.store(true, Ordering::SeqCst);
        for t in threads {
            t.join().unwrap();
        }
        assert!(
            probe
                .services
                .iter()
                .all(|s| s.state == ServiceState::Healthy),
            "{probe:?}"
        );
        // API no ar com o consumidor parado: o aviso "consumidor de eventos parado" (FR-028).
        assert_eq!(probe.consumer_connected, Some(false));
    }

    #[test]
    fn service_status_serializes_in_camel_case() {
        let probe = probe_services(
            None,
            Ports {
                postgres: 5432,
                rabbitmq: 5672,
                api: 8778,
            },
            false,
        );
        let json = serde_json::to_value(&probe.services).unwrap();
        assert_eq!(json[0]["service"], "postgres");
        assert_eq!(json[1]["service"], "rabbitmq");
        assert_eq!(json[2]["service"], "api");
        assert_eq!(json[0]["state"], "stopped");
        assert_eq!(json[2]["address"], "127.0.0.1:8778");
        assert!(json[0]["checkedAt"].as_str().unwrap().ends_with('Z'));
    }

    /// 11.7 (quickstart, cenário 8): roundtrip contra a stack REAL de teste. Só roda com `ARTIGO2MD_STACK=1` e
    /// com as portas da stack de teste em `ARTIGO2MD_STACK_{POSTGRES,RABBITMQ,API}_PORT` (quem as fornece é
    /// `tests/test_desktop_stack_integration.py`, que sobe um projeto Compose descartável). Sem a variável,
    /// retorna cedo com aviso. A consulta ao Compose (`compose ps`) é a do executável falso de `docker_env`: o
    /// que é real aqui são as sondagens em loopback e o `GET /v1/status` desserializado com o tipo de produção.
    #[test]
    fn real_stack_status_roundtrip_matches_the_snapshot_field() {
        if std::env::var("ARTIGO2MD_STACK").ok().as_deref() != Some("1") {
            eprintln!("aviso: roundtrip com a stack real pulado (defina ARTIGO2MD_STACK=1)");
            return;
        }
        let port = |name: &str| -> u16 {
            std::env::var(name)
                .unwrap_or_else(|_| {
                    panic!("ARTIGO2MD_STACK=1 exige {name} (porta da stack de teste)")
                })
                .parse()
                .unwrap_or_else(|_| panic!("{name} não é um número de porta"))
        };
        let ports = Ports {
            postgres: port("ARTIGO2MD_STACK_POSTGRES_PORT"),
            rabbitmq: port("ARTIGO2MD_STACK_RABBITMQ_PORT"),
            api: port("ARTIGO2MD_STACK_API_PORT"),
        };

        // payload real: `consumer.connected` é booleano (não string, não ausente)
        let raw = http_json(ports.api, "/v1/status").expect("GET /v1/status devolveu 200 com JSON");
        assert!(raw["consumer"]["connected"].is_boolean(), "{raw}");
        assert!(raw["publisher"]["connected"].is_boolean(), "{raw}");

        // o tipo de produção desserializa o payload real
        assert!(probe_api(ports.api));
        let status = fetch_api_status(ports.api).expect("o payload real desserializa em ApiStatus");
        assert_eq!(
            Some(status.consumer.connected),
            raw["consumer"]["connected"].as_bool()
        );

        // o campo do retrato (`consumerConnected`) é igual ao `consumer.connected` do payload real
        let tmp = tempfile::tempdir().unwrap();
        let dir = fs::canonicalize(tmp.path()).unwrap();
        let env = docker_env(&dir);
        fs::write(dir.join("running.txt"), "postgres\nrabbitmq\napi\n").unwrap();
        let probe = probe_services(Some(&env), ports, false);
        assert!(
            probe
                .services
                .iter()
                .all(|s| s.state == ServiceState::Healthy),
            "{probe:?}"
        );
        assert_eq!(probe.consumer_connected, Some(status.consumer.connected));

        // 11.5: com Docker Desktop, quem escuta na porta do RabbitMQ de teste é o `com.docker.backend`
        if std::env::var("ARTIGO2MD_STACK_RUNTIME").ok().as_deref() == Some("dockerDesktop") {
            assert_eq!(
                crate::stack::socket_owner::probe(
                    ports.rabbitmq,
                    Some(crate::stack::runtime::RuntimeKind::DockerDesktop)
                ),
                crate::stack::socket_owner::SocketOwner::Confirmed
            );
        }
    }
}
