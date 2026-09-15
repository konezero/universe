//! Durable node-assignment ownership, projected from the UniverseStore
//! original through the Supervisor into this exact Host (2026-09-15:
//! Session/PTY Supervisor -> Host node projection sync). This Host never
//! decides ownership -- it only records the last projection the Supervisor
//! pushed for *this* Host's own bound Anchor, and rejects anything that
//! does not carry that same Anchor or that is not newer than what it
//! already has. Mirrors turn_delivery.rs's shape (a small owned struct,
//! serialized read-only into the public snapshot, mutated only through one
//! validated apply() entry point) rather than inventing a second pattern.
use serde::Serialize;
use serde_json::{Value, json};

#[derive(Clone, Debug, Serialize)]
pub struct NodeProjection {
    pub capability: &'static str,
    /// UNKNOWN until the first projection lands; then ACTIVE or UNASSIGNED
    /// exactly as UniverseStore's session_persona_assignment.state names it.
    pub projection_state: String,
    pub node_ref: Option<String>,
    pub owner_session_anchor_ref: Option<String>,
    pub owner_assignment_revision: Option<u64>,
    pub received_at_ms: u64,
    /// Local, host-side change counter (distinct from
    /// owner_assignment_revision, which is the Store's own number) --
    /// handle_connection's persist-to-disk dirty-check compares this
    /// before/after a request the same way it already does for
    /// turn_delivery.revision, so a successful projection survives a Host
    /// restart instead of silently reverting to UNKNOWN.
    pub revision: u64,
}

impl Default for NodeProjection {
    fn default() -> Self {
        Self {
            capability: "HOST_NODE_PROJECTION_V1",
            projection_state: "UNKNOWN".into(),
            node_ref: None,
            owner_session_anchor_ref: None,
            owner_assignment_revision: None,
            received_at_ms: 0,
            revision: 0,
        }
    }
}

