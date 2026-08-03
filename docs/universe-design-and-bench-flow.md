# Universe Design and Bench Flow

Status: IMPLEMENTATION_DIRECTION
Scope: Universe-side design, Bench, and project handoff contract

## Purpose

Universe reduces avoidable project failure by helping a user design a complete
project before implementation, proposing appropriate routes from curated and
observed Bench data, and returning only task-relevant context and Skills to an
LLM. It is not a source executor, an authority service, or a replacement for a
Project Master.

## Product Objectives

Universe is complete only when these three objectives work together.

1. Project design: turn a user's goal into an inspectable project composition,
   including functional, implementation, document, operating, and completion
   nodes.
2. Seed-based proposal: use curated Seeds and Bench evidence to propose
   suitable stacks, routes, Skills, dependencies, and risk conditions.
3. LLM efficiency: return a bounded Context Pack and Skill Plan for the
   current node instead of repeatedly loading unrelated repository material.

## Ownership Boundaries

```text
Universe
  owns Bench aggregation, Context Pack assembly, project composition,
  selectable future routes, and cross-project comparison.

Project
  owns source, documents, tests, Task Frames, execution, and raw evidence.

Project Master
  owns implementation planning, Skill binding for Task Frames, validation,
  and completion evidence.

Career
  separately owns reusable governance adoption. Universe does not directly
  promote a Bench observation into a Career rule.
```

Universe must not mutate Project source, create execution authority, or treat
a room message, Skill Plan, or Bench result as execution permission.

## Provider Session Boundary

Universe retains one last Provider/Session coordinate per connection target.
The application target requests `CONDUCTOR`; each Project Master target
requests `MASTER`. A matching Provider Session is resumed without another Mode
greeting. A new or replaced coordinate receives that greeting once.

The coordinate is routing state only. The opened Session owns its Mode
preparation and currentness evaluation. Task Frame Boss and Worker executions
are ephemeral and never replace a target's last connection coordinate. The
application prepares the Conductor coordinate at service startup; `Call Project
Master` prepares the selected Project coordinate before entering its room.

Provider Session UI state is limited to Provider, connection state, and Mode
intent. It is not a Current Anchor, authority, Assignment, or execution
currentness statement. Task Frame results are accepted only when the dispatcher
attests `EPHEMERAL`, an `UNKNOWN` persistent Session Ref, and no persisted
Universe coordinate.

## Project Queue and Career Carrier Boundary

Project progress is useful Universe evidence, but it is not Career input by
default. A Project publishes bounded, redacted observations to its
Project-to-Universe queue. Universe consumes that queue asynchronously and
uses the observations for the connected Project's Bench, composition, and
cross-project comparison.

```text
Project Task Frame / Project Master
  -> Project-to-Universe queue
  -> Universe ingest and Bench aggregation
```

A scheduler may wake a queue consumer, but it does not define the data
relationship, create authority, or permit source work. Queue records and their
receipts remain the durable cross-Host contract.

Only a Universe-generated, reusable promotion candidate may leave this
boundary. The Career Carrier transports that candidate from Universe to Career
for Conductor review. It does not poll Projects, copy Project progress into
Career, or adopt a pattern itself.

```text
Universe aggregate or promotion candidate
  -> Universe-to-Career queue
  -> Career Carrier
  -> Career Conductor review
  -> adopted release or governance candidate
```

Candidate sharing is opt-in and redacted. The Career Carrier receives the
candidate's provenance, evidence digest, scope, redaction state, and aggregate
support or contradiction summary. It must not receive raw Project source,
prompts, secrets, worker transcripts, or unbounded execution logs.

## Memory Sync Is Not Publication

```text
MEMORY_SYNC != Candidate creation != Queue publication
```

Memory Sync preserves user-selected brainstorming, questions, observations,
and decision notes. A Project may attach such a note to a functional or
implementation node in its published Seed, while retaining it under a
Project-local memory path. The attachment improves recall and later review; it
does not alter the Seed graph, create a Bench observation, or leave the
Project.

Only a separate Master or Universe review may turn selected memory material
into a bounded candidate. That candidate then requires its own provenance,
redaction, approval, and queue receipt before Universe ingest or Career
promotion.

## Fresh Project Flow

A Fresh Project starts with minimum user intent rather than a prescribed stack.
The user supplies enough information to bound discovery: purpose, target users,
problem, key constraints, and optional desired technologies.

```text
minimum intent
  -> Universe Bench selection
  -> bounded Context Pack
  -> LLM proposal for specification, design, stack, and route
  -> user adopts or changes meaningful choices
  -> selected composition becomes the Project Seed
  -> Project Master receives the implementation handoff
```

