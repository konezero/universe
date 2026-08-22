# Universe Supervisor Session Topology Audit

**Status:** audit and remediation design only. No production behavior is changed by this document.

## Decision

`node` and `mode` identify an **Anchor location**.  They do not identify a
provider conversation.  A Supervisor session is a durable, provider-scoped
conversation identity.  An Anchor selects one such session as its default; it
does not collapse every provider conversation at that location into one record.

The currently running implementation violates that separation.  Its injector
derives a Supervisor ID from only `(node, mode)`, then treats provider changes
as a rebind of the same record.  Consequently, a CODEX and a CLAUDE session
opened for `universe / MASTER` overwrite each other's current provider binding.

## Functional node map

```text
N0 Mode Registry
  └─ validates that a Mode exists; no session identity

N1 Mode Current Anchor: (project_id, node, mode)
  ├─ owns currentness of the location
  └─ points to one selected Supervisor session (optional default)

N2 Supervisor Session: (provider, provider_session_ref)
  ├─ immutable session identity and lifetime record
  ├─ may have one or more historical locations
  └─ never uses node/mode as its primary identity

N3 Provider Binding
  └─ links a Supervisor session to its provider coordinate; historical only
     when the same durable provider session itself changes coordinates

N4 Anchor Default Pointer: (node, mode) -> supervisor_session_id
  └─ selects a current session without deleting or rebinding peer sessions

N5 Runtime Activity and Process Lease
  └─ says whether a particular Supervisor session is observed/live; no identity

N6 Multi-room Slot
  └─ attaches one or more Supervisor session IDs to a room role, independently
     of N4's selected default

N7 SessionStart / MODE_CHANGE Injector
  ├─ resolves provider + provider_session_ref
  ├─ upserts N2 by provider identity
  ├─ records an N3 binding only for that N2 record
  ├─ optionally moves N4 by explicit policy
  └─ attaches N6 without requiring Session Observatory

N8 Session Observatory
  └─ is the management/history surface for N2, N3, N4, N5, and N6

N9 Node-mode rail
  └─ shows N0/N1 summary and selected-default status only; it does not render
     persistent-session history cards
```

## Required identity and pointer model

| Concern | Key | Cardinality | Must not do |
| --- | --- | --- | --- |
| Mode Current Anchor | `(project_id, node, mode)` | one current location per Mode | represent a provider thread |
| Supervisor Session | `(provider, provider_session_ref)` | one per provider conversation | derive identity from `node/mode` |
| Provider Binding History | `binding_id -> supervisor_session_id` | many bindings per session only when that session changes binding | use it to merge different providers |
| Default selection | `(node, mode) -> supervisor_session_id` | zero or one selected session | mutate the selected session's provider to switch defaults |
| Room attachment | `(room_id, slot_role, supervisor_session_id)` | one or many, per room policy | silently replace unrelated provider sessions |
| Process lease/activity | `supervisor_session_id` | zero or one active lease per session/process policy | establish session identity or Anchor currentness |

The desired relationships are:

```text
Anchor universe/MASTER
  └─ default -> SupervisorSession CODEX/01a029…

SupervisorSession CLAUDE/<claude-ref>
  └─ historical/current location includes universe/MASTER

Both records may be LIVE at the same time.
Changing the Anchor default changes only the pointer.
```

## Evidence from the current implementation

### F1 — session identity is location-scoped, not provider-scoped (P0)

`tools/universe_server.py` function `supervisor_session_id_for()` accepts
`provider` and `provider_session_ref`, immediately discards both, and hashes
only `node` and `mode`.  The injector then uses that ID for
`SessionSupervisorStore.register_session()`.

This is the direct collision point: CODEX and CLAUDE injected into the same
node/mode resolve to the same `session_*` record.

**Required correction:** derive an immutable Supervisor session ID from the
normalized provider identity, for example
`sha256({provider, provider_session_ref})`; retain `node/mode` as a location
record and use the default pointer to associate an Anchor with the session.

### F2 — rebind turns distinct conversations into one session (P0)

`tools/session_supervisor.py` method `register_session()` detects a changed
provider/ref for an existing `session_id`, appends a new binding history entry,
and updates the record's current `provider` and `provider_session_ref`.  With
F1 present this behavior causes cross-provider replacement rather than a
legitimate binding update.

**Required correction:** an existing `(provider, provider_session_ref)` must
resolve to its own Supervisor record.  A provider mismatch for a requested
session ID must be an identity conflict, not `PROVIDER_SESSION_REBOUND`.

### F3 — default selection has the right job but receives a merged record (P0)

`target_default_session` is keyed by `node` and `mode`, which is appropriate
for N4.  The injector's live-default guard is also directionally correct: a
new arrival should not blindly replace a live default.  It cannot work as
intended while every provider session for that location is represented by the
same session ID.

