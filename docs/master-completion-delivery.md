# Master completion delivery

A Master calls `/v1/master-messages/{id}/complete` with `provider`, `body_text`
(actual result summary), and optional `result_ref`. The claimed provider must match.
An identical completion request is replayed without changes; changed content conflicts.

For work with `metadata.reply_anchor_ref`, DONE and a completion result envelope
are committed in the same Master record. A missing summary is explicitly labeled;
a result reference is not treated as a summary. Historical DONE records are not
backfilled or redispatched automatically.

The server publishes one deterministic Session Bus RESULT per Master id. Its
Conductor loop retries publication after restart or storage failure. Publication
works while the recipient is offline and addresses its Anchor, not a stale terminal.
The existing RESULT -> PROCESS_REPLY -> Supervisor -> Host -> provider path handles
receipt. Completion of PROCESS_REPLY consumes receipt without generating a reply loop.
Stop hooks report lifecycle only. They do not carry completion bodies.

Master completion, Bus persistence, Host queue acceptance and provider turn start
remain separate evidence. The completion HTTP response includes result_delivery
errors; DONE does not claim delivery success. No source or work replay is used to
repair delivery. Do not send an additional coordination result for the same completion.

A MASTER_QUEUE_WAKE is only a request to claim queued work. The periodic Bus
recovery closes that notification once the exact project/provider/owner Anchor
has a durable claim at or after the wake. This runs even when no next message
arrives. It does not mark the claimed work DONE or synthesize a provider result.


## Claude quota wait and continuation (2026-09-15)

Interactive Claude quota is observed from its structured assistant API-error
transcript, not inferred from Host IDLE or the absence of a reply. The account
strip reports EXHAUSTED and the explicit reset clock without inventing usage
percentages. Recovery resolves the live Supervisor provider session and reads
only that session's transcript. System warning notices remain display-only.

A channel STARTED work item with a later quota error retains its original Bus
message, body and ownership in durable `lifecycle.quota_wait=WAITING_QUOTA`.
Only an explicit session reset clock with an IANA zone permits automatic retry;
unknown reset formats remain waiting. At reset + 5 seconds and authoritative
Host IDLE with no active input, recovery requeues that original item once per
observed quota error. This deadline permits an attempt; it does not prove the
provider has remaining quota. A new quota error returns the item to waiting.

Each retry has a durable distinct channel message id because the Rust Host
correctly deduplicates already seen ids. The original Bus id remains the work
identity. Channel callbacks and restart reconciliation map the attempt result
back to the original work; stale attempt results are rejected under the Bus
lock. No PTY input, Enter writes, replacement work, provider transfer or new
agent is involved. Completed work is not resumed. Web recovery and its existing
bounded delivery backoff own scheduling, including after Web restart.

Verification: exact CLI transcript format/reset conversion, chat false-positive
rejection, unknown reset handling, real SQLite wait/reopen/retry-once, busy Host,
repeated exhaustion, late result rejection, server session correlation,
production dispatch mapping and result reconciliation are covered by isolated
Python tests. Terminal injection UI shows quota waiting and retains the body.
Actual provider acceptance after the future reset remains unobserved until it
occurs; deterministic deadline tests are not an actual Claude quota reset.
