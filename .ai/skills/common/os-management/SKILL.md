---
name: os-management
description: Route OS management commands to distribution-owned and installed project runtime surfaces.
---

# OS Management Invocation

Invocation class: `DISTRIBUTION_RUNTIME`

Capability classification: `os_install_validate = DISTRIBUTION_RUNTIME`

This Skill routes `OS_INSTALL`, `OS_UPDATE`, `OS_STATUS`, `OS_PREFLIGHT`,
`OS_VALIDATE`, `OS_ROLLBACK`, and `OS_SYNC`. It does not implement those
commands and does not turn a command label into write authority.

For an unmanaged target collision, route migration discovery through the
installed distribution command:

```text
project_runtime_installer.py inspect-migration <full source and target coordinates>
```

This read-only command may emit a hash-bound `candidate_profile`. Present that
profile for review and use `migrate --profile-file <reviewed-json>` only after
explicit approval. Never convert the candidate directly into generic write
permission.

## Targets

```text
.ai/core/RUNTIME_COMMANDS.md
  -> .ai/core/RUNTIME_INSTRUCTION_SET.md
  -> OS_INSTALL / OS_UPDATE: Host Runtime Lifecycle dispatch
  -> status commands: installed .ai/runtime/project_instance/ surfaces
  -> BOOT / REBOOT: .ai/skills/common/boot/SKILL.md
  -> installed distribution runtime/tool surface when the command requires it
```

## OS_UPDATE Pre-Route Invariant

After intent classification confirms `OS_UPDATE`, select this Skill and the
Host Runtime Lifecycle route before any Session Boot or ordinary mutation
route. This ordering is mandatory even when the Host reports that no Session
Runtime, Authority, Execution Assignment, endpoint, or Guard receipt exists.

```text
OS_UPDATE intent confirmed
  -> load OS Management
  -> bind immutable source and absolute target
  -> perform read-only managed-target preflight
  -> present one exact OS_UPDATE proposal
  -> wait for exact approval
  -> Host Runtime Lifecycle adapter
  -> validate / status / READY_FOR_BOOT
  -> stop
```

The Host must not:

```text
start or require BOOT before OS_UPDATE
start a Session Boot executor to obtain update permission
construct an ordinary Execution Guard request for OS_UPDATE
offer "existing lifecycle practice" versus "BOOT / Execution Guard" choices
invoke project_runtime_installer.py install directly
```

`BOOT` remains a separate later command. `READY_FOR_BOOT` is the update
handoff, not permission to infer or execute `BOOT`.

`OS_INSTALL` installs the durable Project Runtime. It ends at
`boot_handoff: READY_FOR_BOOT` and must not report Session Runtime readiness.
The Boot Skill separately owns `BOOT` / `REBOOT` invocation and
`session_boot_executor = AVAILABLE`. A successful repository-runtime status
does not replace the process-local Session Boot result.

For status reads, use:

```text
.ai/runtime/project_instance/status.md
.ai/runtime/project_instance/VERSION_MANIFEST.md
.ai/runtime/project_instance/validation/latest.md
```

When the Host is source-only, these files are observed references rather than
current execution evidence. Follow
`.ai/skills/common/runtime-status/SKILL.md` and produce the source-only
`OS_STATUS` baseline. A checkpoint, Resume Archive, validation, Runtime Image,
or Core gate document must not be promoted to active, restored, current,
verified, or ready state from its text.

The canonical `.ai/runtime/reference_runtime/cli.py` is not an installer or an
OS validator and must not be invoked as one. Preserve the distribution/runtime
stdout, structured result, and failure state without mapping `UNKNOWN`,
`PARTIAL`, `STALE`, or failure into success.

## Project Runtime Source Provider

For `OS_INSTALL` and `OS_UPDATE`, select exactly one source transport after Host
capability inspection:

```text
local Git CLI and immutable object database available
  -> bind the immutable local source with --source-root semantics

GitHub connector file/blob reads and sandbox execution available
  -> fetch source path .ai/distribution/context_management_runtime_pack/SOURCE_BUNDLE_CONTRACT.md
  -> resolve the requested ref exactly once to one full source commit
  -> preserve source commit created_at as commit-date evidence
  -> fetch project_runtime_source_index.json at that commit
  -> fetch every indexed path at the same commit
  -> materialize the content-addressed source bundle
  -> bind the provider-attested source bundle

authenticated GitHub CLI and sandbox execution available
  -> use gh api to resolve the requested ref exactly once
  -> record source.provider as github-cli
  -> preserve the commit timestamp as commit-date evidence
  -> fetch project_runtime_source_index.json at that full commit
  -> fetch every indexed path and provider blob SHA at the same commit
  -> materialize the content-addressed source bundle
  -> bind the provider-attested source bundle

neither transport available
  -> SOURCE_PROVIDER_UNSUPPORTED
```

The Skill is the connector caller and transport adapter only. It must not:

```text
invent source bytes
mix files from different refs or commits
relabel github-cli evidence as github-connector evidence or vice versa
create a synthetic Git commit
copy a partial package directly into the target
change provider-attested into git-object-database evidence
map source_cleanliness NOT_APPLICABLE to CLEAN or VERIFIED
```

The Python installer is the deterministic bundle verifier. The Host Runtime
Lifecycle adapter owns first-install and managed-update dispatch and calls the
low-level installer only after exact approval. Do not invoke
`project_runtime_installer.py install` directly for `OS_INSTALL` or
`OS_UPDATE`. Return raw JSON before interpreting
installation, migration, validation, or status.

