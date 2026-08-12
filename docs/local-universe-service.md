# Universe Local Service

The Universe local service is an application process. It is not the ai-career
Session Boot executor and it does not create project authority or execution
assignments.

## Operating Mode contract

The application entry intent is `Mode=CONDUCTOR, Role=CONDUCTOR`.
`UNIVERSE` remains a project-local operational alias, but application startup
requests `CONDUCTOR` through the installed Mode Registry.

`MASTER` remains separate and is required for Release DB installation, update,
Mode Registry mutation, and Universe policy lifecycle changes. Neither Mode
grants authority or permission to mutate an attached project. See
`docs/universe-mode-contract.md`.

## Responsibilities

- listen only on a loopback address;
- own local Task Frame provider adapters and dispatcher under `tools/`;
- maintain the Universe project registry in SQLite;
- maintain an editable prioritized Todo work map across Universe, Project, and Node scopes;
- accept one-time project registration and later refreshes;
- retain append-only project observation events;
- queue Master-owned Project Seed discovery and verify published `.ai/universe` Seed assets;
- persist current Project Projections and missing-connection candidates;
- create read-only `.ai/universe/documents` derivation proposals;
- verify and retain immutable ai-career Release artifacts;
- create read-only Project release install/update proposals;
- queue durable Project dispatches and retain their complete event/result timeline;
- provide compact project summaries to a UI or LLM client.

Universe Runtime Host provider processes remain outside the installed project Runtime. They can return a bounded read-only Worker result, but cannot create project authority, write scope, or mutation permission. See `docs/universe-runtime-host.md`.

The Universe Conductor Room persists each user message before execution. The
local service prepares the `CONDUCTOR / CONDUCTOR` Mode, starts one owned
internal Skill Router Session Runtime, and binds its loopback endpoint in
process memory before it accepts Conductor work. Service startup also opens or
resumes the last Conductor Provider Session coordinate without sending a model
prompt. The prepared `CONDUCTOR` Registry snapshot, Current Anchor, and Mode
Boot Binding are recorded atomically in the Project Runtime database. The
executable Runtime consumes that one-use binding; it does not reselect Mode from
the Distribution Manifest default. A single service-owned queue then
delivers ordinary conversation to one resident Provider Session:

```text
Universe UI
  -> durable Conductor Room message
  -> QUEUED
  -> resident Conductor Provider Session
  -> bounded reply plus Provider Session Ref
  -> durable Conductor Room reply
```

The Conductor creates a Task Frame only when it delegates bounded work. A Task
Frame Boss or Worker opens an ephemeral Provider execution, never reads or
replaces the Conductor's last Provider Session coordinate, and does not leave a
Universe-owned durable Provider chat.

The HTTP request does not wait for the provider. The UI polls the durable room
for `PROCESSING`, `ANSWERED`, or `FAILED`. Restart recovery returns interrupted
`PROCESSING` messages to `QUEUED` and the service creates a new process-local
binding before replay. `WAITING_FOR_RUNTIME_BINDING` remains a compatibility
state for manually constructed or test servers that explicitly disable owned
Runtime startup. A failed owned Runtime startup is reported as `START_FAILED`;
messages must not wait indefinitely. Raw CLI transcripts, tokens, endpoint
credentials, and repository contents are not Conductor Room records.

The Conductor Worker returns a bounded structured reply with no UI action, one
`TODO_DRAFT`, or one `FRESH_PROJECT_DRAFT`. A Todo draft is validated against
the registered Project and the currently selected node before it is attached
to the reply. It only opens the editable Todo review dialog. The Todo is not
recorded until the user confirms the normal Todo form.

A Fresh Project draft may be partial. It only pre-fills the existing Intent
step of the Fresh Project Wizard. Missing required fields remain visibly empty,
and route search still requires the user's explicit `Find routes` action. The
draft does not search Seed routes, create or adopt a Composition, register a
Project, create a Dispatch or Task Frame execution, mutate a Project, or create
authority or Assignment.

Before each Conductor provider turn, the service asks the installed Universe
Runtime to observe the input with `commander_surface: UNIVERSE_UI`. This updates
the Mode Current Anchor observation without changing Anchor identity,
authority, or execution assignment. The Session Runtime token and endpoint
remain process-local and are never persisted to the Universe database or
browser state.

The Conductor CLI selection is local application configuration. Its persisted
value is `AUTO`, `GROK`, `CODEX`, or `CLAUDE`; absence is equivalent to `AUTO`.
`AUTO` chooses the first available provider in the configured order. An explicit
provider that is unavailable fails visibly and is never replaced after the
request has started.

Provider selection is separate from Host executable resolution. Runtime
Settings exposes the central Host Profile for Python, Git, OpenSSH, Codex, Grok,
and Claude.
The service initializes this Profile before Runtime providers or resident
Project Masters start. Discover, select, and verify operations update the same
local Profile consumed by all Runtime callers. They do not create authority,
assignment, write scope, or provider credentials.

