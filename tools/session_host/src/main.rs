mod turn_delivery;
mod native_queue;
mod node_projection;
use base64::Engine;
use portable_pty::{CommandBuilder, MasterPty, NativePtySystem, PtySize, PtySystem};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet, VecDeque};
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
const MAX_REQUEST_BYTES: u64 = 64 * 1024;
const OUTPUT_CAPACITY_BYTES: usize = 256 * 1024;
const CHANNEL_QUEUE_CAPACITY: usize = 64;
const STATE_WRITE_RETRIES: usize = 40;
const STATE_WRITE_RETRY_DELAY_MS: u64 = 10;

#[derive(Debug)]
struct Config {
    state_file: PathBuf,
    anchor_ref: String,
    token: String,
    shell: String,
    cwd: Option<PathBuf>,
    shell_args: Vec<String>,
    host_kind: String,
    owner_ref: String,
    server_version: String,
    supervisor_version: String,
    host_version: String,
    pty_version: String,
    channel_lookup_file: Option<PathBuf>,
    channel_bootstrap_token: Option<String>,
    channel_session_token: Option<String>,
    environment: Vec<(String, String)>,
    cols: u16,
    rows: u16,
}

#[derive(Clone, Debug, Serialize)]
struct HostSnapshot {
    schema: &'static str,
    host_id: String,
    anchor_ref: String,
    host_kind: String,
    owner_ref: String,
    server_version: String,
    supervisor_version: String,
    host_version: String,
    pty_version: String,
    endpoint: String,
    pid: u32,
    started_at_unix_ms: u128,
    attachment_generation: u64,
    session_binding_generation: u64,
    provider: Option<String>,
    provider_session_ref: Option<String>,
    mode: Option<String>,
    attached_supervisor_id: Option<String>,
    runtime_state: String,
    protocol_state: String,
    protocol_owner_id: Option<String>,
    shell: String,
    cwd: Option<String>,
    child_pid: Option<u32>,
    child_started_at_unix_ms: Option<u128>,
    child_exit_code: Option<u32>,
    handle_kinds: [&'static str; 3],
    channel_enabled: bool,
    channel_registered: bool,
    auth_token: String,
    #[serde(serialize_with = "turn_delivery::serialize_recent")]
    turn_delivery: turn_delivery::TurnDelivery,
    node_projection: node_projection::NodeProjection,
}

#[derive(Debug, Deserialize)]
struct HostRequest {
    token: String,
    action: String,
    supervisor_id: Option<String>,
    input: Option<String>,
    input_base64: Option<String>,
    after_cursor: Option<u64>,
    cols: Option<u16>,
    rows: Option<u16>,
    channel: Option<Value>,
    provider: Option<String>,
    provider_session_ref: Option<String>,
    mode: Option<String>,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    channel: Option<Value>,
}

#[derive(Debug, Serialize)]
struct PublicSnapshot {
    schema: &'static str,
    host_id: String,
    anchor_ref: String,
    host_kind: String,
    owner_ref: String,
    server_version: String,
    supervisor_version: String,
    host_version: String,
    pty_version: String,
    endpoint: String,
    pid: u32,
    started_at_unix_ms: u128,
    attachment_generation: u64,
    session_binding_generation: u64,
    provider: Option<String>,
    provider_session_ref: Option<String>,
    mode: Option<String>,
    attached_supervisor_id: Option<String>,
    runtime_state: String,
    protocol_state: String,
    shell: String,
    cwd: Option<String>,
    child_pid: Option<u32>,
    child_started_at_unix_ms: Option<u128>,
    child_exit_code: Option<u32>,
    handle_kinds: [&'static str; 3],
    channel_enabled: bool,
    channel_registered: bool,
    turn_delivery: turn_delivery::TurnDelivery,
    node_projection: node_projection::NodeProjection,
}

#[derive(Debug, Serialize)]
struct OutputChunk {
    data_base64: String,
    start_cursor: u64,
    next_cursor: u64,
    truncated: bool,
}

#[derive(Debug)]
struct MessageChannel {
    bootstrap_token: String,
    session_token: String,
    registered: bool,
    queue: VecDeque<Value>,
    seen: HashSet<String>,
    anchors: HashMap<String, String>,
    results: HashMap<String, Value>,
}

impl MessageChannel {
    fn new(bootstrap_token: String, session_token: String) -> Self {
        Self {
            bootstrap_token,
            session_token,
            registered: false,
            queue: VecDeque::new(),
            seen: HashSet::new(),
            anchors: HashMap::new(),
            results: HashMap::new(),
        }
    }

    fn exchange(&mut self, token: &str) -> Result<Value, (&'static str, String)> {
        if self.registered {
            return Err((
                "CHANNEL_BOOTSTRAP_ALREADY_USED",
                "channel bootstrap was already exchanged".to_owned(),
            ));
        }
        if token != self.bootstrap_token {
            return Err((
                "CHANNEL_BOOTSTRAP_INVALID",
                "channel bootstrap token is invalid".to_owned(),
            ));
        }
        self.registered = true;
        self.bootstrap_token.clear();
        Ok(json!({"status": "REGISTERED", "session_token": self.session_token, "ack_protocol": 1}))
    }

    fn require_session(&self, token: &str) -> Result<(), (&'static str, String)> {
        if !self.registered || token != self.session_token {
            return Err((
                "CHANNEL_SESSION_UNAUTHORIZED",
                "channel session token is invalid".to_owned(),
            ));
        }
        Ok(())
    }

