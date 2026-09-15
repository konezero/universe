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
    ///
    /// At the SAME `assignment_revision` as what is already recorded for
    /// this Anchor, the incoming (state, node_ref) content must match
    /// exactly what is already stored, or this is refused as a real
    /// conflict (two different assignment contents claiming the same
    /// revision number is a Store-side bug or a forged push, never
    /// silently accepted as the newer one) -- NOT treated as revision <
    /// current alone would miss (2026-09-15 Conductor static review: the
    /// original apply() only compared revision numbers and would have
    /// silently overwritten ACTIVE feature_A with ACTIVE feature_B, or
    /// ACTIVE with UNASSIGNED and back, at the identical revision). An
    /// EXACT replay (same revision, same content) is the one case that
    /// must stay a true no-op: it does not advance `revision` (the local
    /// change counter) or `received_at_ms`, so a Supervisor retrying a
    /// lost response can never be told anything changed when nothing did.
    pub fn apply(
        &mut self,
        payload: &Value,
        host_anchor_ref: &str,
        now_ms: u64,
    ) -> Result<Value, (&'static str, String)> {
        let anchor = payload["session_anchor_ref"].as_str().unwrap_or("").trim();
        if anchor.is_empty() || anchor != host_anchor_ref {
            return Err((
                "HOST_NODE_PROJECTION_ANCHOR_MISMATCH",
                "projection session_anchor_ref must equal this Host's own bound Anchor".into(),
            ));
        }
        let Some(state_raw) = payload["state"].as_str() else {
            return Err((
                "HOST_NODE_PROJECTION_INVALID",
                "state must be a string, ACTIVE or UNASSIGNED".into(),
            ));
        };
        let state = state_raw.trim();
        if !matches!(state, "ACTIVE" | "UNASSIGNED") {
            return Err((
                "HOST_NODE_PROJECTION_INVALID",
                "state must be ACTIVE or UNASSIGNED".into(),
            ));
        }
        let revision_field = &payload["assignment_revision"];
        let Some(revision) = revision_field.as_u64() else {
            let detail = if revision_field.is_i64() || revision_field.is_f64() {
                "assignment_revision must be a non-negative integer"
            } else {
                "assignment_revision is required and must be an integer"
            };
            return Err(("HOST_NODE_PROJECTION_INVALID", detail.into()));
        };
        let node_ref_field = &payload["node_ref"];
        let node_ref = match node_ref_field {
            Value::Null => None,
            Value::String(raw) => {
                let trimmed = raw.trim();
                if trimmed.is_empty() {
                    return Err((
                        "HOST_NODE_PROJECTION_INVALID",
                        "node_ref must not be an empty string; omit it or use null".into(),
                    ));
                }
                Some(trimmed.to_owned())
            }
            _ => {
                return Err((
                    "HOST_NODE_PROJECTION_INVALID",
                    "node_ref must be a string or null".into(),
                ));
            }
        };
        if state == "ACTIVE" && node_ref.is_none() {
            return Err((
                "HOST_NODE_PROJECTION_INVALID",
                "node_ref is required when state is ACTIVE".into(),
            ));
        }
        if state == "UNASSIGNED" && node_ref.is_some() {
            return Err((
                "HOST_NODE_PROJECTION_INVALID",
                "node_ref must be omitted or null when state is UNASSIGNED".into(),
            ));
        }
        let same_owner = self.owner_session_anchor_ref.as_deref() == Some(anchor);
        if same_owner {
            if let Some(current_revision) = self.owner_assignment_revision {
                if revision < current_revision {
                    return Err((
                        "HOST_NODE_PROJECTION_STALE",
                        format!(
                            "assignment_revision {revision} is older than the already-recorded {current_revision}"
                        ),
                    ));
                }
                if revision == current_revision {
                    let same_content =
                        self.projection_state == state && self.node_ref.as_deref() == node_ref.as_deref();
                    if same_content {
                        // Pure replay: no field on self changes, including
                        // revision and received_at_ms.
                        return Ok(json!({
                            "status": "REPLAYED",
                            "projection_state": self.projection_state,
                            "node_ref": self.node_ref,
                            "owner_assignment_revision": self.owner_assignment_revision,
                        }));
                    }
                    return Err((
                        "HOST_NODE_PROJECTION_CONFLICT",
                        format!(
                            "assignment_revision {revision} already recorded a different projection \
                             (state={:?}, node_ref={:?}) than this push (state={:?}, node_ref={:?})",
                            self.projection_state, self.node_ref, state, node_ref
                        ),
                    ));
                }
            }
        }
        self.projection_state = state.to_owned();
        self.node_ref = node_ref;
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
    fn replaying_the_exact_same_revision_and_content_is_a_pure_no_op() {
        let mut p = NodeProjection::default();
        p.apply(&active("feature_1", "anchor_a", 5), "anchor_a", 1000).unwrap();
        let result = p.apply(&active("feature_1", "anchor_a", 5), "anchor_a", 2000).unwrap();
        assert_eq!("REPLAYED", result["status"]);
        // Nothing was written -- neither the host-side change counter nor
        // the received-at timestamp advances on a true replay.
        assert_eq!(1, p.revision);
        assert_eq!(1000, p.received_at_ms);
    }

    #[test]
    fn same_revision_different_node_ref_is_an_explicit_conflict_not_a_silent_overwrite() {
        let mut p = NodeProjection::default();
        p.apply(&active("feature_A", "anchor_a", 5), "anchor_a", 1000).unwrap();
        let err = p.apply(&active("feature_B", "anchor_a", 5), "anchor_a", 2000).unwrap_err();
        assert_eq!("HOST_NODE_PROJECTION_CONFLICT", err.0);
        assert_eq!(Some("feature_A".to_owned()), p.node_ref);
        assert_eq!(1, p.revision);
    }

    #[test]
    fn same_revision_active_to_unassigned_is_a_conflict() {
        let mut p = NodeProjection::default();
        p.apply(&active("feature_A", "anchor_a", 5), "anchor_a", 1000).unwrap();
        let payload = json!({"session_anchor_ref": "anchor_a", "state": "UNASSIGNED", "assignment_revision": 5});
        let err = p.apply(&payload, "anchor_a", 2000).unwrap_err();
        assert_eq!("HOST_NODE_PROJECTION_CONFLICT", err.0);
        assert_eq!("ACTIVE", p.projection_state);
    }

    #[test]
    fn same_revision_unassigned_to_active_is_also_a_conflict_the_other_direction() {
        let mut p = NodeProjection::default();
        let unassigned = json!({"session_anchor_ref": "anchor_a", "state": "UNASSIGNED", "assignment_revision": 5});
        p.apply(&unassigned, "anchor_a", 1000).unwrap();
        let err = p.apply(&active("feature_A", "anchor_a", 5), "anchor_a", 2000).unwrap_err();
        assert_eq!("HOST_NODE_PROJECTION_CONFLICT", err.0);
        assert_eq!("UNASSIGNED", p.projection_state);
        assert_eq!(None, p.node_ref);
    }

    #[test]
    fn empty_string_node_ref_is_rejected_not_treated_as_null() {
        let mut p = NodeProjection::default();
        let payload = json!({"session_anchor_ref": "anchor_a", "state": "ACTIVE", "node_ref": "", "assignment_revision": 1});
        let err = p.apply(&payload, "anchor_a", 1000).unwrap_err();
        assert_eq!("HOST_NODE_PROJECTION_INVALID", err.0);
    }

    #[test]
    fn non_integer_revision_is_rejected() {
        let mut p = NodeProjection::default();
        let payload = json!({"session_anchor_ref": "anchor_a", "state": "ACTIVE", "node_ref": "feature_1", "assignment_revision": "3"});
        let err = p.apply(&payload, "anchor_a", 1000).unwrap_err();
        assert_eq!("HOST_NODE_PROJECTION_INVALID", err.0);
    }

    #[test]
    fn negative_revision_is_rejected() {
        let mut p = NodeProjection::default();
        let payload = json!({"session_anchor_ref": "anchor_a", "state": "ACTIVE", "node_ref": "feature_1", "assignment_revision": -1});
        let err = p.apply(&payload, "anchor_a", 1000).unwrap_err();
        assert_eq!("HOST_NODE_PROJECTION_INVALID", err.0);
    }

    #[test]
    fn unassigned_with_a_node_ref_present_is_rejected() {
        let mut p = NodeProjection::default();
        let payload = json!({"session_anchor_ref": "anchor_a", "state": "UNASSIGNED", "node_ref": "feature_1", "assignment_revision": 1});
        let err = p.apply(&payload, "anchor_a", 1000).unwrap_err();
        assert_eq!("HOST_NODE_PROJECTION_INVALID", err.0);
    }

    #[test]
    fn a_different_anchor_at_the_same_revision_number_is_not_confused_with_a_conflict() {
        // owner_session_anchor_ref differs, so this is the ordinary
        // cross-anchor rejection, not the same-owner conflict path.
        let mut p = NodeProjection::default();
        p.apply(&active("feature_A", "anchor_a", 5), "anchor_a", 1000).unwrap();
        let err = p.apply(&active("feature_A", "anchor_b", 5), "anchor_a", 2000).unwrap_err();
        assert_eq!("HOST_NODE_PROJECTION_ANCHOR_MISMATCH", err.0);
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