The proposed composition includes functional capabilities and acceptance
conditions; design and UX direction; data, API, authentication, integration,
test, release, and operating nodes; technology alternatives; document
requirements; dependencies; branches; and completion conditions. A selected
route is a design coordinate, not an execution assignment.

## Bench and Context Pack

Universe is the durable Bench owner. A Project consumes a small Context Pack;
it does not maintain a separate long-lived global Bench database. A Bench entry
records source provenance and applicability rather than an unqualified
recommendation:

```text
reference and digest
technology / Skill / model version
project domain and stack conditions
observed quality, rework, duration, token or cost signals
validation and evidence references
redaction state and collection time
```

Context Pack assembly selects only entries relevant to the current project
node, proposed action, constraints, and selected stack. It must state when no
applicable evidence exists and must not invent probability or performance
claims.

## Skill Plan

The user chooses project direction, not every individual Skill. Universe and
an LLM may propose a bounded Skill Plan from the Context Pack.

```text
purpose and project node
  -> candidate Skills with contracts and Bench rationale
  -> user adopts or changes the plan
  -> Universe binds the adopted plan to Project Master planning context
  -> Project Master resolves local Skill refs and proposes Task Frame bindings
```

Skill Catalog records include `skill_id`, version, input/output contract,
applicable domains and stacks, operation class, evidence requirements, and
Bench history. A result is conditional on task type, model, context, and Host
conditions; it is never a universal ranking. Canonical Host observations encode
that pair as `provider://<PROVIDER>/model/<MODEL>` in `model_ref`; legacy model
references keep `provider_ref: UNKNOWN`. A Project-local proposal may order
compatible candidates by validated success, validation failure, successful
outcomes, observation count, and average observed duration. The scorecard is
exposed with the proposal, and every item remains `CANDIDATE_ONLY` with explicit
user selection and Project Master binding required. A Task Worker runs only
Skills bound by its Project Master and may request a replacement only as a
candidate.

Explicit handoff delivery stores the adopted plan in the resident Project
Master's file-backed session database. The stored context preserves Skill,
model, provider, operation-class, and Context Pack coordinates. It deliberately
records the incoming project-local `skill_ref` as `UNRESOLVED`, then the
Project Master resolves each Skill against exactly one installed
`.ai/skills/**/<skill_id>/SKILL.md`. The resulting passive binding proposal
contains the project-relative Skill ref and file digest. Missing or duplicate
installed refs block application. This step creates no Task Frame, authority,
assignment, repository write, or execution permission. Delivery is digest-bound
and idempotent across Universe and Project Master stores.

## Project Master Handoff

After user adoption, Universe produces a Project Seed and implementation
handoff. The Project Master decomposes the selected route into Task Frames and
performs source work through the Project Runtime and its guards.

For an already recorded Project Seed, Universe may prepare an exact five-file
Seed asset proposal for the Project Master. That proposal is read-only and
digest-bound; it cannot create `.ai/universe/`, create a write receipt, or
replace the Project Master's approval and validation path.

The Universe Project Room and Project Master Bridge are discussion and
delivery surfaces only. They do not create vendor chat sessions, execute source
changes, or turn a design selection into a repository mutation.

## Asynchronous Skill Observations

A Task Frame is the Project-side observation point. On completion it produces
one immutable, redacted `ai-career.skill-observation-candidate.v1` candidate
containing summary metadata, not raw repository material:

```text
candidate and observation identifiers
project and task-frame references
worker reference
skill and model version
context-pack digest
task kind
quality / validation / rework signals
duration, token, and cost observations when available
evidence references and redaction state
```

The Project publishes this observation asynchronously. It retains only the
Task Frame result and a queue publication receipt. Publication also carries a
Project Master approval artifact bound to the Project, candidate ID, canonical
candidate digest, selection reference, and approval evidence reference.
Universe rejects an unapproved or mismatched artifact before durable queue
storage. It then uses `project_id + candidate_id + observation_digest` with the
candidate digest for idempotency and conflict detection.

```text
Allowed candidate fields
  candidate_id
  schema, project_ref, task_frame_ref, source_ref, observed_at, target_ref
  redaction_state: REDACTED
  observation_digest, skill_binding_digest
  skill_id, skill_version, operation_class, context_pack_digest
  model_ref, outcome, validation_state, evidence_refs, bounded metrics

Rejected at the Universe boundary
  skill_ref, raw source paths or content, prompts, secrets, worker transcripts,
  repository documents, executable commands, and arbitrary extension fields
```

Universe stores accepted observations in its local ingress queue first. Its
consumer performs the later Bench database insert and exposes only observations
and aggregate counts, validation states, and metric totals. It may produce a
deterministic Project-local Skill/Model/Provider candidate order with the full
scorecard, but does not rank Skills universally or turn Bench records into
source authority.

