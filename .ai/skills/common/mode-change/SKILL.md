---
name: mode-change
description: Route an explicit Mode change through installed frame and Core transition surfaces.
---

# Mode Change Invocation

Invocation class: `GOVERNANCE_ROUTER`

Host transition action: `HOST_DEPENDENT`

Natural-language Mode intent such as `MASTER mode` or `Conductor mode` is the
user-facing entry. The Host routes that intent internally through
`MODE_CHANGE`, which first loads
`.ai/runtime/project_instance/mode_registry.json`. The requested Mode must be
registered, and its Role, Scope, and Mode Profile come from that entry rather
than from caller inference.

```text
safe token + missing Registry entry
  -> MODE_NOT_REGISTERED
  -> stop

selected Mode + missing Registry evidence
  -> MODE_REGISTRY_UNAVAILABLE
  -> stop

caller Role / Scope / Profile mismatch
  -> MODE_REGISTRY_PROFILE_MISMATCH
```

After `MODE_NOT_REGISTERED`, do not prepare a session, access a Current Anchor,
make Runtime decisions, or suggest that Host evidence can make the Mode valid.

The ai-career Registry is immutable and contains only `CONDUCTOR` and
`CARRIER`. An installed project Registry is `MASTER_MANAGED` and contains
`MASTER` as its non-deletable root Mode.

After Registry resolution, `MODE_CHANGE` loads the source-backed Mode, Role,
and Scope context.
It does not grant authority or start an executable Runtime merely because the
Host is local.

The Host records `mode_context_active` after the Registry resolves the requested
Mode/Role/Scope/Profile and the corresponding source-backed context is loaded.
A Host session reference may be recorded as optional observation provenance;
it is not a Mode-entry precondition. `SOURCE_READY` proves readable source only;
it is not preparation evidence by itself. This is governance context, not
executor activity, Runtime currentness, or execution authority.

## Targets

Resolve the request through these existing surfaces:

```text
.ai/core/SESSION_FRAMEWORK.md
.ai/core/ROLE_MODE_AUTHORITY_GATE.md
.ai/core/NODE_MODE_COORDINATE_CONTRACT.md
.ai/core/MODE_REGISTRY.md
.ai/core/SESSION_CURRENTNESS.md
.ai/runtime/project_instance/mode_registry.json
.ai/runtime/project_instance/role_selection_gate.md when installed
.ai/runtime/project_instance/runtime_anchor_frame.md when installed
.ai/runtime/state/session.md when installed
.ai/runtime/state/current_anchor_frame.md when installed
```

The Host evaluates the transition through `prepare-session --request` before
any optional executable Runtime start. Source-only Hosts may return an active
Mode context while executable Runtime identifiers, endpoint, and Runtime
currentness remain `UNKNOWN`. An `EXECUTION_HOST_START_REQUIRED` decision is
separate and must be satisfied through the existing Session Boot executor.

This is common to every Mode. A Mode change may rehydrate governance context
from a source-backed Anchor Snapshot. Verified rehydration reports
`session_preparation_state: REHYDRATED`. After Mode/Role/Scope resolve, a bound
Mode Anchor store is opened for that selected Mode only. It returns the existing
Current Anchor and advances its `observed_at`, or creates that Mode's first
Current Anchor. The Anchor binds the Registry revision, Registry digest, and
selected definition digest. A changed binding retires the previous Anchor to
Beyond evidence and creates a new Current Anchor. This does not start an
executable Runtime or infer its session, frame, endpoint, or runtime
currentness. Conversation Resume and Archive files support recall only.

Unavailable transition capability remains `UNKNOWN`; missing Registry evidence
returns `MODE_REGISTRY_UNAVAILABLE`. Prior Mode context is not used as a
substitute. Mode preparation does not require execution authority.

A process-local transition proposal may remain read-only. If the transition is
persisted to session files, an Anchor surface, a database, or another durable
target, execute `.ai/skills/common/execution-guard/SKILL.md` first. A selected
Mode or Role never supplies the missing Authority, Write Scope, Assignment, or
approval.