    fn push(&mut self, payload: &Value) -> Result<Value, (&'static str, String)> {
        if !self.registered {
            return Err((
                "CHANNEL_NOT_REGISTERED",
                "message channel is not registered".to_owned(),
            ));
        }
        let message_id = payload
            .get("message_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        let content = payload.get("content").and_then(Value::as_str).unwrap_or("");
        let anchor = payload
            .get("session_anchor_ref")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if message_id.is_empty() || content.trim().is_empty() || anchor.is_empty() {
            return Err((
                "CHANNEL_PAYLOAD_INVALID",
                "message_id, content, and session_anchor_ref are required".to_owned(),
            ));
        }
        if self.seen.contains(message_id) {
            return Ok(json!({"status": "DUPLICATE", "message_id": message_id}));
        }
        if self.queue.len() >= CHANNEL_QUEUE_CAPACITY {
            return Err((
                "CHANNEL_QUEUE_FULL",
                "message channel queue is full".to_owned(),
            ));
        }
        let meta = payload.get("meta").cloned().unwrap_or_else(|| json!({}));
        self.seen.insert(message_id.to_owned());
        self.anchors
            .insert(message_id.to_owned(), anchor.to_owned());
        self.queue.push_back(json!({
            "schema": "universe.host-message-channel.v1",
            "message_id": message_id,
            "content": content,
            "meta": meta,
        }));
        Ok(json!({"status": "QUEUED", "message_id": message_id}))
    }

    fn poll(&mut self, token: &str) -> Result<Value, (&'static str, String)> {
        self.require_session(token)?;
        match self.queue.pop_front() {
            Some(event) => Ok(json!({"status": "EVENT", "event": event})),
            None => Ok(json!({"status": "EMPTY"})),
        }
    }

    fn submit_result(
        &mut self,
        token: &str,
        payload: &Value,
    ) -> Result<Value, (&'static str, String)> {
        self.require_session(token)?;
        let message_id = payload
            .get("message_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        let body = payload
            .get("body_text")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        let Some(anchor) = self.anchors.get(message_id).cloned() else {
            return Err((
                "CHANNEL_RESULT_MESSAGE_UNKNOWN",
                "channel result message is unknown".to_owned(),
            ));
        };
        if body.is_empty() {
            return Err((
                "CHANNEL_RESULT_INVALID",
                "channel result body_text is required".to_owned(),
            ));
        }
        let kind = payload.get("kind").and_then(Value::as_str).unwrap_or("RESULT");
        let phase = payload.get("phase").and_then(Value::as_str).unwrap_or("");
        if !matches!(kind, "ACK" | "RESULT") || (kind == "ACK" && !matches!(phase, "RECEIVED" | "STARTED")) {
            return Err(("CHANNEL_RESULT_INVALID", "invalid result kind or ACK phase".to_owned()));
        }
        let outcome = payload.get("outcome").and_then(Value::as_str).unwrap_or("COMPLETED");
        if kind == "RESULT" && !matches!(outcome, "COMPLETED" | "FAILED") {
            return Err(("CHANNEL_RESULT_INVALID", "invalid final outcome".to_owned()));
        }
        let result = json!({
            "status": if kind == "ACK" { "ACKNOWLEDGED" } else { "ACCEPTED" },
            "kind": kind,
            "phase": phase,
            "message_id": message_id,
            "body_text": body,
            "outcome": outcome,
            "result_ref": payload.get("result_ref").and_then(Value::as_str).unwrap_or(""),
            "session_anchor_ref": anchor,
        });
        if let Some(existing) = self.results.get(message_id) {
            if existing == &result {
                return Ok(json!({"status": "DUPLICATE", "message_id": message_id}));
            }
            if existing.get("kind").and_then(Value::as_str) != Some("ACK") {
                return Err((
                "CHANNEL_RESULT_CONFLICT",
                "channel result conflicts with the stored result".to_owned(),
                ));
            }
            if kind == "ACK" && existing.get("phase").and_then(Value::as_str) == Some("STARTED") && phase == "RECEIVED" {
                return Ok(json!({"status": "DUPLICATE", "message_id": message_id}));
            }
        }
        self.results.insert(message_id.to_owned(), result.clone());
        Ok(result)
    }

    fn result(&self, message_id: &str) -> Value {
        self.results
            .get(message_id)
            .cloned()
            .unwrap_or_else(|| json!({"status": "EMPTY", "message_id": message_id}))
    }
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
    master: Mutex<Box<dyn MasterPty>>,
    writer: Mutex<Box<dyn Write + Send>>,
    child: Mutex<Box<dyn portable_pty::Child>>,
    output: Arc<Mutex<OutputBuffer>>,
    native_queue: Option<native_queue::NativeQueue>,
}

impl TerminalRuntime {
    fn spawn(config: &Config, host_id: &str) -> Result<(Self, Option<u32>, Option<u128>), String> {
        let pair = NativePtySystem::default()
            .openpty(PtySize {
                rows: config.rows,
                cols: config.cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|error| error.to_string())?;
        let mut command = CommandBuilder::new(&config.shell);
        command.args(&config.shell_args);
        if let Some(cwd) = config.cwd.as_deref() {
            command.cwd(cwd);
        }
        for (name, value) in &config.environment {
            command.env(name, value);
        }
        command.env("UNIVERSE_SESSION_HOST_ID", host_id);
        let child = pair
            .slave
            .spawn_command(command)
            .map_err(|error| error.to_string())?;
        let child_pid = child.process_id();
        // Wall time immediately after spawn; the Supervisor corroborates
        // this against the OS process start time (with tolerance) to catch
        // PID reuse in reconnection_host.verify_child_liveness.
        let child_started_at_unix_ms = child_pid.map(|_| now_unix_ms());
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
                master: Mutex::new(pair.master),
                writer: Mutex::new(writer),
                child: Mutex::new(child),
                output,
                native_queue: config.environment.iter().find(|(k,_)| k=="UNIVERSE_CODEX_QUEUE_EXECUTABLE")
                    .map(|(_,v)| native_queue::NativeQueue { executable: PathBuf::from(v), cwd: config.cwd.clone(), environment: config.environment.clone() }),
            },
            child_pid,
            child_started_at_unix_ms,
        ))
    }

    fn terminate(&self) -> Result<(), String> {
        self.child
            .lock()
            .map_err(|_| "terminal child lock is unavailable".to_owned())?
            .kill()
            .map_err(|error| error.to_string())
    }

    fn write(&self, input: &[u8]) -> Result<(), String> {
        let mut writer = self
            .writer
            .lock()
            .map_err(|_| "terminal writer lock is unavailable".to_owned())?;
        writer
            .write_all(input)
            .and_then(|_| writer.flush())
            .map_err(|error| error.to_string())
    }