Bench observations are shareable inputs to common learning, not automatic
Career policy. Universe may compare compatible redacted observations across
Projects and create a reusable promotion candidate only when provenance,
applicability conditions, evidence references, and redaction state remain
intact. Career adoption remains a separate Conductor decision.

An `OBSERVED_EXPERIENCE_PATTERN` Career candidate is derived only from a
recorded Universe pattern proposal and is placed on the Universe-to-Career
queue with `promotion_state: CANDIDATE_ONLY`. The queue contains redacted
aggregate support only. Career Carrier later prepares two append candidates,
for its Memory Inbox and the Conductor Inbox, but the queue record does not
write Career or make a governance decision.

The Universe application provides a local HTTP publisher for an explicitly
selected, already prepared candidate. The publisher returns a durable Universe
queue receipt but does not write a Project archive. A Project that needs an
append-only cross-Host record must perform its existing approved
`HANDOFF_APPEND` operation separately; neither receipt creates Project
authority or Task Frame execution permission.

```text
Task Worker -> Task Frame -> Project publication -> Universe ingress queue
Universe queue consumer -> Bench -> Context Pack -> next Project or next Task proposal
```

Workers never write directly to the Universe database. Universe never reads
raw source, secrets, or unredacted prompts solely to build Bench history.

## Future Routes and Experience

Universe exposes three distinct proposal surfaces:

- Structural route: missing functional, implementation, document, or contract
  connections in the current Project Seed.
- Seed route: curated cold-start routes and known conditions for a Fresh
  Project.
- Bench route: context-specific suggestions supported by prior observations.

They remain user-selectable proposals, not forecasts, authority, or automatic
source changes.

After asynchronous observations exist, Universe may add an Experience Plane:
Case records, evidence-linked events, causal candidates, and similarity
matching. Canonical causal relations remain `cause -> effect`; a Why view
traverses in reverse from an outcome to possible earlier causes. Inferred
relations remain distinct from observed evidence. Career adoption is separate.

## Implementation Sequence

1. Add Universe Bench and Skill Catalog schemas with provenance and
   applicability constraints. Initial redacted asynchronous ingest,
   idempotency, and aggregate Bench query are implemented.
2. Add Fresh Project intent, specification, design, and route proposal
   contracts. Minimum intent to read-only Official Seed route candidates is
   implemented. The local Fresh Project Wizard now exposes route selection,
   Composition review, optional Planning Frame refinement, side-by-side
   candidate comparison, and explicit adoption without creating a Project.
   A refinement request is bound to one Composition digest. Proposal creation
   is model-free; provider execution requires a process-local Runtime binding
   plus exact `proposal_id` and `plan_digest` approval. One read-only BOSS turn
   returns a strict Worker output object. Universe binds provider receipt
   coordinates, records the validated candidate, and explicit selection creates
   a revised Composition proposal. Raw Worker text is discarded before Task
   Frame or Universe persistence.
3. Assemble Context Packs and propose Skill Plans from selected routes.
   Initial Project-local Context Pack assembly, Skill Plan proposal, and
   explicit adoption record are implemented. Adopted plans can be delivered
   once into durable Project Master planning context, where installed
   project-local Skill refs are resolved into a passive binding proposal.
   Cross-project applicability remains a follow-up.
4. Add Project-side Task Frame SkillRunObservation publication as an
   ai-career/Core Runtime follow-up. The redacted candidate preparation
   surface and Boss-bound Result Packet persistence are implemented; a provider
   append adapter remains a follow-up.
5. Connect an approved Project publication provider to the Universe ingress
   queue, then use consumed Bench records for Context Pack assembly. The local
   publisher, digest-bound Project Master approval, durable queue receipt,
   deterministic queue drain, and Project-local Skill/Model/Provider candidate
   ordering are implemented; Project-owned archive retention remains a separate
   `HANDOFF_APPEND` integration.
6. Hand selected Project Seeds and Skill Plans to Project Masters through the
   existing handoff and Bridge boundaries. Exact-byte Seed asset proposal
   preparation and receipt-aware Project Master application are implemented.
   Release DB `OS_INSTALL`/`OS_UPDATE` planning, exact approval, independent
   Project Lifecycle Host application, and durable idempotent receipts are
   implemented. Selected Skill Plan delivery to Project Master planning context
   and project-local Skill ref resolution are implemented. Creating the actual
   ai-career Task Frame execution proposal remains a later Master action because
   it requires current Anchor, Session, Frame, Assignment, and Host capability
   coordinates.
7. Add Experience and causal comparison only after observed Skill data exists.
8. Add display-language localization for the Universe UI. Localization is a
   presentation-layer follow-up: canonical Mode, Role, state, schema, provider,
   and evidence identifiers remain stable English values in APIs and storage.
