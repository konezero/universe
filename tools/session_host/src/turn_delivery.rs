//! Provider turn state and at-most-once input ownership live with the PTY.
use serde::Serialize;
use serde_json::{Value, json};
use std::collections::VecDeque;

#[derive(Clone, Debug, Serialize)]
pub struct Delivery {
    pub message_id: String,
    pub phase: String,
    pub turn_id: String,
    #[serde(skip)]
    pub(crate) text: String,
    pub transport: String,
    pub queue_calls: u32,
    pub queued_submission_id: Option<String>,
    pub body_writes: u32,
    pub body_write_attempted_at_ms: Option<u128>,
    pub submit_write_attempted_at_ms: Option<u128>,
    pub submit_writes: u32,
    pub input_settle: Option<Value>,
    pub error_code: Option<String>,
    pub detail: Option<String>,
}
#[derive(Clone, Debug, Serialize)]
pub struct TurnDelivery {
    pub capability: &'static str,
    pub native_queue_available: bool,
    pub state: String,
    pub input_active: bool,
    pub revision: u64,
    pub turn_id: String,
    pub last_event: String,
    pub observed_at_ms: u64,
    pub messages: VecDeque<Delivery>,
}
// Keep the discovery document below its 64 KiB limit. The surviving Host keeps
// the full dedup ledger; only the latest 64 receipts are retained for diagnostics.
pub fn serialize_recent<S: serde::Serializer>(
    state: &TurnDelivery,
    serializer: S,
) -> Result<S::Ok, S::Error> {
    let mut recent = state.clone();
    while recent.messages.len() > 64 {
        recent.messages.pop_front();
    }
    recent.serialize(serializer)
}
impl Default for TurnDelivery {
    fn default() -> Self {
        Self {
            capability: "HOST_TURN_DELIVERY_V1",
            native_queue_available: false,
            state: "UNKNOWN".into(),
            input_active: false,
            revision: 0,
            turn_id: String::new(),
            last_event: String::new(),
            observed_at_ms: 0,
            messages: VecDeque::new(),
        }
    }
}
impl TurnDelivery {
    pub fn offer(&mut self, payload: &Value) -> Result<Value, (&'static str, String)> {
        let mid = payload["message_id"].as_str().unwrap_or("");
        let text = payload["text"].as_str().unwrap_or("");
        if mid.is_empty() || mid.len() > 80 || text.is_empty() || text.len() > 48000 {
            return Err((
                "HOST_DELIVERY_INVALID",
                "bounded message_id and text are required".into(),
            ));
        }
        if let Some(previous) = self.messages.iter().find(|m| m.message_id == mid) {
            if previous.text != text {
                return Err((
                    "HOST_DELIVERY_CONFLICT",
                    "message_id already has a different payload".into(),
                ));
            }
            return Ok(json!({"status":"DUPLICATE","message_id":mid,"phase":previous.phase}));
        }
        if self.messages.len() >= 1024 {
            return Err((
                "HOST_DELIVERY_CAPACITY",
                "Host delivery ledger is full; entries are never evicted and replayed".into(),
            ));
        }
        self.messages.push_back(Delivery {
            message_id: mid.into(),
            text: text.into(),
            transport: payload["_transport"].as_str().unwrap_or("PTY_INPUT").into(),
            queue_calls: 0,
            queued_submission_id: None,
            phase: "QUEUED".into(),
            turn_id: String::new(),
            body_writes: 0,
            body_write_attempted_at_ms: None,
            submit_write_attempted_at_ms: None,
            submit_writes: 0,
            input_settle: None,
            error_code: None,
            detail: None,
        });
        self.revision += 1;
        Ok(json!({"status":"ACCEPTED","message_id":mid,"phase":"QUEUED"}))
    }
    pub fn observe(&mut self, event: &Value) -> Result<(), (&'static str, String)> {
        let kind = event["event"].as_str().unwrap_or("");
        let at = event["observed_at_ms"].as_u64().unwrap_or(0);
        let turn = event["turn_id"].as_str().unwrap_or("");
        if !matches!(
            kind,
            "PROMPT_SUBMITTED"
                | "WORKING"
                | "STOPPING"
                | "IDLE"
                | "WAITING_USER"
                | "SESSION_ENDED"
                | "INTERRUPTED"
        ) || at == 0
        {
            return Err((
                "HOST_TURN_EVENT_INVALID",
                "known lifecycle event and timestamp required".into(),
            ));
        }
        if at < self.observed_at_ms {
            return Ok(());
        }
        if matches!(kind, "IDLE" | "STOPPING" | "INTERRUPTED")
            && !turn.is_empty()
            && !self.turn_id.is_empty()
            && turn != self.turn_id
        {
            return Ok(());
        }
        if kind == "IDLE"
            && (self.input_active || matches!(self.state.as_str(), "STARTING" | "INPUT_ACTIVE"))
        {
            return Ok(());
        }
        if !turn.is_empty() {
            self.turn_id = turn.into();
        }
        self.observed_at_ms = at;
        self.last_event = kind.into();
        self.revision += 1;
        self.state = match kind {
            "PROMPT_SUBMITTED" => "WORKING",
            "WORKING" => "WORKING",
            "STOPPING" => "STOPPING",
            "IDLE" => "IDLE",
            "WAITING_USER" => "WAITING_USER",
            "SESSION_ENDED" => "SESSION_ENDED",
            _ => "INTERRUPTED",
        }
        .into();
        if kind == "PROMPT_SUBMITTED" {
            self.input_active = false;
            let mid = event["message_id"].as_str().unwrap_or("");
            if let Some(m) = self
                .messages
                .iter_mut()
                .find(|m| m.message_id == mid && matches!(m.phase.as_str(), "AWAITING_START" | "SUBMIT_UNCONFIRMED" | "INPUT_UNCONFIRMED" | "NATIVE_SUBMITTING" | "NATIVE_QUEUED" | "NATIVE_UNCONFIRMED"))
            {
                m.phase = "PROMPT_SUBMITTED".into();
                m.turn_id = turn.into();
            }
        }
        if kind == "WORKING" || kind == "STOPPING" {
            if let Some(m) =
                self.messages.iter_mut().rev().find(|m| {
                    m.phase == "PROMPT_SUBMITTED" && !turn.is_empty() && m.turn_id == turn
                })
            {
                m.phase = "STARTED".into();
            }
        }
        Ok(())
    }
    pub fn user_input(&mut self) {
        // A user's draft is never replaced by an automated message.
        if !self.input_active {
            self.revision += 1;
        }
        self.input_active = true;
        if matches!(self.state.as_str(), "IDLE" | "UNKNOWN" | "STOPPING") {
            self.state = "INPUT_ACTIVE".into();
        }
    }
    pub fn reserve_native(&mut self) -> Option<(String, String)> {
        if self.messages.iter().any(|m| m.phase == "NATIVE_SUBMITTING") { return None; }
        let m = self.messages.iter_mut().find(|m| m.phase == "QUEUED" && m.transport == "CODEX_NATIVE_QUEUE")?;
        m.phase = "NATIVE_SUBMITTING".into();
        m.queue_calls += 1;
        self.revision += 1;
        Some((m.message_id.clone(), m.text.clone()))
    }
    pub fn finish_native(&mut self, mid: &str, result: Result<String, String>) {
        let Some(m) = self.messages.iter_mut().find(|m| m.message_id == mid) else { return; };
        match result {
            Ok(id) => { m.queued_submission_id = Some(id); if m.phase == "NATIVE_SUBMITTING" { m.phase = "NATIVE_QUEUED".into(); } }
            Err(detail) => {
                if m.phase == "NATIVE_SUBMITTING" { m.phase = "NATIVE_UNCONFIRMED".into(); }
                m.error_code = Some("HOST_NATIVE_QUEUE_UNCONFIRMED".into()); m.detail = Some(detail);
            }
        }
        self.revision += 1;
    }
    #[cfg(test)]
    pub fn drain(&mut self, write: impl FnMut(&[u8]) -> Result<(), String>) {
        self.drain_with_settle(write, || Ok(json!({"reason":"TEST_SETTLED"})));
    }
    pub fn expire_submit(&mut self, now_ms: u128) {
        for m in &mut self.messages {
            if m.phase == "AWAITING_START" && m.submit_write_attempted_at_ms.is_some_and(|at| now_ms.saturating_sub(at) >= 30_000) {
                m.phase = "SUBMIT_UNCONFIRMED".into();
                m.error_code = Some("HOST_SUBMIT_ACK_TIMEOUT".into());
                m.detail = Some("No matching PROMPT_SUBMITTED within 30 seconds; no input retry".into());
                self.revision += 1;
            }
        }
    }
    pub fn drain_with_settle(&mut self, mut write: impl FnMut(&[u8]) -> Result<(), String>, settle: impl FnOnce() -> Result<Value, Value>) {
        if self.state != "IDLE"
            || self.input_active
            || self
                .messages
                .iter()
                .any(|m| matches!(m.phase.as_str(), "AWAITING_START" | "WRITE_UNCERTAIN" | "INPUT_UNCONFIRMED" | "SUBMIT_UNCONFIRMED"))
        {
            return;
        }
        let Some(m) = self.messages.iter_mut().find(|m| m.phase == "QUEUED" && m.transport == "PTY_INPUT") else {
            return;
        };
        // Reserve before any bytes. An IPC timeout never releases this reservation.
        self.state = "STARTING".into();
        self.revision += 1;
        m.phase = "AWAITING_START".into();
        let text = m.text.replace('\u{1b}', "<ESC>");
        let frame = text
            .replace("\r\n", "\n")
            .replace('\r', "\n")
            .split('\n')
            .collect::<Vec<_>>()
            .join(" ");
        m.body_write_attempted_at_ms = Some(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis(),
        );
        m.body_writes += 1;
        if let Err(error) = write(frame.as_bytes()) {
            m.phase = "WRITE_UNCERTAIN".into();
            m.error_code = Some("HOST_BODY_WRITE_FAILED".into());
            m.detail = Some(error);
            return;
        }
        // Restore output-based pacing removed during the Python -> Rust move.
        // Output is only a transport observation, never CLI acceptance evidence.
        match settle() {
            Ok(evidence) => m.input_settle = Some(evidence),
            Err(evidence) => {
                m.input_settle = Some(evidence);
                m.phase = "INPUT_UNCONFIRMED".into();
                m.error_code = Some("HOST_INPUT_SETTLE_TIMEOUT".into());
                m.detail = Some("No bounded output settling observed; Enter was not sent and body will not be repeated".into());
                return;
            }
        }
        m.submit_write_attempted_at_ms = Some(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis(),
        );
        m.submit_writes += 1;
        if let Err(error) = write(b"\r") {
            m.phase = "WRITE_UNCERTAIN".into();
            m.error_code = Some("HOST_SUBMIT_WRITE_FAILED".into());
            m.detail = Some(error);
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn event(kind: &str, at: u64) -> Value {
        json!({"event":kind,"observed_at_ms":at,"turn_id":"t1"})
    }
    #[test]
    fn stop_is_not_idle_and_echo_is_not_start() {
        let mut t = TurnDelivery::default();
        t.observe(&event("PROMPT_SUBMITTED", 1)).unwrap();
        t.offer(&json!({"message_id":"m","text":"hello"})).unwrap();
        t.observe(&event("STOPPING", 2)).unwrap();
        t.drain(|_| panic!("must not write during stop hooks"));
        t.observe(&event("IDLE", 3)).unwrap();
        let mut writes = Vec::new();
        t.drain(|b| {
            writes.push(b.to_vec());
            Ok(())
        });
        assert_eq!(2, writes.len());
        assert_eq!("AWAITING_START", t.messages[0].phase);
        t.observe(&event("IDLE", 4)).unwrap();
        t.drain(|_| panic!("no double delivery"));
        t.observe(
            &json!({"event":"PROMPT_SUBMITTED","observed_at_ms":5,"turn_id":"t2","message_id":"m"}),
        )
        .unwrap();
        assert_eq!("PROMPT_SUBMITTED", t.messages[0].phase);
        t.observe(&event("IDLE", 6)).unwrap();
        assert_eq!("WORKING", t.state);
    }
    #[test]
    fn duplicates_conflicts_and_uncertain_write() {
        let mut t = TurnDelivery::default();
        let p = json!({"message_id":"m","text":"hello"});
        t.offer(&p).unwrap();
        assert_eq!("DUPLICATE", t.offer(&p).unwrap()["status"]);
        assert!(
            t.offer(&json!({"message_id":"m","text":"different"}))
                .is_err()
        );
        t.observe(&event("IDLE", 1)).unwrap();
        t.drain(|_| Err("lost writer".into()));
        assert_eq!("WRITE_UNCERTAIN", t.messages[0].phase);
        t.offer(&p).unwrap();
        t.drain(|_| panic!("uncertain writes must not retry"));
        assert_eq!(1, t.messages[0].body_writes);
    }
    #[test]
    fn another_turn_cannot_ack_a_previous_message() {
        let mut t = TurnDelivery::default();
        t.offer(&json!({"message_id":"m","text":"hello"})).unwrap();
        t.observe(&event("IDLE", 1)).unwrap();
        t.drain(|_| Ok(()));
        t.observe(&json!({"event":"PROMPT_SUBMITTED","observed_at_ms":2,"turn_id":"bus-turn","message_id":"m"})).unwrap();
        t.observe(&json!({"event":"PROMPT_SUBMITTED","observed_at_ms":3,"turn_id":"human-turn"}))
            .unwrap();
        t.observe(&json!({"event":"STOPPING","observed_at_ms":4,"turn_id":"bus-turn"}))
            .unwrap();
        assert_eq!("WORKING", t.state);
        t.observe(&json!({"event":"STOPPING","observed_at_ms":5,"turn_id":"human-turn"}))
            .unwrap();
        assert_eq!("PROMPT_SUBMITTED", t.messages[0].phase);
        let revision = t.revision;
        t.user_input();
        assert!(t.revision > revision);
    }
    #[test]
    fn submit_failure_is_not_a_reason_to_repeat_the_body() {
        let mut t = TurnDelivery::default();
        let p = json!({"message_id":"m","text":"hello"});
        t.offer(&p).unwrap();
        t.observe(&event("IDLE", 1)).unwrap();
        let mut calls = 0;
        t.drain(|_| {
            calls += 1;
            if calls == 2 {
                Err("submit failed".into())
            } else {
                Ok(())
            }
        });
        assert_eq!(
            "HOST_SUBMIT_WRITE_FAILED",
            t.messages[0].error_code.as_deref().unwrap()
        );
        assert_eq!(1, t.messages[0].body_writes);
        assert_eq!(1, t.messages[0].submit_writes);
        t.offer(&p).unwrap();
        t.drain(|_| panic!("must not retry uncertain submit"));
    }
    #[test]
    fn human_draft_and_permission_block_delivery() {
        let mut t = TurnDelivery::default();
        t.observe(&event("IDLE", 1)).unwrap();
        t.user_input();
        t.observe(&event("IDLE", 2)).unwrap();
        assert_eq!("INPUT_ACTIVE", t.state);
        t.offer(&json!({"message_id":"m","text":"hello"})).unwrap();
        t.drain(|_| panic!());
        t.observe(&event("WAITING_USER", 3)).unwrap();
        t.drain(|_| panic!());
    }
}

// Explicit output progress plus quiet time, independent of wall-clock jumps.
// Bounded to 2 seconds as in the previous Python composer wait. No matching
// progress is a failure to confirm, not permission to blindly send Enter.
pub struct InputSettle {
    baseline: u64,
    cursor: u64,
    first_output_ms: Option<u64>,
    changed_ms: u64,
}
impl InputSettle {
    pub fn new(baseline: u64) -> Self {
        Self { baseline, cursor: baseline, first_output_ms: None, changed_ms: 0 }
    }
    pub fn observe(&mut self, cursor: u64, elapsed_ms: u64) -> Option<Result<Value, Value>> {
        if cursor > self.cursor {
            self.cursor = cursor;
            self.first_output_ms.get_or_insert(elapsed_ms);
            self.changed_ms = elapsed_ms;
        }
        // The old placeholder branch allowed up to 500ms after its repaint.
        let settled = self.first_output_ms.is_some() && elapsed_ms.saturating_sub(self.changed_ms) >= 500;
        let evidence = || json!({"baseline_cursor":self.baseline,"last_cursor":self.cursor,
            "first_output_after_body_ms":self.first_output_ms,"last_output_after_body_ms":self.changed_ms,
            "elapsed_ms":elapsed_ms,"quiet_ms":elapsed_ms.saturating_sub(self.changed_ms),
            "reason":if settled {"OUTPUT_SETTLED"} else {"OUTPUT_SETTLE_TIMEOUT"}});
        if settled { Some(Ok(evidence())) }
        else if elapsed_ms >= 2000 { Some(Err(evidence())) }
        else { None }
    }
}
#[cfg(test)]
mod settle_tests {
    use super::*;
    #[test]
    fn delayed_input_is_not_submitted_at_the_old_fixed_500ms_boundary() {
        let mut gate = InputSettle::new(20);
        assert!(gate.observe(20, 500).is_none());
        assert!(gate.observe(30, 800).is_none());
        assert!(gate.observe(40, 1100).is_none());
        assert!(gate.observe(40, 1500).is_none());
        let evidence = gate.observe(40, 1600).unwrap().unwrap();
        assert_eq!(evidence["first_output_after_body_ms"],800);
        assert_eq!(evidence["quiet_ms"],500);
    }
    #[test]
    fn absent_or_continuous_output_times_out_without_enter_or_repaste() {
        for advances in [false,true] {
            let mut gate = InputSettle::new(0);
            assert!(gate.observe(if advances {1} else {0}, 1900).is_none());
            let evidence = gate.observe(if advances {2} else {0},2000).unwrap().unwrap_err();
            let mut t=TurnDelivery::default();
            t.offer(&json!({"message_id":"m","text":"hi"})).unwrap();
            t.observe(&json!({"event":"IDLE","observed_at_ms":1})).unwrap();
            let mut writes=vec![];
            t.drain_with_settle(|b|{writes.push(b.to_vec());Ok(())},||Err(evidence));
            assert_eq!(writes, vec![b"hi".to_vec()]);
            assert_eq!(t.messages[0].phase,"INPUT_UNCONFIRMED");
            t.drain(|_|panic!("no retry"));
        }
    }
    #[test]
    fn missing_submit_ack_is_bounded_but_late_matching_ack_is_preserved() {
        let mut t=TurnDelivery::default();
        t.offer(&json!({"message_id":"m","text":"hi"})).unwrap();
        t.observe(&json!({"event":"IDLE","observed_at_ms":1})).unwrap();
        t.drain(|_|Ok(()));
        let at=t.messages[0].submit_write_attempted_at_ms.unwrap();
        t.expire_submit(at+29999);
        assert_eq!(t.messages[0].phase,"AWAITING_START");
        t.expire_submit(at+30000);
        assert_eq!(t.messages[0].phase,"SUBMIT_UNCONFIRMED");
        t.drain(|_|panic!("no retry"));
        t.observe(&json!({"event":"PROMPT_SUBMITTED","observed_at_ms":2,"message_id":"m","turn_id":"late"})).unwrap();
        assert_eq!(t.messages[0].phase,"PROMPT_SUBMITTED");
        assert_eq!(t.messages[0].turn_id,"late");
        assert_eq!(t.messages[0].submit_writes,1);
    }
}

#[cfg(test)] mod native_delivery_tests {
 use super::*;
 #[test] fn native_queue_reservation_survives_replays_without_terminal_writes() {
  let mut t=TurnDelivery::default();
  let p=json!({"message_id":"m","text":"hello","_transport":"CODEX_NATIVE_QUEUE"});
  t.offer(&p).unwrap(); t.observe(&json!({"event":"IDLE","observed_at_ms":1})).unwrap();
  t.drain(|_|panic!("native queue must never write PTY"));
  assert_eq!(t.reserve_native().unwrap().0,"m");
  t.offer(&p).unwrap(); assert!(t.reserve_native().is_none());
  t.finish_native("m",Err("ambiguous timeout".into()));
  t.offer(&p).unwrap(); assert!(t.reserve_native().is_none());
  assert_eq!(t.messages[0].queue_calls,1);
  assert_eq!(t.messages[0].body_writes+t.messages[0].submit_writes,0);
 }
 #[test] fn early_prompt_hook_is_not_overwritten_by_late_queue_receipt() {
  let mut t=TurnDelivery::default();
  t.offer(&json!({"message_id":"m","text":"hello","_transport":"CODEX_NATIVE_QUEUE"})).unwrap();
  t.reserve_native().unwrap();
  t.observe(&json!({"event":"PROMPT_SUBMITTED","observed_at_ms":1,"message_id":"m","turn_id":"turn"})).unwrap();
  t.finish_native("m",Ok("queue-id".into()));
  assert_eq!(t.messages[0].phase,"PROMPT_SUBMITTED");
  assert_eq!(t.messages[0].turn_id,"turn");
  assert_eq!(t.messages[0].queued_submission_id.as_deref(),Some("queue-id"));
 }
}
