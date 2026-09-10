# Session Bus processing protocols

Messages, model activation, and human notifications are separate concerns.
The protocol is not a permission or Work Receipt. Existing actor, recipient,
claim, provider adapter and execution-authority checks remain in force.

| protocol | Compatible kind | Handling action | Automatic processing |
| --- | --- | --- | --- |
| WORK | INSTRUCTION | EXECUTE_WORK | Claim and deliver to the target model |
| CONVERSATION | COORDINATION | RESPOND | Claim and deliver for a question/coordination response |
| REPLY | RESULT | CORRELATE_REPLY | Correlate with the original message; existing Conductor loop forwards for processing |
| NOTICE | NOTE | DISPLAY_ONLY | Store/display only; never claim as model work |

POST `/v1/session-bus/messages` accepts `protocol: WORK|CONVERSATION|NOTICE`
instead of `kind`. If both are supplied, they must agree. Existing kind-only
clients keep their behavior. Explicit REPLY posting is rejected: use
`POST /v1/session-bus/messages/{original_message_id}/reply` so recipient and
thread correlation are resolved from the original. Legacy kind=RESULT posts
remain accepted for compatibility; migrate those callers to the reply route.

Public messages include server-derived `protocol` and `handling` fields.
`handling.action` is a processing discriminator, not an Action Gateway grant.
`wake_model` describes routing eligibility, not proof that a turn has started.
Persisted legacy messages are classified on read; no destructive migration.

Conductor reply processing reuses the existing durable forwarded-INSTRUCTION
transport slot, but its semantic protocol is REPLY and action PROCESS_REPLY.
Its prompt explicitly says that the report is not a new work authorization.
Completing it records a consumption receipt, including the summary/outcome,
on the forwarded message and consumed_at on the source result. It does not
create another RESULT, preventing reply-to-reply notification loops.
Actual follow-up work requires a separate WORK message. The existing final
result revision/idempotency implementation is preserved.

`notify: UI` is the supported display notification mode. Legacy HEADER is
accepted as an alias. Neither mode writes bytes to PTY stdin, terminal output,
screen snapshots, or replay. The UI inbox and unread badges display messages;
the composer explicitly distinguishes work, conversation, and notice.

CLI: `universe_session_inbox.py ... post --protocol CONVERSATION ...` requests
a response; `--protocol NOTICE` does not wake a model. The `reply` subcommand
returns a correlated result. The legacy default RESULT post remains unchanged.

Not changed here: provider turn-ID correlation, actual STARTED ACK semantics,
or the choice of which non-Conductor roles automatically consume results.
