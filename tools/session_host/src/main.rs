use base64::Engine;
use portable_pty::{CommandBuilder, MasterPty, NativePtySystem, PtySize, PtySystem};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::env;
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
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
const OUTPUT_CAPACITY_BYTES: usize = 256 * 1024;

#[derive(Debug)]
struct Config {
    state_file: PathBuf,
    anchor_ref: String,
    token: String,
    shell: String,
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
    shell: String,
    child_pid: Option<u32>,
    handle_kinds: [&'static str; 3],
    auth_token: String,
}

#[derive(Debug, Deserialize)]
struct HostRequest {
    token: String,
    action: String,
    supervisor_id: Option<String>,
    input: Option<String>,
    after_cursor: Option<u64>,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    output: Option<OutputChunk>,
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
    shell: String,
    child_pid: Option<u32>,
    handle_kinds: [&'static str; 3],
}

#[derive(Debug, Serialize)]
struct OutputChunk {
    data_base64: String,
    start_cursor: u64,
    next_cursor: u64,
    truncated: bool,
}

#[derive(Debug)]
struct OutputBuffer {
    bytes: VecDeque<u8>,
    start_cursor: u64,
    next_cursor: u64,
}

impl OutputBuffer {
    fn new() -> Self {
        Self {
            bytes: VecDeque::with_capacity(OUTPUT_CAPACITY_BYTES),
            start_cursor: 0,
            next_cursor: 0,
        }
    }

    fn append(&mut self, data: &[u8]) {
        self.bytes.extend(data);
        self.next_cursor = self.next_cursor.saturating_add(data.len() as u64);
        while self.bytes.len() > OUTPUT_CAPACITY_BYTES {
            self.bytes.pop_front();
            self.start_cursor = self.start_cursor.saturating_add(1);
        }
    }

    fn read_after(&self, requested_cursor: u64) -> OutputChunk {
        let actual_cursor = requested_cursor
            .max(self.start_cursor)
            .min(self.next_cursor);
        let offset = (actual_cursor - self.start_cursor) as usize;
        let bytes: Vec<u8> = self.bytes.iter().skip(offset).copied().collect();
        OutputChunk {
            data_base64: base64::engine::general_purpose::STANDARD.encode(bytes),
            start_cursor: actual_cursor,
            next_cursor: self.next_cursor,
            truncated: requested_cursor < self.start_cursor,
        }
    }
}

struct TerminalRuntime {
    _master: Mutex<Box<dyn MasterPty>>,
    writer: Mutex<Box<dyn Write + Send>>,
    _child: Mutex<Box<dyn portable_pty::Child>>,
    output: Arc<Mutex<OutputBuffer>>,
}

impl TerminalRuntime {
    fn spawn(shell: &str) -> Result<(Self, Option<u32>), String> {
        let pair = NativePtySystem::default()
            .openpty(PtySize {
                rows: 30,
                cols: 120,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|error| error.to_string())?;
        let child = pair
            .slave
            .spawn_command(CommandBuilder::new(shell))
            .map_err(|error| error.to_string())?;
        let child_pid = child.process_id();
        let mut reader = pair
            .master
            .try_clone_reader()
            .map_err(|error| error.to_string())?;
        let writer = pair
            .master
            .take_writer()
            .map_err(|error| error.to_string())?;
        drop(pair.slave);
        let output = Arc::new(Mutex::new(OutputBuffer::new()));
        let reader_output = Arc::clone(&output);
        thread::spawn(move || {
            let mut chunk = [0_u8; 8192];
            loop {
                match reader.read(&mut chunk) {
                    Ok(0) | Err(_) => return,
                    Ok(length) => {
                        if let Ok(mut output) = reader_output.lock() {
                            output.append(&chunk[..length]);
                        } else {
                            return;
                        }
                    }
                }
            }
        });
        Ok((
            Self {
                _master: Mutex::new(pair.master),
                writer: Mutex::new(writer),
                _child: Mutex::new(child),
                output,
            },
            child_pid,
        ))
    }

    fn write(&self, input: &str) -> Result<(), String> {
        let mut writer = self
            .writer
            .lock()
            .map_err(|_| "terminal writer lock is unavailable".to_owned())?;
        writer
            .write_all(input.as_bytes())
            .and_then(|_| writer.flush())
            .map_err(|error| error.to_string())
    }

    fn read_after(&self, cursor: u64) -> Result<OutputChunk, String> {
        self.output
            .lock()
            .map(|output| output.read_after(cursor))
            .map_err(|_| "terminal output lock is unavailable".to_owned())
    }
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
            shell: value.shell.clone(),
            child_pid: value.child_pid,
            handle_kinds: value.handle_kinds,
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
        shell: args
            .iter()
            .position(|value| value == "--shell")
            .and_then(|position| args.get(position + 1))
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| "cmd.exe".to_owned()),
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
        output: None,
    }
}

fn failure(code: &'static str, detail: impl Into<String>) -> HostResponse {
    HostResponse {
        schema: RESPONSE_SCHEMA,
        status: "ERROR",
        error_code: Some(code),
        detail: Some(detail.into()),
        host: None,
        output: None,
    }
}

fn terminal_success(state: &HostSnapshot, output: OutputChunk) -> HostResponse {
    HostResponse {
        schema: RESPONSE_SCHEMA,
        status: "OK",
        error_code: None,
        detail: None,
        host: Some(PublicSnapshot::from(state)),
        output: Some(output),
    }
}

fn require_attached_supervisor(
    state: &HostSnapshot,
    request: &HostRequest,
) -> Result<(), Box<HostResponse>> {
    let requested = request
        .supervisor_id
        .as_deref()
        .filter(|value| !value.trim().is_empty());
    if requested.is_none() || requested != state.attached_supervisor_id.as_deref() {
        return Err(Box::new(failure(
            "SUPERVISOR_ATTACHMENT_MISMATCH",
            "terminal I/O requires the currently attached Supervisor",
        )));
    }
    Ok(())
}

fn apply_request(
    state: &mut HostSnapshot,
    request: HostRequest,
    shutdown: &AtomicBool,
    terminal: Option<&TerminalRuntime>,
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
        "write" => {
            if let Err(response) = require_attached_supervisor(state, &request) {
                return *response;
            }
            let Some(input) = request.input.as_deref() else {
                return failure("HOST_INPUT_REQUIRED", "write requires input");
            };
            let Some(terminal) = terminal else {
                return failure("HOST_TERMINAL_UNAVAILABLE", "terminal is unavailable");
            };
            match terminal.write(input) {
                Ok(()) => success(state),
                Err(error) => failure("HOST_INPUT_WRITE_FAILED", error),
            }
        }
        "read" => {
            if let Err(response) = require_attached_supervisor(state, &request) {
                return *response;
            }
            let Some(terminal) = terminal else {
                return failure("HOST_TERMINAL_UNAVAILABLE", "terminal is unavailable");
            };
            match terminal.read_after(request.after_cursor.unwrap_or(0)) {
                Ok(output) => terminal_success(state, output),
                Err(error) => failure("HOST_OUTPUT_READ_FAILED", error),
            }
        }
        _ => failure("HOST_ACTION_UNSUPPORTED", "unsupported host action"),
    }
}