9. Universe Conductor and per-Project Master CLI provider settings are
   implemented as local SQLite configuration. The default is `AUTO`, with the
   current ordered preference `GROK`, then `CODEX`, then `CLAUDE`; an explicit
   provider selection never falls back silently when unavailable. Capability and
   resolution state are visible in the settings UI. Changing a Project Master
   setting stops its resident Host and applies the new selection on the next
   explicit Project Master preparation. Per-task provider selection remains part of the
   separate Task Frame proposal. Provider credentials remain in each provider
   credential store and are never persisted in Universe settings.
10. Use one Universe-owned ACP session gateway for Conductor Task Frame Workers
    and resident Project Master conversation sessions. Grok uses native ACP.
    Codex app-server callbacks are normalized to ACP updates and permission
    options. Claude uses print-mode JSON with target-scoped session resume and
    non-persistent Task Frame calls. HTTP/SSE remains the Web UI adapter and
    does not become provider authority or a Task Frame bypass.
11. Productize the desktop-first Universe UI as the canonical responsive Web
    SPA. Keep the persistent conversation surface and its + control scoped to
    Project Master, Skill, and context invocation; explicit management remains
    available through ordinary UI controls. Structured Wizards must support both
    direct field entry and Conductor/Master-assisted completion of missing values,
    with final user confirmation. Keep the custom graph behind a renderer boundary
    so Canvas can advance to WebGL without changing application state contracts.
    Package the stable Web UI later in a Tauri desktop shell with the Python
    Universe service as a sidecar, tray, and autostart; mobile and remote clients
    reuse the same responsive UI as a later delivery surface.
12. Central Host Profile resolution for Python, Git, Codex, Grok, and Claude is
    implemented. The local Universe service initializes and verifies one
    machine-local Profile, and all product Runtime callers consume it instead
    of independently searching process state. Runtime Settings exposes
    discovery, exact executable selection, and verification. Installer and tray
    packaging will set the Profile pointer and reuse this same settings surface.
13. Paired remote browser access now has a LAN dogfood slice. A separate
    fixed-origin gateway forwards the same SPA, HTTP API, and SSE streams while
    the canonical service stays loopback-only. One-time pairing, local approval,
    paired-device sessions, revocation, Settings controls, and Windows tray
    controls are implemented. The first Internet dogfood adapter now keeps the
    Gateway on PC loopback and opens a key-only, strict-host-key SSH reverse
    tunnel to one trusted server loopback port. OpenSSH resolves through the
    central Host Profile; connector status, saved configuration, UI controls,
    tray restart, fixed routing, and SSE regression coverage are included. The
    public HTTPS reverse proxy remains user-operated deployment configuration.
    OAuth, P2P, and Universe peer networking remain later adapters. The next
    network slice adds a minimal Universe Rendezvous Registry:
    each Universe registers only its stable `universe_id`, rotatable
    `remote_route_id`, public key, signed public manifest, expiring endpoint
    candidates, and presence. A mobile browser resolves a known route UUID and
    then pairs with the target Universe; users, Projects, Rooms, conversation
    content, Provider credentials, local API tokens, and Runtime receipts are
    never Registry records. Endpoint negotiation prefers LAN or safe direct
    access and falls back to an outbound tunnel or later Relay adapter. The same
    signed identity and resolution contract may later locate another Universe,
    but peer trust must not reuse a paired-browser credential. See
    `docs/universe-network-architecture.md`.

14. Separate provider execution capability from three-tier model binding.
    Host Profile retains executable, authentication availability, and transport
    capability only. Universe Binding DB independently selects Provider, Model,
    Reasoning Effort, and fallback chain for `INTERACTIVE_SESSION`,
    `TASK_FRAME_BOSS`, and `TASK_FRAME_WORKER`, with Node/Task override,
    Project default, Universe default, then Host fallback precedence.
    Master room UI lists saved Provider Sessions, allows one active selection per
    Node/Mode, and treats a Provider or Model change as a new prepared session
    while retaining prior session history.
    Normalize every provider behind one ACP client contract with capability
    negotiation: Grok uses native ACP; Codex uses a pinned `codex-acp` or
    equivalent validated app-server adapter; Claude uses a pinned
    `claude-agent-acp` adapter. Print-mode CLI remains `LEGACY_LIMITED` and must
    not claim ACP parity. Usage/quota HUD state and provider-limit rebinding
    belong to this binding layer.

## Runtime Boundary

Universe consumes the installed ai-career Skill binding and observation
contract without taking ownership of Project execution. The current contract
uses a canonical provider-bearing `model_ref`; adding an independent
`provider_ref` field remains an ai-career schema migration and is not inferred
into existing Project Runtime records.