Each attached project remains responsible for its own source mutation,
validation, Execution Guard, and evidence. Universe stores project roots and
evidence references; it does not merge or rewrite project Runtime databases.

## Product E2E scenario

One fixed product line (start → connect GCS → Master surface → seed
dispatch deliver → seed asset publish → dispatch COMPLETED → optional
Master handoff) is documented in:

```text
docs/universe-e2e-product-scenario.md
```

Scenario ID: `UNIVERSE_E2E_GCS_SEED_AND_MASTER_LINE_V1`. Use that document for
pass/fail re-runs. This file remains the API and boundary contract.

## Start the service

Foreground developer path:

```powershell
python tools/universe_server.py serve --open-ui
```

Product control path (background lifecycle):

```powershell
python tools/universe_server.py status
python tools/universe_server.py start --open-ui
python tools/universe_server.py stop
python tools/universe_server.py restart --no-open-ui
```

Windows user Start Menu / optional autostart install is documented in
`docs/universe-packaging.md`.

The process selects a free loopback port by default and writes its endpoint,
token, PID, and database location to:

```text
%LOCALAPPDATA%\Universe\server.json
```

The SQLite database defaults to:

```text
%LOCALAPPDATA%\Universe\universe.sqlite3
```

The database receives one immutable `universe_id` UUID when it is created.
Reopening the same database preserves that ID; creating another Universe
database creates another ID. The value is returned by `/health`, the service
startup result, and `server.json` so local clients can distinguish Universe
instances. It does not create authority, network identity proof, or discovery.

Use `--database`, `--state-file`, `--port`, or `--token` to override these
values. Non-loopback listen addresses are rejected.

## Network anchor projects (Universe + Career)

On service start and on `GET /v1/projects`, Universe **idempotently attaches**
built-in multiverse nodes when their roots exist next to this repository:

| project_id | default root | role |
|------------|--------------|------|
| `universe` | this repo (`tools/..`) | `UNIVERSE_HOME` |
| `career` | `UNIVERSE_CAREER_SOURCE_ROOT`, otherwise sibling `../ai-career` (or `../career`) | `CAREER_SOURCE` |

These appear in the left project rail as **Universe** / **Career**. Ordinary
product projects (GCS, etc.) still register explicitly.

Career may use `VERSION_MANIFEST.md` / `AGENTS.md` / `README.md` as identity
when `REPOSITORY_MANIFEST.md` is absent (`network_role=CAREER_SOURCE`).

## Register a project

With the service running:

```powershell
python tools/universe_server.py register `
  --project-id GCS `
  --project-root C:\workspace\GCS
```

Registration requires `REPOSITORY_MANIFEST.md`. When a Mode Registry exists,
its owner must match the supplied project ID. Project references must remain
relative to the registered project root.

Default project refs include:

```text
manifest: REPOSITORY_MANIFEST.md
mode_registry: .ai/runtime/project_instance/mode_registry.json
runtime_status: .ai/runtime/project_instance/status.md
anchor_store: .ai/runtime/anchor_store
master_inbox: .ai/inbox/MASTER
```

`master_inbox` is the Project-owned MASTER inbox directory used by approved
dispatch delivery. The default is `.ai/inbox/MASTER`. A project may register
the alternate exact path `.ai/master/inbox` when that is the Project Master
layout already in use. Universe does not create either directory.

Registering the same ID and root again refreshes metadata instead of creating
a duplicate connection.

List attached projects:

```powershell
python tools/universe_server.py list
```

## HTTP API

`GET /health` is public and returns only service readiness. Every `/v1` route
requires the local Bearer token from the state file.

