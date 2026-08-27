use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const STATE_SCHEMA: &str = "universe.reconnection-host-state.v1";
const RESPONSE_SCHEMA: &str = "universe.reconnection-host-response.v1";
const MAX_REQUEST_BYTES: u64 = 16 * 1024;

#[derive(Debug)]
struct Config {
    state_file: PathBuf,
    anchor_ref: String,
    token: String,
}

#[derive(Clone, Debug, Serialize)]
struct HostSnapshot {
    schema: &'static str,
    host_id: String,
    anchor_ref: String,
    endpoint: String,
    pid: u32,
    started_at_unix_ms: u128,
    attachment_generation: u64,
    attached_supervisor_id: Option<String>,
    runtime_state: &'static str,
    auth_token: String,
}

#[derive(Debug, Deserialize)]
struct HostRequest {
    token: String,
    action: String,
    supervisor_id: Option<String>,
}

#[derive(Debug, Serialize)]
struct HostResponse {
    schema: &'static str,
    status: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_code: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    detail: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    host: Option<PublicSnapshot>,
}

#[derive(Debug, Serialize)]
struct PublicSnapshot {
    schema: &'static str,
    host_id: String,
    anchor_ref: String,
    endpoint: String,
    pid: u32,
    started_at_unix_ms: u128,
    attachment_generation: u64,
    attached_supervisor_id: Option<String>,
    runtime_state: &'static str,
}

impl From<&HostSnapshot> for PublicSnapshot {
    fn from(value: &HostSnapshot) -> Self {
        Self {
            schema: value.schema,
            host_id: value.host_id.clone(),
            anchor_ref: value.anchor_ref.clone(),
            endpoint: value.endpoint.clone(),
            pid: value.pid,
            started_at_unix_ms: value.started_at_unix_ms,
            attachment_generation: value.attachment_generation,
            attached_supervisor_id: value.attached_supervisor_id.clone(),
            runtime_state: value.runtime_state,
        }
    }
}

fn required_arg(args: &[String], name: &str) -> Result<String, String> {
    let position = args
        .iter()
        .position(|value| value == name)
        .ok_or_else(|| format!("{name} is required"))?;
    let value = args
        .get(position + 1)
        .ok_or_else(|| format!("{name} requires a value"))?
        .trim();
    if value.is_empty() {
        return Err(format!("{name} must not be empty"));
    }
    Ok(value.to_owned())
}

fn parse_config() -> Result<Config, String> {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.first().map(String::as_str) != Some("serve") {
        return Err(
            "usage: universe-session-host serve --state-file PATH --anchor-ref REF --token TOKEN"
                .to_owned(),
        );
    }
    Ok(Config {
        state_file: PathBuf::from(required_arg(&args, "--state-file")?),
        anchor_ref: required_arg(&args, "--anchor-ref")?,
        token: required_arg(&args, "--token")?,
    })
}

fn now_unix_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

fn atomic_write_state(path: &Path, state: &HostSnapshot) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "state file requires a parent directory".to_owned())?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let temporary = path.with_extension(format!("tmp-{}", process::id()));
    let mut bytes = serde_json::to_vec_pretty(state).map_err(|error| error.to_string())?;
    bytes.push(b'\n');
    fs::write(&temporary, bytes).map_err(|error| error.to_string())?;
    if path.exists() {
        fs::remove_file(path).map_err(|error| error.to_string())?;
    }
    fs::rename(&temporary, path).map_err(|error| error.to_string())
}

fn success(state: &HostSnapshot) -> HostResponse {
    HostResponse {
        schema: RESPONSE_SCHEMA,
        status: "OK",
        error_code: None,
        detail: None,
        host: Some(PublicSnapshot::from(state)),
    }
}

fn failure(code: &'static str, detail: impl Into<String>) -> HostResponse {
    HostResponse {
        schema: RESPONSE_SCHEMA,
        status: "ERROR",
        error_code: Some(code),
        detail: Some(detail.into()),
        host: None,
    }
}

fn apply_request(
    state: &mut HostSnapshot,
    request: HostRequest,
    shutdown: &AtomicBool,
) -> HostResponse {
    if request.token != state.auth_token {
        return failure("HOST_UNAUTHORIZED", "invalid host token");
    }
    match request.action.as_str() {
        "status" => success(state),
        "attach" => {
            let supervisor_id = match request.supervisor_id {
                Some(value) if !value.trim().is_empty() => value,
                _ => return failure("SUPERVISOR_ID_REQUIRED", "attach requires supervisor_id"),
            };
            if state.attached_supervisor_id.as_deref() != Some(supervisor_id.as_str()) {
                state.attachment_generation += 1;
                state.attached_supervisor_id = Some(supervisor_id);
            }
            success(state)
        }
        "detach" => {
            let supervisor_id = match request.supervisor_id {
                Some(value) if !value.trim().is_empty() => value,
                _ => return failure("SUPERVISOR_ID_REQUIRED", "detach requires supervisor_id"),
            };
            if state.attached_supervisor_id.as_deref() != Some(supervisor_id.as_str()) {
                return failure(
                    "SUPERVISOR_ATTACHMENT_MISMATCH",
                    "only the attached Supervisor may detach",
                );
            }
            state.attached_supervisor_id = None;
            success(state)
        }
        "shutdown" => {
            shutdown.store(true, Ordering::SeqCst);
            success(state)
        }
        _ => failure("HOST_ACTION_UNSUPPORTED", "unsupported host action"),
    }
}