**Required correction:** keep this pointer keyed by node/mode, but point it to
separate N2 rows.  Define policy explicitly: `make_default=true` may move N4;
ordinary observation must not.

### F4 — public UI data hides the discriminator it needs (P1)

The public Supervisor session response exposes the current provider but not
the provider session reference or binding history.  The UI therefore cannot
reliably distinguish two provider sessions at the same location or present
their relationship without a dedicated safe session descriptor.

**Required correction:** expose a redacted, stable session identity and a
provider label for each N2 record.  Do not expose secrets or raw provider
transcripts merely to render session management UI.

### F5 — node-mode rail deliberately renders persistent history (P1)

`tools/universe_ui/app.js` function `renderNodeModeSessionCards()` collects
and orders persistent sessions.  The node-mode render path calls it for a
selected Mode with `liveOnly: false`, deliberately expanding the complete
Anchor session lineage.

This is why old cards are visible.  It is a presentation policy, separate
from the F1/F2 identity defect.

**Required correction:** N9 should show only Anchor/default and compact live
state.  Move session creation, history, archival, and multi-provider choice
to N8 Session Observatory.

### F6 — injector success is not durable diagnostics (P1)

The SessionStart hook can locally patch the Anchor observation even when the
Universe service is offline.  Provider hook stdout is intentionally silent,
so a user cannot later distinguish `INJECTED`, `OFFLINE`, and
`INJECT_FAILED` from the local observation alone.

**Required correction:** write a redacted, bounded injector receipt with
outcome, effective Supervisor session ID, provider identity digest, default
pointer decision, room-attach decision, and failure code when applicable.

## Target injection transaction

For an incoming `CODEX / <thread-id>` at `universe / MASTER`:

1. Normalize the provider and session ref; reject an absent ref unless the
   explicitly supported provisional-session policy applies.
2. Upsert N2 using `(CODEX, <thread-id>)`.  Never locate a record by
   `(node, mode)` for this purpose.
3. Append or refresh N3 only on that N2 row.
4. Record N2's location association with N1; preserve other N2 rows that use
   the same location.
5. Evaluate explicit default policy and, only if selected, update N4 using its
   versioned compare-and-set pointer.
6. Attach N2 to the requested N6 room slot without removing peers unless the
   API requested an exclusive slot and supplied that authorization.
7. Return and persist a redacted receipt.  The hook remains non-blocking, but
   its durable diagnostic result is queryable in Session Observatory.

## Migration and compatibility plan

1. **Freeze new cross-provider rebinds.** Add an invariant check before any
   migration so a provider/ref mismatch cannot overwrite a current record.
2. **Add provider-scoped canonical IDs.** Introduce a canonical N2 identity
   column/index and backfill from existing provider/ref data.  Preserve legacy
   `session_id` as an alias only where unambiguous.
3. **Split collided records.** For each old location-keyed record with binding
   history, create one N2 record per distinct provider/ref pair; attach
   locations and room references to every applicable record.
4. **Repoint N4 deliberately.** Keep the currently selected provider as the
   default only when current evidence proves it; otherwise leave the pointer
   `UNSELECTED` rather than guessing.
5. **Version the API.** Keep legacy list/read routes compatible, add an
   explicit session-topology response for Observatory, and update the UI only
   after the data model is available.
6. **Remove legacy rebind behavior.** Treat it as a same-session binding
   update only under a provider-specific, documented continuity rule.

## Acceptance tests

| Scenario | Expected result |
| --- | --- |
| CODEX and CLAUDE opened at `universe / MASTER` | two Supervisor session IDs; neither provider/ref changes the other |
| Both sessions live | two independent activity/lease states; one optional N4 default |
| CODEX SessionStart re-runs | idempotently refreshes the same CODEX N2 row |
| Anchor default changes from CODEX to CLAUDE | only N4 pointer/version changes; both N2 rows and their histories remain |
| Room receives both provider sessions | N6 reflects both attachments according to room policy |
| Node-mode rail selected | no persistent session cards rendered |
| Session Observatory | lists both provider sessions, their safe identifiers, state, default marker, and history |
| Service offline at hook time | local observation may update, durable injector receipt reports `OFFLINE`; no false Supervisor claim |

## Recommended work order

1. Add regression tests for F1/F2 before changing the data model.
2. Implement canonical provider-scoped N2 identity and the N4 pointer-only
   switch semantics.
3. Add a migration preview/report; do not auto-merge ambiguous historic rows.
4. Update injection and room attachment contracts plus durable receipts.
5. Add the safe topology API and Observatory presentation.
6. Remove persistent cards from the node-mode rail and validate both views.

## Non-goals

- This audit does not infer execution authority from an Anchor, a Supervisor
  session, a provider binding, or a live process.
- It does not import provider transcripts.
- It does not choose a default provider policy for the user; it requires that
  policy to be explicit and versioned.