fn read_request(stream: &TcpStream) -> Result<HostRequest, Box<HostResponse>> {
    let mut bytes = Vec::new();
    BufReader::new(stream)
        .take(MAX_REQUEST_BYTES + 1)
        .read_until(b'\n', &mut bytes)
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
    terminal: Arc<TerminalRuntime>,
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
    let response = apply_request(&mut state, request, &shutdown, Some(&terminal));
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
    let (terminal, child_pid) = TerminalRuntime::spawn(&config.shell)?;
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
        shell: config.shell,
        child_pid,
        handle_kinds: ["CONPTY", "INPUT_WRITER", "OUTPUT_READER"],
        auth_token: config.token,
    };
    atomic_write_state(&config.state_file, &state)?;
    let shared = Arc::new(Mutex::new(state));
    let terminal = Arc::new(terminal);
    let shutdown = Arc::new(AtomicBool::new(false));
    while !shutdown.load(Ordering::SeqCst) {
        match listener.accept() {
            Ok((stream, _)) => {
                let state = Arc::clone(&shared);
                let terminal = Arc::clone(&terminal);
                let state_file = config.state_file.clone();
                let shutdown_flag = Arc::clone(&shutdown);
                thread::spawn(move || {
                    handle_connection(stream, state, terminal, state_file, shutdown_flag)
                });
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
            shell: "cmd.exe".to_owned(),
            child_pid: Some(2),
            handle_kinds: ["CONPTY", "INPUT_WRITER", "OUTPUT_READER"],
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
                input: None,
                after_cursor: None,
            },
            &shutdown,
            None,
        );
        assert_eq!(first.status, "OK");
        assert_eq!(state.attachment_generation, 1);
        let second = apply_request(
            &mut state,
            HostRequest {
                token: "token".to_owned(),
                action: "attach".to_owned(),
                supervisor_id: Some("supervisor-b".to_owned()),
                input: None,
                after_cursor: None,
            },
            &shutdown,
            None,
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
                input: None,
                after_cursor: None,
            },
            &shutdown,
            None,
        );
        assert_eq!(response.error_code, Some("HOST_UNAUTHORIZED"));
        assert_eq!(state.attachment_generation, 0);
        assert!(state.attached_supervisor_id.is_none());
    }

    #[test]
    fn output_buffer_reports_truncation_and_monotonic_cursor() {
        let mut output = OutputBuffer::new();
        output.append(&vec![b'a'; OUTPUT_CAPACITY_BYTES + 4]);
        let chunk = output.read_after(0);
        assert!(chunk.truncated);
        assert_eq!(chunk.start_cursor, 4);
        assert_eq!(chunk.next_cursor, (OUTPUT_CAPACITY_BYTES + 4) as u64);
        assert_eq!(
            base64::engine::general_purpose::STANDARD
                .decode(chunk.data_base64)
                .expect("valid base64")
                .len(),
            OUTPUT_CAPACITY_BYTES
        );
    }
}