```text
POST   /v1/projects/register
GET    /v1/projects
GET    /v1/todos
POST   /v1/todos
PATCH  /v1/todos/{todo_id}
DELETE /v1/todos/{todo_id}
GET    /v1/runtime/providers
GET    /v1/settings/providers
POST   /v1/settings/providers/universe
GET    /v1/settings/host-tools
POST   /v1/settings/host-tools/discover
POST   /v1/settings/host-tools/{tool}/select
POST   /v1/settings/host-tools/{tool}/verify
POST   /v1/runtime/planning-binding
GET    /v1/runtime/planning-binding
GET    /v1/projects/{project_id}
DELETE /v1/projects/{project_id}
GET    /v1/projects/{project_id}/provider-setting
POST   /v1/projects/{project_id}/provider-setting
POST   /v1/projects/{project_id}/master-session/prepare
POST   /v1/future-paths
POST   /v1/fresh-project-compositions
GET    /v1/fresh-project-compositions
POST   /v1/fresh-project-composition-adoptions
GET    /v1/fresh-project-composition-adoptions
POST   /v1/fresh-project-refinement-requests
GET    /v1/fresh-project-refinement-requests
POST   /v1/fresh-project-refinement-runs
GET    /v1/fresh-project-refinement-runs
POST   /v1/fresh-project-refinement-runs/{run_id}/execute
POST   /v1/fresh-project-refinement-candidates
GET    /v1/fresh-project-refinement-candidates
POST   /v1/fresh-project-refinement-adoptions
GET    /v1/fresh-project-refinement-adoptions
POST   /v1/projects/{project_id}/events
GET    /v1/projects/{project_id}/events
POST   /v1/projects/{project_id}/skill-observations
GET    /v1/projects/{project_id}/skill-observations
GET    /v1/bench/skills
POST   /v1/projects/{project_id}/context-packs
GET    /v1/projects/{project_id}/context-packs
POST   /v1/projects/{project_id}/skill-plan-proposals
GET    /v1/projects/{project_id}/skill-plan-proposals
POST   /v1/projects/{project_id}/skill-plan-adoptions
GET    /v1/projects/{project_id}/skill-plan-adoptions
POST   /v1/projects/{project_id}/master-handoffs
GET    /v1/projects/{project_id}/master-handoffs
POST   /v1/projects/{project_id}/master-handoffs/{handoff_id}/deliver
POST   /v1/projects/{project_id}/experience-cases
GET    /v1/projects/{project_id}/experience-cases
POST   /v1/projects/{project_id}/experience-matches
POST   /v1/projects/{project_id}/experience-pattern-proposals
GET    /v1/projects/{project_id}/experience-pattern-proposals
POST   /v1/projects/{project_id}/memories
GET    /v1/projects/{project_id}/memories
POST   /v1/projects/{project_id}/memories/link
GET    /v1/projects/{project_id}/memories/propose-links
POST   /v1/projects/{project_id}/seed
GET    /v1/projects/{project_id}/seed
GET    /v1/templates/project-seed
POST   /v1/projects/{project_id}/discovery-dispatch
POST   /v1/projects/{project_id}/sync
POST   /v1/projects/{project_id}/projection
GET    /v1/projects/{project_id}/projection
POST   /v1/projects/{project_id}/document-incorporation-proposals
GET    /v1/releases
GET    /v1/releases/{release_id}
POST   /v1/releases/import
GET    /v1/projects/{project_id}/release-proposals
POST   /v1/projects/{project_id}/release-proposals
POST   /v1/projects/{project_id}/release-proposals/apply
GET    /v1/projects/{project_id}/seed-asset-proposal
POST   /v1/projects/{project_id}/seed-asset-proposal/apply
GET    /v1/project-templates
GET    /v1/projects/{project_id}/integration-template-proposal
POST   /v1/projects/{project_id}/integration-template-proposal/apply
GET    /v1/projects/{project_id}/runtime-worker-invocations
POST   /v1/projects/{project_id}/runtime-worker-invocations
GET    /v1/projects/{project_id}/runtime-worker-results
GET    /v1/projects/{project_id}/dispatches
POST   /v1/projects/{project_id}/dispatches
GET    /v1/dispatches/{dispatch_id}
POST   /v1/dispatches/{dispatch_id}/deliver
POST   /v1/dispatches/{dispatch_id}/wake
POST   /v1/dispatches/{dispatch_id}/acknowledge
POST   /v1/dispatches/{dispatch_id}/start
POST   /v1/dispatches/{dispatch_id}/result
```

The local CLI also provides `python tools/universe_server.py work <project_root>
--project-id <project_id>` as a read-only companion. It resolves host health,
the project-local Career Runtime marker, and proposal state without registering
or changing the Project.

## Todo work map

Todo is durable planning state owned by Universe. It is not a Dispatch, Task
Frame, execution assignment, approval, or permission. Creating, editing, or
deleting a Todo never starts a provider or writes an attached project.

Each Todo has one explicit scope:

- `UNIVERSE`: no project or node coordinate;
- `PROJECT`: one attached `project_id`;
- `NODE`: one attached `project_id` plus an opaque `node_ref`.

Priority is `P0` through `P3`. State is `BACKLOG`, `READY`, `IN_PROGRESS`,
`BLOCKED`, or `DONE`. Updates use the current integer `revision`; stale updates
fail with `TODO_REVISION_CONFLICT` instead of overwriting a newer edit.

The UI can use the selected project or graph node to prefill these coordinates.
Moving work into execution remains a separate explicit Dispatch or Task Frame
operation.

An event body has this shape:

```json
{
  "event_id": "gcs-status-001",
  "event_type": "STATUS_OBSERVED",
  "payload": {
    "repository_runtime": "VERIFIED"
  }
}
```

Event IDs are idempotency keys. Repeating the same event is accepted; reusing
an ID for different content is rejected.

`POST /v1/future-paths` accepts a Fresh Project's name, project kind,
technology signals, final goal, and an optional candidate limit. It queries the
read-only Official Development Seed and returns user-selectable route
candidates. It does not require a registered repository, persist the intent,
create a Project Seed, or grant execution authority.

