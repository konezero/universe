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