impl NodeProjection {
    /// `host_anchor_ref` is this Host's own, sealed binding identity
    /// (HostSnapshot.anchor_ref) -- never taken from the request. A
    /// projection whose own `session_anchor_ref` field does not match it
    /// is a cross-anchor push and is refused outright, regardless of
    /// revision.
    pub fn apply(
        &mut self,
        payload: &Value,
        host_anchor_ref: &str,
        now_ms: u64,
    ) -> Result<Value, (&'static str, String)> {
        let anchor = payload["session_anchor_ref"].as_str().unwrap_or("");
        if anchor.is_empty() || anchor != host_anchor_ref {
            return Err((
                "HOST_NODE_PROJECTION_ANCHOR_MISMATCH",
                "projection session_anchor_ref must equal this Host's own bound Anchor".into(),
            ));
        }
        let state = payload["state"].as_str().unwrap_or("");
        if !matches!(state, "ACTIVE" | "UNASSIGNED") {
            return Err((
                "HOST_NODE_PROJECTION_INVALID",
                "state must be ACTIVE or UNASSIGNED".into(),
            ));
        }
        let revision = payload["assignment_revision"].as_u64();
        let Some(revision) = revision else {
            return Err((
                "HOST_NODE_PROJECTION_INVALID",
                "assignment_revision is required".into(),
            ));
        };
        let node_ref = payload["node_ref"].as_str().map(|s| s.to_owned());
        if state == "ACTIVE" && node_ref.is_none() {
            return Err((
                "HOST_NODE_PROJECTION_INVALID",
                "node_ref is required when state is ACTIVE".into(),
            ));
        }
        // Replay of the exact same (owner, revision) is idempotent-safe --
        // a Supervisor that lost the response and retries must not be
        // rejected as stale. Anything OLDER than what is already recorded
        // for the same owner Anchor is a real stale/out-of-order push and
        // is refused, not silently applied.
        if let Some(current_revision) = self.owner_assignment_revision {
            if self.owner_session_anchor_ref.as_deref() == Some(anchor) && revision < current_revision
            {
                return Err((
                    "HOST_NODE_PROJECTION_STALE",
                    format!(
                        "assignment_revision {revision} is older than the already-recorded {current_revision}"
                    ),
                ));
            }
        }
        self.projection_state = state.to_owned();
        self.node_ref = if state == "ACTIVE" { node_ref } else { None };
        self.owner_session_anchor_ref = Some(anchor.to_owned());
        self.owner_assignment_revision = Some(revision);
        self.received_at_ms = now_ms;
        self.revision += 1;
        Ok(json!({
            "status": "ACCEPTED",
            "projection_state": self.projection_state,
            "node_ref": self.node_ref,
            "owner_assignment_revision": self.owner_assignment_revision,
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn active(node_ref: &str, anchor: &str, revision: u64) -> Value {
        json!({"session_anchor_ref": anchor, "state": "ACTIVE", "node_ref": node_ref, "assignment_revision": revision})
    }

    #[test]
    fn accepts_the_first_active_projection() {
        let mut p = NodeProjection::default();
        let result = p.apply(&active("feature_1", "anchor_a", 1), "anchor_a", 1000).unwrap();
        assert_eq!("ACCEPTED", result["status"]);
        assert_eq!("ACTIVE", p.projection_state);
        assert_eq!(Some("feature_1".to_owned()), p.node_ref);
        assert_eq!(Some(1), p.owner_assignment_revision);
    }

    #[test]
    fn rejects_a_different_anchor() {
        let mut p = NodeProjection::default();
        let err = p.apply(&active("feature_1", "anchor_b", 1), "anchor_a", 1000).unwrap_err();
        assert_eq!("HOST_NODE_PROJECTION_ANCHOR_MISMATCH", err.0);
        assert_eq!("UNKNOWN", p.projection_state);
    }

    #[test]
    fn rejects_a_stale_older_revision_after_a_newer_one_landed() {
        let mut p = NodeProjection::default();
        p.apply(&active("feature_1", "anchor_a", 5), "anchor_a", 1000).unwrap();
        let err = p.apply(&active("feature_1", "anchor_a", 3), "anchor_a", 2000).unwrap_err();
        assert_eq!("HOST_NODE_PROJECTION_STALE", err.0);
        // Unchanged: the stale push must not have overwritten anything.
        assert_eq!(Some(5), p.owner_assignment_revision);
    }

    #[test]
    fn replaying_the_exact_same_revision_is_accepted_not_rejected_as_stale() {
        let mut p = NodeProjection::default();
        p.apply(&active("feature_1", "anchor_a", 5), "anchor_a", 1000).unwrap();
        let result = p.apply(&active("feature_1", "anchor_a", 5), "anchor_a", 2000).unwrap();
        assert_eq!("ACCEPTED", result["status"]);
        assert_eq!(2000, p.received_at_ms);
    }

    #[test]
    fn unassigned_clears_node_ref_but_keeps_the_revision_history() {
        let mut p = NodeProjection::default();
        p.apply(&active("feature_1", "anchor_a", 1), "anchor_a", 1000).unwrap();
        let payload = json!({"session_anchor_ref": "anchor_a", "state": "UNASSIGNED", "assignment_revision": 2});
        p.apply(&payload, "anchor_a", 2000).unwrap();
        assert_eq!("UNASSIGNED", p.projection_state);
        assert_eq!(None, p.node_ref);
        assert_eq!(Some(2), p.owner_assignment_revision);
    }

    #[test]
    fn active_without_node_ref_is_rejected() {
        let mut p = NodeProjection::default();
        let payload = json!({"session_anchor_ref": "anchor_a", "state": "ACTIVE", "assignment_revision": 1});
        let err = p.apply(&payload, "anchor_a", 1000).unwrap_err();
        assert_eq!("HOST_NODE_PROJECTION_INVALID", err.0);
    }
}