The local web UI exposes that boundary as a Fresh Project Wizard: Intent, Seed
Routes, Composition, optional Planning Frame refinement, comparison, and
Adoption. Proposal creation is model-free. A separate `Approve and run` action
starts one read-only provider turn only when a process-local Planning Runtime
binding exists. The returned structured candidate is shown beside the base
Composition. Adopting that revision creates another Composition proposal; the
normal Composition adoption remains a separate decision. No step creates a
repository or registers a Project. Final adoption records only a Project
Master handoff candidate.

`POST /v1/projects/{project_id}/skill-observations` accepts only a redacted
ai-career Skill observation candidate. The service rejects raw source,
prompts, commands, `skill_ref`, and arbitrary extension fields. Replaying the
same candidate is idempotent; `GET /v1/bench/skills` returns aggregate observed
counts, validation states, metric totals, and canonical Provider dimensions
without universal rankings.

## Publish a prepared Skill observation

The Universe application can publish an already prepared ai-career candidate
without becoming an ai-career Runtime dependency:

```powershell
python tools/universe_server.py publish-skill-observation `
  --project-id GCS `
  --candidate-file C:\path\to\skill-observation-prepared.json `
  --approval-file C:\path\to\project-master-publication-approval.json
```

The command reads the local service state file for its loopback endpoint and
token, accepts only `PREPARED` `SKILL_OBSERVATION` artifacts, and returns a
Universe-local SQLite queue receipt. The approval file must be
`universe.skill-observation-publication-approval.v1` with `status: APPROVED`,
`approver: PROJECT_MASTER`, and exact Project, candidate ID, and candidate
digest bindings. It neither writes the Project repository nor starts a Task
Frame. A separately scheduled or manually invoked
`drain-skill-observation-queue` consumer performs Bench ingest after the queue
write. Retaining that receipt under a Project `.ai/archive/` path is a later,
separately approved ai-career `HANDOFF_APPEND` operation; the queue receipt
itself is not handoff evidence.

To prepare a Project-owned archive step without writing the Project, use:

```powershell
python tools/universe_server.py prepare-skill-observation-archive `
  --project-id GCS `
  --receipt-file C:\path\to\universe-ingest-receipt.json `
  --selection-ref user-selection-gcs-001 `
  --archive-path .ai/archive/universe/skill-observation-gcs-001.json
