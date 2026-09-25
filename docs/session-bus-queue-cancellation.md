# Session Bus queue cancellation

`POST /v1/session-bus/messages/{message_id}/cancel` uses the existing loopback
and local-operator API boundary and requires exactly `session_anchor_ref`
(the intended recipient) and `reason`.
The Session Bus UI exposes this as **대기 취소** on pending/claimed messages.

This is cancellation of pending delivery, not deletion of history or interruption
of a running task. Claim and cancellation share the mailbox lock. Cancellation
is persisted before becoming visible in memory; a storage failure does not
report success. Cancelled messages cannot subsequently be claimed, including
after a service restart. Repeating the operation is idempotent.

| Result | Meaning |
| --- | --- |
| CANCELLED | Cancelled before any claim or transport handoff |
| ALREADY_CANCELLED | Previously cancelled; history retained |
| PROVIDER_RECALL_UNSUPPORTED | Handoff already occurred; no recall performed |
| ALREADY_RECEIVED | Provider receipt exists; no turn interruption performed |
| NOT_PENDING | Message is terminal or otherwise not cancellable |

The response explicitly reports `provider_recalled=false`,
`execution_stopped=false`, and `history_preserved=true`. A generic state
transition cannot be used to bypass the cancellation boundary. In particular,
marking a Bus record FAILED does **not** remove a message from a provider queue.
The installed Codex queue CLI offers enqueue, not an authenticated recall action.
Never edit provider databases or delete their files to simulate recall.

For a wrong recipient, preserve the original record and disclose any unrecalled
delivery. Send a correction to that recipient when necessary, without granting
new work. Resolve the intended session's current exact Anchor and Goal assignment
before sending the correctly scoped message once. Verify provider receipt and
later work outcome separately; neither dispatch nor receipt proves completion.
