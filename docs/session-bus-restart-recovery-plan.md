# Session Bus restart recovery: Claude channel

Status: proposed work plan (2026-09-25). No runtime behavior is changed by this document.
Owner: Universe project; Fleet Goal `goal_session_bus_restart_recovery_20260925`.

## Problem and observed boundary

The Session Bus database is the durable instruction ledger; the Claude channel
is a live transport. `complete_instruction_claim` marks a channel push as
`DISPATCHED/STARTED` before provider `RECEIVED` or `STARTED` ACK. On server
restart, `recover_pending_deliveries` requeues interrupted `CLAIMED` messages,
but not channel `DISPATCHED/STARTED` messages. The latter block subsequent
instructions as `SESSION_BUSY`. `_recover_claude_channel_results` can reconcile
a completed result, but an absent result does not prove that Claude received
the instruction. A lost in-memory channel queue can therefore strand work.

Relevant source: `tools/universe_app/session_bus.py`,
`tools/universe_server.py`, `tools/claude_channel_broker.py`, and
`tools/universe_app/terminal_host.py`.

## Delivery contract

1. Persist the original instruction and stable `message_id` before transport.
   A channel push is an attempt, not provider acceptance or execution.
2. Keep distinct durable evidence for channel acceptance, provider `RECEIVED`,
   provider `STARTED`, and terminal result. Bind every observation to the exact
   terminal, Session Anchor, original message, and channel attempt.
3. On reconnect, reconcile an exact Host result first. If a provider ACK exists,
   preserve the in-flight instruction and do not replay it. If no ACK/result
   exists, redeliver the *same* instruction identity through the supported
   channel, with bounded retry/backoff and a visible pending/error state.
4. Channel/consumer deduplication must make a repeated delivery acknowledgeable
   without running the same instruction twice. If receipt status cannot be
   established, fail closed as `UNKNOWN`; do not silently clear `SESSION_BUSY`
   or invent a new instruction identity.
5. A `STARTED` instruction is not automatically re-executed merely because a
   lease expires or the service restarts. Reconcile the provider/Host result and
   Todo journal or Task Frame checkpoint; unresolved side effects require a
   bounded recovery decision.
6. A later queued instruction cannot leapfrog an unresolved earlier instruction
   for the same recipient. The UI should distinguish delivery pending, received,
   running, result pending, and recovery required.

This is at-least-once *delivery*, not exactly-once LLM execution. Any effectful
operation still needs its own idempotency key and authoritative receipt.

## Verification and rollout

- Use an isolated database and channel/Host fixture to cut the process at:
  before push, after push/before `RECEIVED`, after `RECEIVED`, after `STARTED`,
  and after result/before Bus reply. Restart and verify one original message
  identity, correct ACK/result reconciliation, no skipped queue item, and no
  duplicate execution.
- Include an exact-coordinate mismatch and unavailable Host case; neither may
  be treated as a safe replay or successful completion.
- Exercise the real resident Claude channel and service-restart route only with
  a scoped test session. Do not restart a shared service or re-run live work as
  part of unit tests. Record transport and lifecycle evidence separately.

## Why this shape

Slack retries deliveries without an intake ACK; RabbitMQ and NATS JetStream
redeliver unacknowledged work and require duplicate-safe consumers. Celery
documents the tradeoff between early ACK (lost work) and late ACK (possible
duplicate execution). Durable workflow systems checkpoint execution separately
from message delivery. Universe should adopt that separation without adding a
second broker solely to compensate for a missing recovery transition.

Primary references:

- Slack Events API: https://docs.slack.dev/apis/events-api/
- RabbitMQ acknowledgements: https://www.rabbitmq.com/docs/confirms
- NATS JetStream consumers: https://github.com/nats-io/nats.docs/blob/master/nats-concepts/jetstream/consumers.md
- Celery task acknowledgement: https://docs.celeryq.dev/en/stable/userguide/tasks.html
- LangGraph checkpoint boundaries: https://docs.langchain.com/oss/javascript/langgraph/thinking-in-langgraph