```

This returns `universe.project-archive-receipt-candidate.v1`. It is only a
bounded handoff input for the Project's approved `HANDOFF_APPEND` operation.
The candidate has no provider write evidence and cannot claim that an archive
entry exists.

An Experience Case records only existing redacted Skill observations for one
Project. It starts as `case_state: OBSERVED`, `causal_state: NOT_INFERRED`, and
`pattern_state: NOT_EVALUATED`. Universe does not turn a model explanation into
a causal fact at case-record time.

`POST /v1/projects/{project_id}/experience-matches` compares one recorded Case
with other Cases from the same Project. It returns only explicit shared Skill
bindings, Skill identities, outcomes, and validation states. Its relationship
is `OBSERVED_SIMILARITY`, not causation, risk probability, or a promoted
cross-project pattern.

An Experience Pattern proposal needs at least two recorded Cases with an exact
observed signature in common. It stores support case IDs and the shared Skill,
outcome, and validation dimensions, but remains `promotion_state: PROPOSAL_ONLY`.
It never writes Career governance or adds a Task Frame constraint.

Universe may turn that recorded proposal into one redacted
`universe.career-promotion-candidate.v1` on its durable Universe-to-Career
queue. The candidate remains `CANDIDATE_ONLY`; it is read by Career Carrier and
cannot itself append a Career Inbox record or adopt a governance rule.

A Context Pack is built from a current Project Seed, selected functional node
IDs, node-linked or project-wide document references, and a bounded set of
redacted observations already recorded for that Project. It contains no raw
project file content and creates no Task Frame. A Skill Plan proposal can use
only the observations contained in that Context Pack. Explicit `ADOPTED`
selection records a handoff candidate for the Project Master, but does not bind
a Skill, start a Task Frame, create authority, or deliver a dispatch.

When the user later sends the handoff with explicit `DELIVER`, Universe first
applies the exact adopted plan to the registered resident Project Master. The
Project Master stores it in its file-backed session database as planning
context. It resolves every candidate against exactly one installed
`.ai/skills/**/<skill_id>/SKILL.md` and stores a passive binding proposal with
the project-relative ref and Skill file digest. Universe then stores the
matching application receipt and sends the visible Project Room message.
Repeating the same delivery reuses all records and does not apply the plan or
send the message again.

## Release and dispatch boundary

Release import verifies both database and manifest, then copies the immutable
pair into content-addressed Universe storage. Import and Project release
proposal creation require the explicit request coordinate `mode: MASTER`.
Proposal generation reads the attached Project only to calculate actions and
collisions. It never applies those actions.

Dispatch creation is durable and idempotent. Delivery to the Project-owned
MASTER inbox is a separate mutation and requires `{"approval": "APPROVED"}` on
that request. The explicit Deliver action in the desktop UI supplies this
value.

Inbox path contract (source of truth for delivery validation):

```text
default / canonical template:  .ai/inbox/MASTER
allowed under prefix:          .ai/inbox/<name>
project-owned alternate:       .ai/master/inbox   (exact path only)
```

Delivery writes through the registered project ref `master_inbox`, not a
hard-coded string. Seed discovery dispatches store that same registered
`inbox_ref` on the envelope so the durable request matches the Project layout.
The Project must already expose the registered inbox directory; Universe does
not create it. Paths outside the allow-list, path escape (`..`), absolute
paths, and symlink hops are rejected. Wake adapters record receipts but do not
advance dispatch status.

Room conversation and MASTER inbox dispatch remain separate surfaces. Ordinary
Project Room / Bridge conversation does not create a dispatch inbox file.
Approved dispatch delivery is the only path that writes a
`dispatch_<id>.json` envelope into the registered MASTER inbox.

The ordered lifecycle is:

```text
QUEUED -> DELIVERED -> ACKNOWLEDGED -> STARTED -> COMPLETED | BLOCKED
```

Every compare-and-set transition and event append occurs in one SQLite
transaction. A stale concurrent transition returns `DISPATCH_STATE_CHANGED`
and appends no event. The desktop UI shows release proposal state, collisions,
dispatch evidence, wake receipts, and the final Result Packet.

## Runtime Host invocation timeline

A Runtime Host invocation is a synchronous, read-only bridge to an already
active Project Task Frame. The request must declare `repository_write_scope:
NONE` with empty mutation operations and targets. Universe records only redacted
metadata and receipt references. It never stores a Task Frame endpoint, token,
Context Pack body, or Worker response body.

A Fresh Project refinement request is different: the Wizard first prepares a
composition-bound `universe.fresh-project-refinement-request.v1` and exact
`universe.fresh-project-refinement-worker-output.v1` contract. This step does
not call a provider. A process-local Runtime binding is then required to create
one durable `universe.fresh-project-refinement-run.v1` proposal. Exact user
approval starts its single read-only BOSS turn. The dispatcher parses provider
text into the Worker output object before the Task Frame journal sees it.
Universe supplies the bound request, Composition, provider, Worker, and receipt
coordinates, validates the resulting
`universe.fresh-project-refinement-candidate.v1`, and stores only that
structured candidate. Raw Worker text is never persisted.

## Desktop UI

The UI is served by the same loopback process and contains no embedded access
token. Loopback UI use does not need an access-token prompt. Remote access is
not part of this service contract.

The first slice supports:

- explicit local project connection;
- Functional, Implementation, Documents, and Future graph views;
- Project Seed preparation Dispatch creation and automatic asset sync on refresh;
- immutable Release DB import in MASTER;
- read-only Project install/update planning with collision visibility;
- durable MASTER dispatch creation and approved Inbox delivery;
- dispatch event and Result Packet inspection.

## Project Master Bridge

Product multi-room architecture (Project room, Boss room, Meeting room,
dashboard, session inject, continuity bridge) is specified in
`docs/multi-room-chat-architecture.md`. Project Master session attach and
streaming mirror for the Project room are detailed in
`docs/room-session-attach-streaming.md`.

A Project Room is durable Universe-side conversation history. It is not, by
itself, a vendor chat session. A project may register one local Project Master
Bridge to carry room messages to a separately running Project Host:

```text
Universe Project Room
  -> registered loopback Project Master Bridge
  -> Project Host / opaque Master session reference
  -> authenticated reply callback
  -> Universe Project Room
```

The local application also supplies a resident Host route. Selecting `Call
Project Master` explicitly prepares that Project's `MASTER` Provider Session,
registers its loopback Bridge, and keeps the Host resident until the Universe
service stops. Later messages reuse the same last Provider Session coordinate
and durable Host queue. Merely selecting a Project graph does not start its
Host.

Each Project Master has an independent persisted CLI selection with the same
`AUTO`, `GROK`, `CODEX`, and `CLAUDE` values. A changed selection closes the existing
resident Host; the next explicit Project Master preparation opens the selected
Provider. Provider session state is target-scoped and contains exactly one
`last_provider` plus
`last_session_ref`. A Provider change replaces that coordinate; Universe does
not retain parallel provider session maps.

On open, Universe supplies that last coordinate and records the Provider and
Session Ref actually opened. An exact match resumes without a greeting. A
missing, changed-Provider, or changed-Session coordinate receives the requested
Mode greeting once (`CONDUCTOR` for application entry and `MASTER` for a Project
Master connection). The opened Session performs its own Mode preparation and
currentness checks; Universe does not judge either.

The UI exposes only the selected Provider, connection state (`NOT_OPENED`,
`NEW`, `REUSED`, `REPLACED`, or `UNAVAILABLE`), and requested Mode intent. It
does not present these fields as Current Anchor, authority, Assignment, or
execution evidence.

### Universe ACP Gateway

Universe owns the agent-session boundary. The browser is an HTTP/SSE client of
that boundary; it does not connect to a provider CLI directly.

```text
Universe UI
  -> Universe ACP Gateway
     -> resident Universe Conductor session
     -> resident Project Master session
     -> ephemeral Task Frame Boss or Worker execution
     -> Grok ACP over JSON-RPC stdio
     -> Codex app-server adapter normalized to ACP events
     -> Claude Code print-mode JSON adapter