fn read_request(stream: &TcpStream) -> Result<HostRequest, Box<HostResponse>> {
    let mut bytes = Vec::new();
    stream
        .take(MAX_REQUEST_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| Box::new(failure("HOST_REQUEST_READ_FAILED", error.to_string())))?;
    if bytes.len() as u64 > MAX_REQUEST_BYTES {
        return Err(Box::new(failure(
            "HOST_REQUEST_TOO_LARGE",
            "request exceeds 16384 bytes",
        )));
    }
    serde_json::from_slice(&bytes)
        .map_err(|error| Box::new(failure("HOST_REQUEST_INVALID", error.to_string())))
}

fn write_response(mut stream: TcpStream, response: &HostResponse) {
    if let Ok(mut body) = serde_json::to_vec(response) {
        body.push(b'\n');
        let _ = stream.write_all(&body);
        let _ = stream.flush();
    }
}

fn handle_connection(
    stream: TcpStream,
    shared: Arc<Mutex<HostSnapshot>>,
    state_file: PathBuf,
    shutdown: Arc<AtomicBool>,
) {
    let request = match read_request(&stream) {
        Ok(request) => request,
        Err(response) => {
            write_response(stream, &response);
            return;
        }
    };
    let mut state = match shared.lock() {
        Ok(state) => state,
        Err(_) => {
            write_response(
                stream,
                &failure("HOST_STATE_POISONED", "host state lock is unavailable"),
            );
            return;
        }
    };
    let before_generation = state.attachment_generation;
    let before_supervisor = state.attached_supervisor_id.clone();
    let response = apply_request(&mut state, request, &shutdown);
    if response.status == "OK"
        && (before_generation != state.attachment_generation
            || before_supervisor != state.attached_supervisor_id)
        && let Err(error) = atomic_write_state(&state_file, &state)
    {
        write_response(stream, &failure("HOST_STATE_WRITE_FAILED", error));
        return;
    }
    write_response(stream, &response);
}

fn serve(config: Config) -> Result<(), String> {
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|error| error.to_string())?;
    listener
        .set_nonblocking(true)
        .map_err(|error| error.to_string())?;
    let address: SocketAddr = listener.local_addr().map_err(|error| error.to_string())?;
    let started_at = now_unix_ms();
    let state = HostSnapshot {
        schema: STATE_SCHEMA,
        host_id: format!("host-{}-{started_at:x}", process::id()),
        anchor_ref: config.anchor_ref,
        endpoint: format!("tcp://{address}"),
        pid: process::id(),
        started_at_unix_ms: started_at,
        attachment_generation: 0,
        attached_supervisor_id: None,
        runtime_state: "LIVE",
        auth_token: config.token,
    };
    atomic_write_state(&config.state_file, &state)?;
    let shared = Arc::new(Mutex::new(state));
    let shutdown = Arc::new(AtomicBool::new(false));
    while !shutdown.load(Ordering::SeqCst) {
        match listener.accept() {
            Ok((stream, _)) => {
                let state = Arc::clone(&shared);
                let state_file = config.state_file.clone();
                let shutdown_flag = Arc::clone(&shutdown);
                thread::spawn(move || handle_connection(stream, state, state_file, shutdown_flag));
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(20));
            }
            Err(error) => return Err(error.to_string()),
        }
    }
    Ok(())
}

fn main() {
    let result = parse_config().and_then(serve);
    if let Err(error) = result {
        eprintln!("{error}");
        process::exit(2);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snapshot() -> HostSnapshot {
        HostSnapshot {
            schema: STATE_SCHEMA,
            host_id: "host-test".to_owned(),
            anchor_ref: "anchor-test".to_owned(),
            endpoint: "tcp://127.0.0.1:1".to_owned(),
            pid: 1,
            started_at_unix_ms: 1,
            attachment_generation: 0,
            attached_supervisor_id: None,
            runtime_state: "LIVE",
            auth_token: "token".to_owned(),
        }
    }

    #[test]
    fn replacement_supervisor_advances_generation_without_replacing_host() {
        let mut state = snapshot();
        let shutdown = AtomicBool::new(false);
        let first = apply_request(
            &mut state,
            HostRequest {
                token: "token".to_owned(),
                action: "attach".to_owned(),
                supervisor_id: Some("supervisor-a".to_owned()),
            },
            &shutdown,
        );
        assert_eq!(first.status, "OK");
        assert_eq!(state.attachment_generation, 1);
        let second = apply_request(
            &mut state,
            HostRequest {
                token: "token".to_owned(),
                action: "attach".to_owned(),
                supervisor_id: Some("supervisor-b".to_owned()),
            },
            &shutdown,
        );
        assert_eq!(second.status, "OK");
        assert_eq!(state.host_id, "host-test");
        assert_eq!(state.attachment_generation, 2);
        assert_eq!(
            state.attached_supervisor_id.as_deref(),
            Some("supervisor-b")
        );
    }

    #[test]
    fn unauthorized_request_cannot_change_attachment() {
        let mut state = snapshot();
        let shutdown = AtomicBool::new(false);
        let response = apply_request(
            &mut state,
            HostRequest {
                token: "wrong".to_owned(),
                action: "attach".to_owned(),
                supervisor_id: Some("supervisor-a".to_owned()),
            },
            &shutdown,
        );
        assert_eq!(response.error_code, Some("HOST_UNAUTHORIZED"));
        assert_eq!(state.attachment_generation, 0);
        assert!(state.attached_supervisor_id.is_none());
    }
}
