# Master permission reply delivery

Conductor replies to a Host permission escalation are Session Bus RESULTs.
Saving a RESULT alone does not wake the originating Master. The server now
forwards that exact result as PROCESS_REPLY both on receipt and during the
periodic maintenance sweep, including after service restart.

Eligibility requires the stored server-originated escalation idempotency key,
its final result ID, matching thread/permission ID, Conductor identity, original
Master Anchor and project. Delivery resolves exactly one live MASTER for that
Anchor/project; another Master is not a replacement. An unavailable recipient
leaves the result unread with a typed recovery error.

The existing result-forward ledger records the linked notification. Repeated
sweeps do not create another notification. The existing delivery recovery path
retries an already-queued notification. A PROCESS_REPLY acknowledgment does not
generate another reply. Neither forwarding nor acknowledgment decides the Host
permission: the Master must still use host-permission with the original
Conductor result message as evidence. APPROVE and DENY use the same transport.

An already-applied decision also settles the exact forwarded notification when
the provider omits its separate receipt acknowledgment. Maintenance validates the
original server escalation, result/forward linkage, current run owner/project,
launched Host and its durable MASTER permission decision. It records
PERMISSION_DECISION_CONFIRMED, not a provider reply or Worker success. This lets
the next failure/result notice proceed without synthesizing a result. Missing or
conflicting evidence leaves the notification pending; persistence failures remain
retryable. The lookup never executes a permission decision.

Tests in `tests/test_master_permission_reply_delivery.py` cover the immediate
observer, restart recovery, periodic server sweep, single delivery, acknowledgment
without a loop, offline owner, mismatched identities and unrelated results.

## Live verification (2026-09-25)

Supported restart operation `master-permission-reply-recovery-20260925-1`
completed with service READY. Maintenance recovered the already-stored approval
`msg_96a3227868ef8971` into one notification `msg_bd51671fa5154b8a` at
08:44:41 UTC; the original Claude Master received it at 08:44:44 UTC.
Room `room_d0a25f4e5c84361de31e5da3` sequence 55 records the Master's
APPROVE for `perm_80cdd70d2ad44de9` at 08:45:10 UTC.

This verifies delivery and decision recording, not successful Worker execution.
Sequences 56-57 subsequently report `CLAUDE_PERMISSION_CANCELLED_BY_SHUTDOWN`
and `RECOVERY_TRANSPORT_FAILED`. The existing Host was alive/WAITING at the
follow-up check. Its failed attempt still needs Master-owned collection/recovery;
the Todo is not verified complete. The shutdown cause is not established by
these room events alone.

Follow-up restart `master-permission-consumption-recovery-20260925-1` completed
with service READY (PID 41888). At 09:03:42 UTC the exact approval notification
was settled with PERMISSION_DECISION_CONFIRMED referencing room decision
`msg_14111ed3ddf635a8c0e588c8`. The previously queued Worker failure notice
`msg_704c030a6dc6c3a6` was then dispatched to the same Claude Master at
09:04:26 UTC. No new approval, fabricated provider reply, or duplicate Host was
created by this recovery. The Host had already exited IDLE_TIMEOUT at 09:00:19;
failure recovery and Todo acceptance remain separate Master-owned work.