```

The implemented common session vocabulary is `session/new` or Provider resume,
`session/prompt`, `session/update`, and `session/request_permission`. Universe
Conductor and Project Master sessions remain resident for conversation
continuity. Task Frame Provider executions use the same Gateway but remain
ephemeral. ACP is the client/session protocol and does not bypass Task Frame,
Mode, authority, or execution-assignment rules.

`EPHEMERAL` means Universe does not retain a connection coordinate for that
execution. Codex also receives `thread/start.ephemeral: true`; Claude receives
`--no-session-persistence`. Grok ACP does not currently attest Provider-side
durable chat cleanup, so that Provider storage state remains `UNKNOWN`.

The Worker dispatcher must return `session_persistence: EPHEMERAL`,
`persistent_session_ref: UNKNOWN`, and
`universe_coordinate_persisted: false`. The Runtime Host rejects the Task Frame
result before exposing it when any field is absent or different. This applies
equally to Boss and Worker turns.

Grok runs through its native ACP `agent stdio` interface. Codex currently
exposes approval callbacks through `app-server`; the adapter maps those
callbacks into the same ACP permission option contract. Provider-specific
request or decision values are preserved inside the adapter and do not leak
into the browser API.

Claude uses one-shot JSON responses. Resident Conductor and Project Master
calls resume the target's single last Claude `session_id`; Task Frame calls
disable tools and session persistence and never publish a Universe coordinate.

Universe pins both provider sessions to request approval. Grok starts with
`--permission-mode default`, so a user-level `always-approve` setting is not
inherited by the Universe-owned ACP session. Codex starts with
`approvalPolicy: on-request`. The Project Room reports this session-effective
auto-approve state rather than the provider's unrelated interactive CLI
default.

Permission requests are stored in the Universe database and published through
the Project Room SSE stream. A Project Room displays only options supplied by
the active provider session. The selected option is delivered back to that
exact resident session and request ID. A missing resident session, unknown
option, repeated conflicting decision, or stale request is rejected. An ACP
permission decision is not ai-career authority, an Execution Assignment, or a
repository mutation receipt.

### Governance Task Proposal Approval

Provider permission and governance approval are separate Project Room
surfaces. Provider permission cards remain bound to one resident provider
session and tool request. Governance cards are read from the installed
Project Runtime's durable `task-proposals.sqlite3` journal and display the
Proposal ID, digest, boundary, scope, and state. Reloading Universe or opening
the Project Room again restores pending governance cards from that journal.
The Conductor conversation is the cross-Project approval inbox: it lists every
pending attached-Project Proposal, includes the owning Project on each card,
counts them in the conversation badge, and marks each Project rail item with
its pending approval count.

The card button and an exact short approval command (`승인`, `진행`, or `고고`,
including their short imperative forms) call the same digest-bound decision
API. Natural-language approval is accepted only when the active Project has
exactly one pending Proposal. With multiple pending Proposals, Universe
approves none and requires card selection. With no pending Proposal, the text
remains an ordinary Project Master message. A failed approval request is
consumed and reported rather than falling through as free-form chat.

Universe records the decision and its durable evidence reference before it
invokes the installed Runtime's `task-proposal approve` command. It then sends
one structured approval packet to the same resident Project Master room. This
decision cannot resolve an ACP permission request, and an ACP permission choice
cannot approve a governance Proposal. Execution Guard receipts remain internal
and do not create an additional user confirmation within the exact unchanged
approved scope.

The current web service is the UI transport adapter for the Universe-owned ACP
Gateway. It is not advertised as a general external ACP stdio or socket endpoint
and does not claim an unimplemented `session/cancel` surface in this slice.

Before registration, the resident Host invokes the installed project's
`prepare-session` command for registered `MASTER` Mode from inside the project
root. The Project Runtime Host records the Registry snapshot, MASTER Current
Anchor, and one-use Mode Boot Binding in the Project-owned Runtime database.
Universe passes only the opaque binding ID to Session Boot and does not open or
rewrite that database itself.
For every accepted Project Room user message, the Project Host invokes:

```text
mode-anchor observe-commander-input
  -> commander_surface: UNIVERSE_UI
  -> evidence_ref: universe://project-room/messages/<message-id>