## Target Resolution

The deterministic installer always requires an explicit absolute `--target`,
but the user does not have to type that path when the local Host already exposes
one stable workspace root. When the user omits the target and the Host exposes
one stable local current working directory, the Skill must present that
directory as the target candidate in the proposal:

```yaml
target_resolution:
  explicit_target: REQUIRED_FOR_INSTALLER
  current_working_directory_candidate: ALLOWED
  candidate_source: HOST_CWD_EVIDENCE
  candidate_display: REQUIRED_WHEN_UNAMBIGUOUS
  target_confirmation: REQUIRED
  installer_target_argument: REQUIRED
  implicit_target_mutation: FORBIDDEN
```

The proposal must display the resolved absolute candidate path and state that
no target mutation has occurred. Do not ask the user to repeat a path already
available as unambiguous Host CWD evidence. Only after the user approves that
displayed path may the Skill pass it as `--target <absolute-path>` to the
installer and continue through the Runtime Lifecycle Gate. This candidate display is
not implicit mutation and does not weaken target confirmation. If the Host has
no stable local working directory, exposes multiple ambiguous roots, or cannot
source-back the path, keep the target `UNKNOWN` and request clarification.
Mobile, browser, chat, and temporary sandbox locations are not durable targets
merely because they are a current working directory.

## OS Install Dispatch

`OS_INSTALL` is the sole user-facing first-install intent, not an unconditional
write command. `PROJECT_RUNTIME_INSTALL` is only the internal deterministic
operation invoked after approval. Resolve immutable source and absolute target,
then classify the target with read-only evidence before selecting a mutation
path:

```text
OS_INSTALL
  -> immutable source binding
  -> explicit target confirmation
  -> read-only target classification

      no installed manifest + inspect-migration PASS + collision_count 0
        -> dispatch_state: FRESH
        -> present exact FRESH_INSTALL request
        -> obtain exact approval
        -> host_fresh_install.py --request <approved-json>

      installed manifest + status PASS + same source commit
        -> dispatch_state: ALREADY_INSTALLED
        -> no mutation
        -> boot_handoff: READY_FOR_BOOT

      installed manifest + (status not PASS or different source commit)
        -> dispatch_state: UPDATE_PROPOSAL
        -> present exact OS_UPDATE proposal
        -> identify RUNTIME_UPDATE only as the internal lifecycle operation
        -> bind immutable source, absolute target, managed-path inventory,
           and any exact unmanaged collision paths
        -> obtain exact approval
        -> host_fresh_install.py --request <approved-json>

      no installed manifest + inspect-migration CANDIDATE or PARTIAL
        -> dispatch_state: MIGRATION_REVIEW_REQUIRED
        -> preserve the candidate profile
        -> no automatic migration or Fresh install

      installed manifest invalid, source unavailable, or classification unclear
        -> dispatch_state: UNKNOWN_OR_BLOCKED
        -> no mutation
```

Every `OS_INSTALL` request reaches this dispatch only after an explicit durable
target is confirmed. If no target is known, return `INSTALL_PROPOSAL_REQUIRED`.
Do not reinterpret `OS_INSTALL` as BOOT or SESSION_ATTACH, and do not expose the
internal `PROJECT_RUNTIME_INSTALL` operation as a second user command.

`OS_UPDATE` is the sole user-facing managed-update intent. Proposal headings,
approval prompts, progress updates, completion reports, and suggested next
commands must use `OS_UPDATE`. Do not present Core and Runtime as separate
update choices, and do not instruct the user to invoke `RUNTIME_UPDATE`.

The user-facing proposal summary records:

```yaml
user_command: OS_UPDATE
internal_operation: RUNTIME_UPDATE
```

The exact adapter request and approval object still bind
`operation: RUNTIME_UPDATE` for deterministic lifecycle enforcement and
compatibility. The raw result retains the same operation. When relaying that
result, label it as internal evidence and report the user-visible completion
as `OS_UPDATE complete`.

The approved Fresh request may use the legacy schema
`ai-career.host-fresh-install-request.v1`; new lifecycle requests use
`ai-career.host-runtime-lifecycle-request.v1`. Both bind the exact operation,
source commit, absolute target, and approval evidence reference. A
`RUNTIME_UPDATE` request additionally binds the sorted exact unmanaged
collision inventory and its SHA-256 in both request and approval. The adapter
may use `--force` only when that exact inventory is still observed at execution
time. A successful Host result proves `repository_runtime: VERIFIED` and returns
`boot_handoff: READY_FOR_BOOT`; it does not prove `session_runtime: READY`.

## Runtime Lifecycle Gate

`OS_STATUS`, `OS_PREFLIGHT`, and any strictly read-only validation may run
without a mutation receipt. `FRESH_INSTALL` and `RUNTIME_UPDATE` must use the
Host Runtime Lifecycle adapter instead of the ordinary Session Execution Guard:
the active Session Runtime may be absent or the broken surface being repaired.

The Lifecycle Gate requires immutable source verification, an exact absolute
target, an exact approved operation, a source-derived managed-path inventory,
and an inline single-invocation lifecycle receipt. It never grants generic file,
shell, repository, commit, or push permission. `src/`, project-owned files,
undeclared collisions, and all non-installer mutations remain outside this
gate and continue through the normal execution binding and Execution Guard
path. A successful source-provider check or repository-runtime result does not
grant that permission.