    fn deliver_turn(&self, delivery: &mut turn_delivery::TurnDelivery) {
        let baseline = self.output.lock().map(|o| o.next_cursor).unwrap_or(0);
        delivery.drain_with_settle(|bytes| self.write(bytes), || {
            let start = std::time::Instant::now();
            let mut gate = turn_delivery::InputSettle::new(baseline);
            loop {
                let elapsed = start.elapsed().as_millis() as u64;
                let cursor = match self.output.lock() {
                    Ok(output) => output.next_cursor,
                    Err(_) => return Err(json!({"reason":"OUTPUT_LOCK_FAILED"})),
                };
                if let Some(result) = gate.observe(cursor, elapsed) { return result; }
                std::thread::sleep(std::time::Duration::from_millis(25));
            }
        });
    }

    fn read_after(&self, cursor: u64) -> Result<OutputChunk, String> {
        self.output
            .lock()
            .map(|output| output.read_after(cursor))
            .map_err(|_| "terminal output lock is unavailable".to_owned())
    }

    fn resize(&self, cols: u16, rows: u16) -> Result<(), String> {
        if cols == 0 || rows == 0 {
            return Err("terminal size must be positive".to_owned());
        }
        self.master
            .lock()
            .map_err(|_| "terminal master lock is unavailable".to_owned())?
            .resize(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|error| error.to_string())
    }