```

Only `coordinates.commander_surface` and `observed_at` may change. Mode,
Anchor identity, execution coordinates, authority, and assignment remain
project-owned and unchanged. A failed preparation or Commander Surface
observation blocks the provider turn.

Project Master response text is emitted as process-local stream events and the
browser subscribes through:

```text
GET /v1/projects/<project_id>/room/stream
```

The stream uses Server-Sent Events. Partial text is transient UI state; only
the completed Project Master reply is appended to durable Project Room history.
Client disconnect or stream loss therefore does not change the authoritative
conversation record.

The binding is registered through:

```text
POST /v1/projects/<project_id>/master-bridge
```

with a literal loopback HTTP origin, an opaque `master_session_ref`, a
`binding_evidence_ref`, and the name of an uppercase environment variable that
contains the bridge credential. Universe stores the environment-variable name,
not the credential. The adapter sends a room envelope to:

```text
POST <bridge-origin>/v1/project-master/messages
Authorization: Bearer <credential>
```

The Project Host replies through:

```text
POST /v1/projects/<project_id>/master-bridge/replies
X-Universe-Bridge-Token: <credential>
```

The reply must name the registered `bridge_id` and the room message it answers.
It appends a `PROJECT_MASTER` message only; it cannot create execution
authority, write source files, start a Task Frame, or control a vendor chat
application.

When no bridge is registered, the original room message remains durable in the
Universe room database with `RECORDED`. If a registered local Host cannot
accept the message, it remains durable with `DELIVERY_FAILED` and the failure
reason. Neither state creates a Project Inbox file. Existing Master Inbox
dispatch remains a separate, explicit operation.

### Project Master Bridge Host

`tools/project_master_bridge.py` supplies the authenticated loopback transport.
The resident Project Master Host accepts ordinary conversation at
`/v1/project-master/messages`, queues it in the local session store, and returns
`repository_write: false`; it does not copy room messages into the Project
Inbox as a dispatch envelope. A standalone receiver rejects that conversation
route. Its separate `/v1/project-master/inbox-dispatches` route is reserved for
an explicitly selected asynchronous Inbox operation and uses the same Project
`master_inbox` allow-list (`.ai/inbox/*` or exact `.ai/master/inbox`).

Set one shared credential in the same local environment as both services, then
start the receiver for the attached project:

```powershell
$env:UNIVERSE_GCS_MASTER_BRIDGE_TOKEN = "<local-secret>"
python tools/project_master_bridge.py serve `
  --project-root C:\workspace\GCS `
  --token-env UNIVERSE_GCS_MASTER_BRIDGE_TOKEN
```

Do not register this standalone Inbox receiver as a live Project Master bridge.
The live bridge is owned by the resident Project Master Host. The opaque
`master_session_ref` belongs to that Host integration; it is an identifier
only, not proof that a chat session is live or authorized.

The separately running Master reads the recorded envelope and replies only by
an explicit callback. The reply client keeps the credential out of command
history by reading it from an environment variable:

```powershell
python tools/project_master_bridge.py reply `
  --universe-endpoint http://127.0.0.1:<universe-port> `
  --project-id GCS `
  --bridge-id <registered-bridge-id> `
  --in-reply-to <room-message-id> `
  --kind STATUS `
  --body "Master received the question." `
  --idempotency-key <stable-reply-key> `
  --token-env UNIVERSE_GCS_MASTER_BRIDGE_TOKEN
```

Neither receipt starts Task Frames, grants Execution Guard permission, or
executes a room instruction. Those remain Project Master decisions through the
Project Runtime.

Release proposal creation never applies Project files. After explicit approval,
the apply route passes the exact proposal to the independent local Project
Lifecycle Host. It rebuilds a provider-attested source bundle from the imported
Release DB, executes ai-career's `OS_INSTALL` or `OS_UPDATE` Host lifecycle, and
accepts completion only with matching source/target evidence, `VERIFIED`
validate/status, and `READY_FOR_BOOT`. The application receipt is durable and
idempotent. The browser does not receive a filesystem write primitive.

The lifecycle Host does not depend on an already installed resident Project
Master. If one is active during `OS_UPDATE`, Universe stops it first and
reconnects it after successful lifecycle completion.

## Project Seed and Projection

Universe queues a Master-owned, read-only discovery request. The Project Master
then publishes the canonical Seed bundle under `.ai/universe/`: a manifest,
functional graph, implementation graph, functional-to-implementation bindings,
and document catalog. Universe verifies the manifest and its file digests,
reconstructs the Seed, and stores no raw project source content.

For a recorded current Seed, Universe can return a read-only
`universe.project-seed-asset-proposal.v1` containing the exact five target
paths, encoded bytes, and per-asset SHA-256 values. The apply route accepts only
`APPROVED` plus that current proposal ID and digest. Universe forwards the
unchanged proposal and a Host-observed approval reference to the resident
Project Master; Universe never writes the Project itself.

The Project Host lazily starts the installed executable Runtime, creates one
exact Execution Binding per changed asset, obtains and consumes one Guard
receipt through `mutation-gateway apply-file`, writes `manifest.json` last, and
then validates every published digest. A repeated apply with identical bytes
is read-only. The installed Project must already expose a real
`.ai/universe/` Runtime-state root; the Host does not create that directory
through raw filesystem access.

## Project integration catalog

Universe owns the versioned project-integration catalog. `GET
/v1/project-templates` exposes its template digests without addressing a
Project. `GET /v1/projects/{project_id}/integration-template-proposal` creates
the exact `universe.project-integration-proposal.v1` for one registered
Project, without storing or writing it.

The proposal contains one tracked Project-source asset,
`.universe/project.json`, and four local Runtime assets under `.ai/`. The
apply route accepts only `APPROVED` with the current proposal ID and digest.
Universe creates one exact approval evidence reference, then passes the
unchanged proposal and scope-specific approval fields to the resident Project
Master. The Master uses the existing receipt-aware mutation gateway for every
changed file, verifies all output digests, and returns an idempotent receipt.

An integration apply requires an installed Career Project Runtime and a
reachable resident Project Master. For a Fresh Project, the approved Release
proposal and Project Lifecycle Host complete `OS_INSTALL` first; this route
never creates a substitute `.ai/` Runtime or bypasses the Career lifecycle.

Building a Projection returns the current node/edge/document map, structural
gaps, and user-selectable predicted paths. The UI places component documents
next to their linked system nodes and Project-wide documents next to the main
Project node. Project summaries and working-reference rules are shown in the
main-node inspector. A newer Project Seed invalidates the prior current
Projection until it is rebuilt.

The document-incorporation endpoint returns a proposal for the Project-owned
`.ai/universe/documents/` hierarchy. It never creates a directory or moves a
legacy document. The Project Master must approve and execute canonical document
derivation through its own Runtime.
See `docs/project-projection-contract.md`.

## Connection and authentication boundary

The complete local and paired remote-access topology is defined in
`docs/universe-network-architecture.md`. Remote mobile access forwards this
existing Web UI, HTTP API, and SSE service; it does not create a second mobile
backend or command protocol.

The local service exposes a connection profile instead of making callers depend
on a hard-coded local endpoint. The current implementation provides exactly one
active combination:

```yaml
interface_kind: HTTP_API
connection_kind: LOCAL
transport_kind: HTTP
auth_type: NONE
credential_ref: NONE
capabilities:
  read: true
  append: true
  realtime: true
  bidirectional: true
  durable: true
```

The axes remain independent:

- interface: `HTTP_API`, with `MCP` and `CLI` reserved
- connection: `LOCAL`, with `REMOTE` and `PEER` reserved
- transport: `HTTP`, with `GIT` and `P2P` reserved
- authentication: `NONE` for loopback local HTTP, with `DEVICE_PAIRING` reserved for paired remote browser access and `OAUTH2` and `PEER_KEY` reserved for future account or peer adapters

Only HTTP addresses are currently validated as HTTP URLs. Git and P2P address
formats remain Adapter-owned so this initial contract does not force future
connections into an HTTP-specific shape.

MCP is an interface contract, not a transport. A future MCP server adapter may
expose Universe operations to an LLM, while an MCP client adapter may invoke an
external project interface over whichever network transport that connection
selects.

Reserved values do not start remote synchronization, device pairing, OAuth
flows, peer discovery, key exchange, Git exchange, or MCP tools. Requesting an unimplemented
authentication provider fails explicitly with `AUTH_PROVIDER_NOT_IMPLEMENTED`.

The local service binds only to a literal loopback address and accepts local
HTTP requests without a browser or API token. That exemption applies only to
this local connection profile; remote and peer adapters must declare their own
credential reference and authentication provider when implemented.

## Next boundary

OS_INSTALL integration should create a connection candidate and display it to
the user. Only the approved registration request is sent to this service.
Installation must not silently attach every discovered project.
`POST /v1/fresh-project-compositions` turns one selected official Seed route
into a Fresh Project Composition proposal: functional nodes, initial
specification and document plan, technology signals, and known route risks.
It does not create a project, write project files, bind a Master, create a
Task Frame, or grant authority. `POST /v1/fresh-project-composition-adoptions`
requires explicit `ADOPTED` selection and produces only a later Project Master
handoff candidate.

The current Wizard uses deterministic Official Seed output. It prepares a
structured refinement request and an exact provider execution proposal before
showing `Approve and run`; only that second action starts a model. The
process-local Runtime binding is redacted from the browser and absent from
SQLite. A completed Planning Frame returns one structured refinement object
plus bounded provider receipt metadata. The UI compares it with the base
Composition. Explicit refinement `ADOPTED` selection creates a new Fresh
Project Composition proposal; it still needs the normal explicit Composition
adoption before any Project Master handoff.

A Project Master handoff only accepts an already adopted Fresh Project
Composition or project-local Skill Plan. It is recorded as `PROPOSAL_ONLY`.
An explicit `DELIVER` request is required before Universe writes a room message
to the registered Master bridge. For a Skill Plan, the same request first binds
the exact adopted plan to durable Project Master planning context. Delivery
creates neither a Task Frame nor project write authority; the Project Master
must use current Runtime coordinates and explicit approval for any later Task
Frame proposal.
