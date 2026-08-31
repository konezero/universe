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
    attached_supervisor_id: Option<String>,
    runtime_state: String,
    protocol_state: String,
    protocol_owner_id: Option<String>,
    shell: String,
    cwd: Option<String>,
    child_pid: Option<u32>,
    child_exit_code: Option<u32>,
    handle_kinds: [&'static str; 3],
    channel_enabled: bool,
    channel_registered: bool,
    auth_token: String,
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
    attached_supervisor_id: Option<String>,
    runtime_state: String,
    protocol_state: String,
    shell: String,
    cwd: Option<String>,
    child_pid: Option<u32>,
    child_exit_code: Option<u32>,
    handle_kinds: [&'static str; 3],
    channel_enabled: bool,
    channel_registered: bool,
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
        Ok(json!({"status": "REGISTERED", "session_token": self.session_token}))
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
        let result = json!({
            "status": "ACCEPTED",
            "message_id": message_id,
            "body_text": body,
            "outcome": payload.get("outcome").and_then(Value::as_str).unwrap_or("COMPLETED"),
            "result_ref": payload.get("result_ref").and_then(Value::as_str).unwrap_or(""),
            "session_anchor_ref": anchor,
        });
        if let Some(existing) = self.results.get(message_id) {
            if existing == &result {
                return Ok(json!({"status": "DUPLICATE", "message_id": message_id}));
            }
            return Err((
                "CHANNEL_RESULT_CONFLICT",
                "channel result conflicts with the stored result".to_owned(),
            ));
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
}

impl TerminalRuntime {
    fn spawn(config: &Config) -> Result<(Self, Option<u32>), String> {
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
        let child = pair
            .slave
            .spawn_command(command)
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
                master: Mutex::new(pair.master),
                writer: Mutex::new(writer),
                child: Mutex::new(child),
                output,
            },
            child_pid,
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
            attached_supervisor_id: value.attached_supervisor_id.clone(),
            runtime_state: value.runtime_state.clone(),
            protocol_state: value.protocol_state.clone(),
            shell: value.shell.clone(),
            cwd: value.cwd.clone(),
            child_pid: value.child_pid,
            child_exit_code: value.child_exit_code,
            handle_kinds: value.handle_kinds,
            channel_enabled: value.channel_enabled,
            channel_registered: value.channel_registered,
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
    let before_generation = state.attachment_generation;
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
        && (before_generation != state.attachment_generation
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
    write_response(stream, &response);
}

fn serve(config: Config) -> Result<(), String> {
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|error| error.to_string())?;
    listener
        .set_nonblocking(true)
        .map_err(|error| error.to_string())?;
    let address: SocketAddr = listener.local_addr().map_err(|error| error.to_string())?;
    let started_at = now_unix_ms();
    let (terminal, child_pid) = TerminalRuntime::spawn(&config)?;
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
        host_id: format!("host-{}-{started_at:x}", process::id()),
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
        attached_supervisor_id: None,
        runtime_state: "LIVE".to_owned(),
        protocol_state: "NEW".to_owned(),
        protocol_owner_id: None,
        shell: config.shell,
        cwd,
        child_pid,
        child_exit_code: None,
        handle_kinds: ["CONPTY", "INPUT_WRITER", "OUTPUT_READER"],
        channel_enabled,
        channel_registered: false,
        auth_token: config.token,
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
            attached_supervisor_id: None,
            runtime_state: "LIVE".to_owned(),
            protocol_state: "NEW".to_owned(),
            protocol_owner_id: None,
            shell: "cmd.exe".to_owned(),
            cwd: None,
            child_pid: Some(2),
            child_exit_code: None,
            handle_kinds: ["CONPTY", "INPUT_WRITER", "OUTPUT_READER"],
            channel_enabled: false,
            channel_registered: false,
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
                input_base64: None,
                after_cursor: None,
                cols: None,
                rows: None,
                channel: None,
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
}