    fn child_status(&self) -> Result<Option<u32>, String> {
        self.child
            .lock()
            .map_err(|_| "terminal child lock is unavailable".to_owned())?
            .try_wait()
            .map(|status| status.map(|value| value.exit_code()))
            .map_err(|error| error.to_string())
    }
}

impl From<&HostSnapshot> for PublicSnapshot {
    fn from(value: &HostSnapshot) -> Self {
        Self {
            schema: value.schema,
            host_id: value.host_id.clone(),
            anchor_ref: value.anchor_ref.clone(),
            host_kind: value.host_kind.clone(),
            owner_ref: value.owner_ref.clone(),
            server_version: value.server_version.clone(),
            supervisor_version: value.supervisor_version.clone(),
            host_version: value.host_version.clone(),
            pty_version: value.pty_version.clone(),
            endpoint: value.endpoint.clone(),
            pid: value.pid,
            started_at_unix_ms: value.started_at_unix_ms,
            attachment_generation: value.attachment_generation,
            session_binding_generation: value.session_binding_generation,
            provider: value.provider.clone(),
            provider_session_ref: value.provider_session_ref.clone(),
            mode: value.mode.clone(),
            attached_supervisor_id: value.attached_supervisor_id.clone(),
            runtime_state: value.runtime_state.clone(),
            protocol_state: value.protocol_state.clone(),
            shell: value.shell.clone(),
            cwd: value.cwd.clone(),
            child_pid: value.child_pid,
            child_started_at_unix_ms: value.child_started_at_unix_ms,
            child_exit_code: value.child_exit_code,
            handle_kinds: value.handle_kinds,
            channel_enabled: value.channel_enabled,
            channel_registered: value.channel_registered,
            turn_delivery: value.turn_delivery.clone(),
            node_projection: value.node_projection.clone(),
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

fn optional_arg(args: &[String], name: &str) -> Option<String> {
    args.iter()
        .position(|value| value == name)
        .and_then(|position| args.get(position + 1))
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn repeated_args(args: &[String], name: &str) -> Result<Vec<String>, String> {
    let mut values = Vec::new();
    let mut index = 0;
    while index < args.len() {
        if args[index] == name {
            let value = args
                .get(index + 1)
                .ok_or_else(|| format!("{name} requires a value"))?;
            values.push(value.clone());
            index += 2;
        } else {
            index += 1;
        }
    }
    Ok(values)
}

fn terminal_dimension(args: &[String], name: &str, default: u16) -> Result<u16, String> {
    match optional_arg(args, name) {
        None => Ok(default),
        Some(value) => value
            .parse::<u16>()
            .ok()
            .filter(|value| *value > 0)
            .ok_or_else(|| format!("{name} must be an integer between 1 and 65535")),
    }
}

fn environment_overlays(args: &[String]) -> Result<Vec<(String, String)>, String> {
    repeated_args(args, "--env")?
        .into_iter()
        .map(|entry| {
            let (name, value) = entry
                .split_once('=')
                .ok_or_else(|| "--env requires NAME=VALUE".to_owned())?;
            if name.trim().is_empty() {
                return Err("--env name must not be empty".to_owned());
            }
            Ok((name.to_owned(), value.to_owned()))
        })
        .collect()
}

fn parse_config() -> Result<Config, String> {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.first().map(String::as_str) != Some("serve") {
        return Err(
            "usage: universe-session-host serve --state-file PATH --anchor-ref REF --host-kind SESSION --owner-ref REF --server-version VERSION --supervisor-version VERSION --host-version VERSION --pty-version VERSION --token TOKEN [--shell PATH] [--cwd PATH] [--shell-arg VALUE] [--env NAME=VALUE] [--cols N] [--rows N]"
                .to_owned(),
        );
    }
    let cwd = optional_arg(&args, "--cwd").map(PathBuf::from);
    if let Some(path) = cwd.as_deref()
        && !path.is_dir()
    {
        return Err("--cwd must name an existing directory".to_owned());
    }
    let host_kind = required_arg(&args, "--host-kind")?.to_ascii_uppercase();
    if host_kind != "SESSION" {
        return Err("--host-kind currently supports SESSION only".to_owned());
    }
    let channel_lookup_file = optional_arg(&args, "--channel-lookup-file").map(PathBuf::from);
    let channel_bootstrap_token = optional_arg(&args, "--channel-bootstrap-token");
    let channel_session_token = optional_arg(&args, "--channel-session-token");
    let channel_parts = [
        channel_lookup_file.is_some(),
        channel_bootstrap_token.is_some(),
        channel_session_token.is_some(),
    ];
    if channel_parts.iter().any(|value| *value) && !channel_parts.iter().all(|value| *value) {
        return Err(
            "channel lookup, bootstrap token, and session token must be supplied together"
                .to_owned(),
        );
    }
    Ok(Config {
        state_file: PathBuf::from(required_arg(&args, "--state-file")?),
        anchor_ref: required_arg(&args, "--anchor-ref")?,
        token: required_arg(&args, "--token")?,
        shell: optional_arg(&args, "--shell").unwrap_or_else(|| "cmd.exe".to_owned()),
        cwd,
        shell_args: repeated_args(&args, "--shell-arg")?,
        host_kind,
        owner_ref: required_arg(&args, "--owner-ref")?,
        server_version: required_arg(&args, "--server-version")?,
        supervisor_version: required_arg(&args, "--supervisor-version")?,
        host_version: required_arg(&args, "--host-version")?,
        pty_version: required_arg(&args, "--pty-version")?,
        channel_lookup_file,
        channel_bootstrap_token,
        channel_session_token,
        environment: environment_overlays(&args)?,
        cols: terminal_dimension(&args, "--cols", 120)?,
        rows: terminal_dimension(&args, "--rows", 30)?,
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
    let mut last_error = None;
    // The Python Supervisor periodically reads this record while reconciling
    // Host-owned sessions. Windows can briefly reject remove/rename while
    // that read handle is open; preserve the receipt ledger instead of
    // converting a transient sharing violation into NATIVE_UNCONFIRMED.
    for attempt in 0..STATE_WRITE_RETRIES {
        if let Err(error) = fs::write(&temporary, &bytes) {
            if retryable_state_write_error(&error) && attempt + 1 < STATE_WRITE_RETRIES {
                thread::sleep(Duration::from_millis(STATE_WRITE_RETRY_DELAY_MS));
                continue;
            }
            return Err(error.to_string());
        }
        if path.exists() {
            if let Err(error) = fs::remove_file(path) {
                let retry = retryable_state_write_error(&error);
                if retry && attempt + 1 < STATE_WRITE_RETRIES {
                    last_error = Some(error);
                    thread::sleep(Duration::from_millis(STATE_WRITE_RETRY_DELAY_MS));
                    continue;
                }
                return Err(error.to_string());
            }
        }
        match fs::rename(&temporary, path) {
            Ok(()) => return Ok(()),
            Err(error) if retryable_state_write_error(&error) && attempt + 1 < STATE_WRITE_RETRIES => {
                last_error = Some(error);
                thread::sleep(Duration::from_millis(STATE_WRITE_RETRY_DELAY_MS));
            }
            Err(error) => return Err(error.to_string()),
        }
    }
    Err(last_error
        .map(|error| error.to_string())
        .unwrap_or_else(|| "state write retries exhausted".to_owned()))
}

fn retryable_state_write_error(error: &std::io::Error) -> bool {
    matches!(
        error.kind(),
        std::io::ErrorKind::PermissionDenied | std::io::ErrorKind::WouldBlock
    )
}

fn success(state: &HostSnapshot) -> HostResponse {
    HostResponse {
        schema: RESPONSE_SCHEMA,
        status: "OK",
        error_code: None,
        detail: None,
        host: Some(PublicSnapshot::from(state)),
        output: None,
        channel: None,
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
        channel: None,
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
        channel: None,
    }
}

fn channel_success(state: &HostSnapshot, channel: Value) -> HostResponse {
    HostResponse {
        schema: RESPONSE_SCHEMA,
        status: "OK",
        error_code: None,
        detail: None,
        host: Some(PublicSnapshot::from(state)),
        output: None,
        channel: Some(channel),
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

fn refresh_runtime_state(
    state: &mut HostSnapshot,
    terminal: &TerminalRuntime,
) -> Result<(), String> {
    match terminal.child_status()? {
        Some(exit_code) => {
            state.runtime_state = "EXITED".to_owned();
            state.child_exit_code = Some(exit_code);
        }
        None => {
            state.runtime_state = "LIVE".to_owned();
            state.child_exit_code = None;
        }
    }
    Ok(())
}

fn apply_request(
    state: &mut HostSnapshot,
    request: HostRequest,
    shutdown: &AtomicBool,
    terminal: Option<&TerminalRuntime>,
    channel: Option<&Mutex<MessageChannel>>,
) -> HostResponse {
    if request.action == "channel_exchange" {
        let Some(channel) = channel else {
            return failure("CHANNEL_UNAVAILABLE", "message channel is unavailable");
        };
        let mut channel = match channel.lock() {
            Ok(value) => value,
            Err(_) => {
                return failure(
                    "CHANNEL_STATE_POISONED",
                    "message channel lock is unavailable",
                );
            }
        };
        return match channel.exchange(&request.token) {
            Ok(value) => {
                state.channel_registered = channel.registered;
                channel_success(state, value)
            }
            Err((code, detail)) => failure(code, detail),
        };
    }
    if matches!(request.action.as_str(), "channel_poll" | "channel_result") {
        let Some(channel) = channel else {
            return failure("CHANNEL_UNAVAILABLE", "message channel is unavailable");
        };
        let mut channel = match channel.lock() {
            Ok(value) => value,
            Err(_) => {
                return failure(
                    "CHANNEL_STATE_POISONED",
                    "message channel lock is unavailable",
                );
            }
        };
        let payload = request.channel.as_ref().unwrap_or(&Value::Null);
        let result = if request.action == "channel_poll" {
            channel.poll(&request.token)
        } else {
            channel.submit_result(&request.token, payload)
        };
        return match result {
            Ok(value) => channel_success(state, value),
            Err((code, detail)) => failure(code, detail),
        };
    }
    if request.token != state.auth_token {
        return failure("HOST_UNAUTHORIZED", "invalid host token");
    }
    match request.action.as_str() {
        "turn_observe" => {
            let payload=request.channel.unwrap_or_else(||json!({}));
            if state.provider.as_deref()!=payload["provider"].as_str()
                || state.provider_session_ref.as_deref()!=payload["provider_session_ref"].as_str() {
                return failure("HOST_TURN_BINDING_MISMATCH", "hook must match the bound provider session");
            }
            if let Err((code,detail))=state.turn_delivery.observe(&payload) { return failure(code,detail); }
            if let Some(terminal)=terminal { terminal.deliver_turn(&mut state.turn_delivery); }
            success(state)
        }
        "turn_offer" | "turn_delivery_status" => {
            if let Err(response)=require_attached_supervisor(state,&request) { return *response; }
            let payload=request.channel.unwrap_or_else(||json!({}));
            if request.action=="turn_offer" {
                // A fresh, typed Persona Worker has an authenticated Host and
                // exact Session Anchor before a provider-native session ref
                // exists.  The Universe server may authorize this narrow
                // Session Bus reply path after validating the persisted run,
                // assignment revision, and message lineage.  Keep the normal
                // provider-session binding requirement for every other turn.
                let explicit_session_bus_reply =
                    payload["delivery_mode"].as_str() == Some("EXPLICIT_SESSION_BUS_REPLY")
                    && payload["awaits_authoritative_reply"].as_bool() == Some(true)
                    && payload["provider_session_ref"].as_str().unwrap_or("").is_empty()
                    && payload["session_anchor_ref"].as_str() == Some(state.anchor_ref.as_str())
                    && state.provider.as_deref() == payload["provider"].as_str();
                if state.provider.as_deref()!=payload["provider"].as_str()
                    || (!explicit_session_bus_reply
                        && state.provider_session_ref.as_deref()!=payload["provider_session_ref"].as_str()) {
                    return failure("HOST_TURN_BINDING_MISMATCH", "delivery must match the bound provider session");
                }
                let mut payload=payload;
                payload["_transport"]=json!(if state.provider.as_deref()==Some("CODEX") { "CODEX_NATIVE_QUEUE" } else { "PTY_INPUT" });
                if state.provider.as_deref()==Some("CODEX") && terminal.and_then(|t|t.native_queue.as_ref()).is_none() {
                    return failure("HOST_NATIVE_QUEUE_UNAVAILABLE", "launch a Host with the bound native Codex executable; PTY fallback is disabled");
                }
                if let Err((code,detail))=state.turn_delivery.offer(&payload) { return failure(code,detail); }
                if let Some(terminal)=terminal { terminal.deliver_turn(&mut state.turn_delivery); }
            }
            channel_success(state,serde_json::to_value(&state.turn_delivery).unwrap())
        }
        "node_projection" => {
            // A Supervisor push, exactly like turn_offer: only the
            // currently attached Supervisor may write it. Read-only
            // observation of the current projection is already covered by
            // "status" (PublicSnapshot always includes node_projection).
            if let Err(response) = require_attached_supervisor(state, &request) {
                return *response;
            }
            let payload = request.channel.unwrap_or_else(|| json!({}));
            match state
                .node_projection
                .apply(&payload, &state.anchor_ref.clone(), now_unix_ms() as u64)
            {
                Ok(outcome) => {
                    // apply()'s Ok(..) is the only place the ACCEPTED vs
                    // REPLAYED distinction exists -- re-serializing just
                    // state.node_projection (as offer_turn does for
                    // turn_delivery) silently drops it, since the struct
                    // itself carries no "status" field. Overlay it onto the
                    // full projection snapshot rather than sending outcome
                    // alone, so callers keep seeing revision/received_at_ms
                    // too (2026-09-15: caught by a real IPC test expecting
                    // "status" on the wire, not just in the Rust return type).
                    let mut snapshot = serde_json::to_value(&state.node_projection).unwrap();
                    if let (Some(object), Some(status)) =
                        (snapshot.as_object_mut(), outcome.get("status"))
                    {
                        object.insert("status".to_string(), status.clone());
                    }
                    channel_success(state, snapshot)
                }
                Err((code, detail)) => failure(code, detail),
            }
        }
        "status" => success(state),
        "bind_provider_session" => {
            let provider = request.provider.unwrap_or_default().trim().to_ascii_uppercase();
            let session_ref = request.provider_session_ref.unwrap_or_default().trim().to_owned();
            if !matches!(provider.as_str(), "CLAUDE" | "CODEX" | "GROK") || session_ref.is_empty() {
                return failure("HOST_PROVIDER_SESSION_BINDING_INVALID", "provider and provider_session_ref are required");
            }
            let same = state.provider.as_deref() == Some(provider.as_str())
                && state.provider_session_ref.as_deref() == Some(session_ref.as_str());
            if state.provider_session_ref.is_some() && !same {
                return failure("HOST_PROVIDER_SESSION_BINDING_CONFLICT", "a Rust Host cannot be rebound to a different provider session");
            }
            if !same {
                state.provider = Some(provider);
                state.provider_session_ref = Some(session_ref);
                state.session_binding_generation += 1;
            }
            success(state)
        }
        "bind_mode" => {
            let mode = request.mode.unwrap_or_default().trim().to_ascii_uppercase();
            if mode.is_empty() {
                return failure("HOST_MODE_BINDING_INVALID", "mode is required");
            }
            if state.mode.as_deref().is_some_and(|current| current != mode) {
                return failure("HOST_MODE_BINDING_CONFLICT", "a Rust Host cannot be rebound to a different mode");
            }
            if state.mode.as_deref() != Some(mode.as_str()) {
                state.mode = Some(mode);
                state.session_binding_generation += 1;
            }
            success(state)
        }
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
        "protocol_initialize_begin" => {
            if let Err(response) = require_attached_supervisor(state, &request) {
                return *response;
            }
            let supervisor_id = request.supervisor_id.unwrap_or_default();
            match state.protocol_state.as_str() {
                "NEW" | "FAILED" => {
                    state.protocol_state = "INITIALIZING".to_owned();
                    state.protocol_owner_id = Some(supervisor_id);
                    success(state)
                }
                "INITIALIZED" => success(state),
                "INITIALIZING"
                    if state.protocol_owner_id.as_deref() == Some(supervisor_id.as_str()) =>
                {
                    success(state)
                }
                "INITIALIZING" => failure(
                    "HOST_PROTOCOL_INITIALIZATION_OWNED",
                    "another Supervisor owns provider initialization",
                ),
                _ => failure(
                    "HOST_PROTOCOL_STATE_INVALID",
                    "provider protocol state cannot begin initialization",
                ),
            }
        }
        "protocol_initialize_complete" => {
            if let Err(response) = require_attached_supervisor(state, &request) {
                return *response;
            }
            let supervisor_id = request.supervisor_id.unwrap_or_default();
            if state.protocol_state != "INITIALIZING"
                || state.protocol_owner_id.as_deref() != Some(supervisor_id.as_str())
            {
                return failure(
                    "HOST_PROTOCOL_INITIALIZATION_MISMATCH",
                    "only the initialization owner may complete provider initialization",
                );
            }
            state.protocol_state = "INITIALIZED".to_owned();
            state.protocol_owner_id = None;
            success(state)
        }
        "protocol_initialize_failed" => {
            if let Err(response) = require_attached_supervisor(state, &request) {
                return *response;
            }
            let supervisor_id = request.supervisor_id.unwrap_or_default();
            if state.protocol_state != "INITIALIZING"
                || state.protocol_owner_id.as_deref() != Some(supervisor_id.as_str())
            {
                return failure(
                    "HOST_PROTOCOL_INITIALIZATION_MISMATCH",
                    "only the initialization owner may fail provider initialization",
                );
            }
            state.protocol_state = "FAILED".to_owned();
            state.protocol_owner_id = None;
            success(state)
        }
        "shutdown" => {
            if state.runtime_state == "LIVE"
                && let Some(terminal) = terminal
                && let Err(error) = terminal.terminate()
            {
                return failure("HOST_CHILD_TERMINATE_FAILED", error);
            }
            state.runtime_state = "TERMINATED".to_owned();
            state.attached_supervisor_id = None;
            shutdown.store(true, Ordering::SeqCst);
            success(state)
        }
        "write" | "execute" => {
            if let Err(response) = require_attached_supervisor(state, &request) {
                return *response;
            }
            let action = request.action.clone();
            let input = match (request.input_base64.as_deref(), request.input.as_deref()) {
                (Some(value), _) => match base64::engine::general_purpose::STANDARD.decode(value) {
                    Ok(value) => value,
                    Err(error) => return failure("HOST_INPUT_INVALID", error.to_string()),
                },
                (None, Some(value)) => value.as_bytes().to_vec(),
                (None, None) => {
                    return failure(
                        "HOST_INPUT_REQUIRED",
                        format!("{action} requires input_base64 or input"),
                    );
                }
            };
            let Some(terminal) = terminal else {
                return failure("HOST_TERMINAL_UNAVAILABLE", "terminal is unavailable");
            };
            // Focus notifications and terminal cursor reports do not create a user draft.
            let control_only = input == b"\x1b[I" || input == b"\x1b[O"
                || (input.starts_with(b"\x1b[") && input.ends_with(b"R")
                    && input[2..input.len()-1].iter().all(|b|b.is_ascii_digit() || *b==b';'));
            if !control_only { state.turn_delivery.user_input(); }
            match terminal.write(&input) {
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
        "resize" => {
            if let Err(response) = require_attached_supervisor(state, &request) {
                return *response;
            }
            let (Some(cols), Some(rows)) = (request.cols, request.rows) else {
                return failure("HOST_SIZE_REQUIRED", "resize requires cols and rows");
            };
            let Some(terminal) = terminal else {
                return failure("HOST_TERMINAL_UNAVAILABLE", "terminal is unavailable");
            };
            match terminal.resize(cols, rows) {
                Ok(()) => success(state),
                Err(error) => failure("HOST_RESIZE_FAILED", error),
            }
        }
        "channel_state" | "channel_push" | "channel_result_get" => {
            if let Err(response) = require_attached_supervisor(state, &request) {
                return *response;
            }
            let Some(channel) = channel else {
                return failure("CHANNEL_UNAVAILABLE", "message channel is unavailable");
            };
            let mut channel = match channel.lock() {
                Ok(value) => value,
                Err(_) => {
                    return failure(
                        "CHANNEL_STATE_POISONED",
                        "message channel lock is unavailable",
                    );
                }
            };
            let payload = request.channel.as_ref().unwrap_or(&Value::Null);
            let result = match request.action.as_str() {
                "channel_state" => {
                    Ok(json!({"status": if channel.registered { "READY" } else { "PENDING" }}))
                }
                "channel_push" => channel.push(payload),
                _ => {
                    let message_id = payload
                        .get("message_id")
                        .and_then(Value::as_str)
                        .unwrap_or("");
                    Ok(channel.result(message_id))
                }
            };
            match result {
                Ok(value) => channel_success(state, value),
                Err((code, detail)) => failure(code, detail),
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
    channel: Option<Arc<Mutex<MessageChannel>>>,
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
    let before_turn_revision = state.turn_delivery.revision;
    let before_node_projection_revision = state.node_projection.revision;
    state.turn_delivery.expire_submit(now_unix_ms());
    let before_generation = state.attachment_generation;
    let before_session_binding_generation = state.session_binding_generation;
    let before_provider = state.provider.clone();
    let before_provider_session_ref = state.provider_session_ref.clone();
    let before_mode = state.mode.clone();
    let before_supervisor = state.attached_supervisor_id.clone();
    let before_runtime_state = state.runtime_state.clone();
    let before_protocol_state = state.protocol_state.clone();
    let before_protocol_owner = state.protocol_owner_id.clone();
    let before_exit_code = state.child_exit_code;
    let before_channel_registered = state.channel_registered;
    if let Err(error) = refresh_runtime_state(&mut state, &terminal) {
        write_response(stream, &failure("HOST_CHILD_STATUS_FAILED", error));
        return;
    }
    let response = apply_request(
        &mut state,
        request,
        &shutdown,
        Some(&terminal),
        channel.as_deref(),
    );
    if response.status == "OK"
        && (before_turn_revision != state.turn_delivery.revision
            || before_node_projection_revision != state.node_projection.revision
            || before_generation != state.attachment_generation
            || before_session_binding_generation != state.session_binding_generation
            || before_provider != state.provider
            || before_provider_session_ref != state.provider_session_ref
            || before_mode != state.mode
            || before_supervisor != state.attached_supervisor_id
            || before_runtime_state != state.runtime_state
            || before_protocol_state != state.protocol_state
            || before_protocol_owner != state.protocol_owner_id
            || before_exit_code != state.child_exit_code
            || before_channel_registered != state.channel_registered)
        && let Err(error) = atomic_write_state(&state_file, &state)
    {
        write_response(stream, &failure("HOST_STATE_WRITE_FAILED", error));
        return;
    }
    let dispatch_native = response.status == "OK" && state.provider.as_deref()==Some("CODEX");
    write_response(stream, &response);
    drop(state);
    if dispatch_native {
        if let Some(adapter)=&terminal.native_queue {
            loop {
                let job = {
                    let Ok(mut state)=shared.lock() else { return; };
                    if state.runtime_state != "LIVE" { return; }
                    let thread=state.provider_session_ref.clone().unwrap_or_default();
                    let Some((mid,text))=state.turn_delivery.reserve_native() else { return; };
                    if let Err(error)=atomic_write_state(&state_file,&state) {
                        state.turn_delivery.finish_native(&mid,Err(format!("Host reservation evidence failed: {error}")));
                        return;
                    }
                    (mid,text,thread)
                };
                let result=adapter.submit(&job.2,&job.1);
                if let Ok(mut state)=shared.lock() {
                    state.turn_delivery.finish_native(&job.0,result);
                    if let Err(error)=atomic_write_state(&state_file,&state) {
                        state.turn_delivery.finish_native(&job.0,Err(format!("Host queue outcome evidence failed: {error}")));
                    }
                }
            }
        }
    }
}

fn serve(config: Config) -> Result<(), String> {
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|error| error.to_string())?;
    listener
        .set_nonblocking(true)
        .map_err(|error| error.to_string())?;
    let address: SocketAddr = listener.local_addr().map_err(|error| error.to_string())?;
    let started_at = now_unix_ms();
    let host_id = format!("host-{}-{started_at:x}", process::id());
    let (terminal, child_pid, child_started_at_unix_ms) =
        TerminalRuntime::spawn(&config, &host_id)?;
    let cwd = config
        .cwd
        .as_deref()
        .map(|path| path.to_string_lossy().into_owned());
    let channel_enabled = config.channel_lookup_file.is_some();
    let channel = match (
        config.channel_bootstrap_token.clone(),
        config.channel_session_token.clone(),
    ) {
        (Some(bootstrap), Some(session)) => Some(Arc::new(Mutex::new(MessageChannel::new(
            bootstrap, session,
        )))),
        _ => None,
    };
    let state = HostSnapshot {
        schema: STATE_SCHEMA,
        host_id,
        anchor_ref: config.anchor_ref,
        host_kind: config.host_kind,
        owner_ref: config.owner_ref,
        server_version: config.server_version,
        supervisor_version: config.supervisor_version,
        host_version: config.host_version,
        pty_version: config.pty_version,
        endpoint: format!("tcp://{address}"),
        pid: process::id(),
        started_at_unix_ms: started_at,
        attachment_generation: 0,
        session_binding_generation: 0,
        provider: None,
        provider_session_ref: None,
        mode: None,
        attached_supervisor_id: None,
        runtime_state: "LIVE".to_owned(),
        protocol_state: "NEW".to_owned(),
        protocol_owner_id: None,
        shell: config.shell,
        cwd,
        child_pid,
        child_started_at_unix_ms,
        child_exit_code: None,
        handle_kinds: ["CONPTY", "INPUT_WRITER", "OUTPUT_READER"],
        channel_enabled,
        channel_registered: false,
        auth_token: config.token,
        turn_delivery: turn_delivery::TurnDelivery { native_queue_available: terminal.native_queue.is_some(), ..Default::default() },
        node_projection: Default::default(),
    };
    atomic_write_state(&config.state_file, &state)?;
    if let (Some(path), Some(bootstrap)) = (
        config.channel_lookup_file.as_deref(),
        config.channel_bootstrap_token.as_deref(),
    ) {
        let parent = path
            .parent()
            .ok_or_else(|| "channel lookup requires a parent directory".to_owned())?;
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        let temporary = path.with_extension(format!("tmp-{}", process::id()));
        fs::write(
            &temporary,
            serde_json::to_vec(&json!({"endpoint": state.endpoint, "bootstrap_token": bootstrap}))
                .map_err(|error| error.to_string())?,
        )
        .map_err(|error| error.to_string())?;
        if path.exists() {
            fs::remove_file(path).map_err(|error| error.to_string())?;
        }
        fs::rename(&temporary, path).map_err(|error| error.to_string())?;
    }
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
                let channel = channel.as_ref().map(Arc::clone);
                thread::spawn(move || {
                    handle_connection(stream, state, terminal, state_file, shutdown_flag, channel)
                });
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(20));
            }
            Err(error) => return Err(error.to_string()),
        }
    }
    if let Some(path) = config.channel_lookup_file.as_deref() {
        let _ = fs::remove_file(path);
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

    #[test]
    fn state_write_retry_policy_is_bounded_to_sharing_errors() {
        assert_eq!(STATE_WRITE_RETRIES, 40);
        assert_eq!(STATE_WRITE_RETRY_DELAY_MS, 10);
        assert!(retryable_state_write_error(&std::io::Error::new(
            std::io::ErrorKind::PermissionDenied,
            "sharing violation",
        )));
        assert!(retryable_state_write_error(&std::io::Error::new(
            std::io::ErrorKind::WouldBlock,
            "would block",
        )));
        assert!(!retryable_state_write_error(&std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "missing parent",
        )));
    }

    fn snapshot() -> HostSnapshot {
        HostSnapshot {
            schema: STATE_SCHEMA,
            host_id: "host-test".to_owned(),
            anchor_ref: "anchor-test".to_owned(),
            host_kind: "SESSION".to_owned(),
            owner_ref: "anchor-test".to_owned(),
            server_version: "UniverseLocal/1".to_owned(),
            supervisor_version: "UniverseSupervisor/1".to_owned(),
            host_version: "UniverseSessionHost/1".to_owned(),
            pty_version: "UniverseConPty/1".to_owned(),
            endpoint: "tcp://127.0.0.1:1".to_owned(),
            pid: 1,
            started_at_unix_ms: 1,
            attachment_generation: 0,
            session_binding_generation: 0,
            provider: None,
            provider_session_ref: None,
            mode: None,
            attached_supervisor_id: None,
            runtime_state: "LIVE".to_owned(),
            protocol_state: "NEW".to_owned(),
            protocol_owner_id: None,
            shell: "cmd.exe".to_owned(),
            cwd: None,
            child_pid: Some(2),
            child_started_at_unix_ms: Some(3),
            child_exit_code: None,
            handle_kinds: ["CONPTY", "INPUT_WRITER", "OUTPUT_READER"],
            channel_enabled: false,
            channel_registered: false,
            auth_token: "token".to_owned(),
            turn_delivery: Default::default(),
            node_projection: Default::default(),
        }
    }

    #[test]
    fn public_snapshot_reports_child_start_time() {
        let mut state = snapshot();
        state.child_started_at_unix_ms = Some(123_456);
        let public = PublicSnapshot::from(&state);
        assert_eq!(Some(123_456_u128), public.child_started_at_unix_ms);
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
                input_base64: None,
                after_cursor: None,
                cols: None,
                rows: None,
                channel: None,
                provider: None,
                provider_session_ref: None,
                mode: None,
            },
            &shutdown,
            None,
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
                input_base64: None,
                after_cursor: None,
                cols: None,
                rows: None,
                channel: None,
                provider: None,
                provider_session_ref: None,
                mode: None,
            },
            &shutdown,
            None,
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
                input_base64: None,
                after_cursor: None,
                cols: None,
                rows: None,
                channel: None,
                provider: None,
                provider_session_ref: None,
                mode: None,
            },
            &shutdown,
            None,
            None,
        );
        assert_eq!(response.error_code, Some("HOST_UNAUTHORIZED"));
        assert_eq!(state.attachment_generation, 0);
        assert!(state.attached_supervisor_id.is_none());
    }

    #[test]
    fn explicit_session_bus_reply_requires_exact_anchor_and_flag() {
        let mut state = snapshot();
        state.provider = Some("CLAUDE".to_owned());
        state.provider_session_ref = None;
        let shutdown = AtomicBool::new(false);
        let attach = HostRequest {
            token: "token".to_owned(),
            action: "attach".to_owned(),
            supervisor_id: Some("supervisor-a".to_owned()),
            input: None,
            input_base64: None,
            after_cursor: None,
            cols: None,
            rows: None,
            channel: None,
            provider: None,
            provider_session_ref: None,
            mode: None,
        };
        assert_eq!(apply_request(&mut state, attach, &shutdown, None, None).status, "OK");

        let offer = |anchor: &str, delivery_mode: Option<&str>| HostRequest {
            token: "token".to_owned(),
            action: "turn_offer".to_owned(),
            supervisor_id: Some("supervisor-a".to_owned()),
            input: None,
            input_base64: None,
            after_cursor: None,
            cols: None,
            rows: None,
            channel: Some(json!({
                "message_id": "msg-explicit-reply",
                "text": "bounded reply",
                "session_anchor_ref": anchor,
                "provider": "CLAUDE",
                "provider_session_ref": "",
                "delivery_mode": delivery_mode,
                "awaits_authoritative_reply": delivery_mode.is_some(),
            })),
            provider: None,
            provider_session_ref: None,
            mode: None,
        };

        let accepted = apply_request(
            &mut state,
            offer("anchor-test", Some("EXPLICIT_SESSION_BUS_REPLY")),
            &shutdown,
            None,
            None,
        );
        assert_eq!(accepted.status, "OK");

        let wrong_anchor = apply_request(
            &mut state,
            offer("different-anchor", Some("EXPLICIT_SESSION_BUS_REPLY")),
            &shutdown,
            None,
            None,
        );
        assert_eq!(wrong_anchor.error_code, Some("HOST_TURN_BINDING_MISMATCH"));

        let missing_flag = apply_request(
            &mut state,
            offer("anchor-test", None),
            &shutdown,
            None,
            None,
        );
        assert_eq!(missing_flag.error_code, Some("HOST_TURN_BINDING_MISMATCH"));
    }

    #[test]
    fn protocol_initialization_is_owned_by_host_across_supervisor_rebind() {
        let mut state = snapshot();
        let shutdown = AtomicBool::new(false);
        let request = |action: &str, supervisor: &str| HostRequest {
            token: "token".to_owned(),
            action: action.to_owned(),
            supervisor_id: Some(supervisor.to_owned()),
            input: None,
            input_base64: None,
            after_cursor: None,
            cols: None,
            rows: None,
            channel: None,
            provider: None,
            provider_session_ref: None,
            mode: None,
        };

        assert_eq!(
            apply_request(
                &mut state,
                request("attach", "supervisor-a"),
                &shutdown,
                None,
                None,
            )
            .status,
            "OK"
        );
        assert_eq!(
            apply_request(
                &mut state,
                request("protocol_initialize_begin", "supervisor-a"),
                &shutdown,
                None,
                None,
            )
            .status,
            "OK"
        );
        assert_eq!(state.protocol_state, "INITIALIZING");
        assert_eq!(
            apply_request(
                &mut state,
                request("protocol_initialize_complete", "supervisor-a"),
                &shutdown,
                None,
                None,
            )
            .status,
            "OK"
        );
        assert_eq!(state.protocol_state, "INITIALIZED");

        apply_request(
            &mut state,
            request("attach", "supervisor-b"),
            &shutdown,
            None,
            None,
        );
        let rebound = apply_request(
            &mut state,
            request("protocol_initialize_begin", "supervisor-b"),
            &shutdown,
            None,
            None,
        );
        assert_eq!(rebound.status, "OK");
        assert_eq!(state.protocol_state, "INITIALIZED");
        assert!(state.protocol_owner_id.is_none());
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

    #[test]
    fn channel_ack_is_not_a_final_result() {
        let mut channel = MessageChannel::new("boot".into(), "session".into());
        channel.exchange("boot").unwrap();
        channel.push(&json!({"message_id":"m", "content":"work", "session_anchor_ref":"a"})).unwrap();
        for phase in ["RECEIVED", "STARTED"] {
            let ack = channel.submit_result("session", &json!({"message_id":"m", "body_text":"ack", "kind":"ACK", "phase":phase})).unwrap();
            assert_eq!(ack["status"], "ACKNOWLEDGED");
        }
        let final_result = json!({"message_id":"m", "body_text":"done"});
        assert_eq!(channel.submit_result("session", &final_result).unwrap()["status"], "ACCEPTED");
        assert_eq!(channel.submit_result("session", &final_result).unwrap()["status"], "DUPLICATE");
        assert_eq!(channel.submit_result("session", &json!({"message_id":"m", "body_text":"different"})).unwrap_err().0, "CHANNEL_RESULT_CONFLICT");
        assert_eq!(channel.result("m")["body_text"], "done");
    }
}
