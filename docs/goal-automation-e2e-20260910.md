# Goal automation end-to-end verification

Instruction: `p0-goal-automation-e2e-20260910`;
active Work Receipt binding: `binding_aeda9403edbd7b577a09dfd3`.

Scope: registration/planning, Master handoff, Task Frame binding, exact Todo
result application, terminal Goal projection. No bulk scheduler start, existing
Goal rewrite, provider-work resend, repository push, or Source Review bypass.

## Observed live state

- `goal_memory_feature_proposal_automation_v1`: ACTIVE, no Goal Start receipt,
  no Work Plan application, `BLOCKED_GOAL_NOT_PLAN_ELIGIBLE`.
- `goal_7f9cc274464c08aff99dd009`: revision 3, old revision-1 Work Plan remains
  historical, no eligible Todos, `MASTER_HANDOFF_UPDATE_READY`. Its superseded
  work was not dispatched.
- Session directory shows GROK CONDUCTOR and CLAUDE MASTER terminals with
  unread inbox entries. No instruction/session was created during diagnosis.
- Inbox/thread preflight found two completed replies and one queued NOTICE
  (`msg_7ba3c371744cf7a7`, DISPLAY_ONLY, wake_model=false). The notice was not
  resent as an instruction. The project's runtime-worker-invocations list was
  empty; that does not prove every provider process is idle.
- Browser at `http://127.0.0.1:60443/` loaded the current project/node/Todo
  surface. This is UI reachability evidence, not a completed provider run.

## Reproduced defect

The existing HTTP/storage planning test covered partial Todo completion only.
Extending it with a validated result for the final remaining Todo reproduced
`TASK_FRAME_READY` instead of `GOAL_COMPLETED`. Todo result application never
created the Goal terminal application; only the Todo-free Goal route did so.

The shared Goal terminal application now checks all required Work Plan Todos
and remaining Goal Todos inside an immediate SQLite transaction before sealing
Goal DONE + result receipt. Partial/failed work cannot close the Goal. The
result handler accepts current-session-validated, revision-bounded replay of the exact
terminal result without reapplying actions or incrementing the Goal revision.
Different result references and unrelated revisions fail closed. Scheduler
execution permissions are unchanged.

Extending the same regression through a real scheduler tick reproduced a
second terminal-boundary defect: the Goal was DONE at revision 2, but the
scheduler still held revision 1 and returned BLOCKED/GOAL_REVISION_CONFLICT.
Terminal result application increments the revision by design. Recognizing
the already-verified GOAL_COMPLETED/NONE projection must precede the revision
gate for further execution; stale nonterminal work must remain blocked.

## Verification boundary

- Baseline Goal-focused server tests: 18 PASS.
- Final Goal-focused server regression run: 20 PASS (40.000 seconds).
- New final-Todo HTTP/storage regression: FAIL before, PASS after fix.
- Scheduler continuation of that regression: FAIL (revision conflict) before,
  PASS after fix. The test also reopens SQLite and checks terminal Goal,
  result application, and scheduler persistence.
- Separate store boundary regression: PASS for empty/missing required Todos,
  partial/failed work, unselected unfinished work, and all-DONE completion.
- Provider responses and Task Frame creation in that HTTP fixture are mocked.
  It is not live provider or full execution-host evidence.
- Live provider execution, pause/restart recovery under this changed service,
  and user-visible Goal completion remain NOT_RUN pending the bounded live probe.
- The running user service has not been restarted/deployed by this work.

## Completion audit

Overall outcome: PARTIAL. The thread Goal and project P0 Todo remain unfinished.

Live execution blocker rechecked after the final regression run: all three
existing Goals still have no current Task Frame binding and no eligible Todos.
The semantic-work-spine and memory-automation Goals both report
BLOCKED_GOAL_NOT_PLAN_ELIGIBLE/USER_RESOLUTION_REQUIRED; the Rust Goal still
points to its historical handoff update. The source Goal Start action requires
a server-resolved USER actor and an exact expected path, revision, digest,
approved scope, and constraints. A generic continuation is not a new selection
or approval for those existing product Goals. The isolated test-Goal question
has remained unanswered across the Goal continuations. Further live dispatch
is blocked on that user choice; no live Goal Start, resend, or server restart
was performed. The implementation remains uncommitted and unapplied.

| Plane | State | Evidence boundary |
| --- | --- | --- |
| Source | PASS | Exact reproduced owners changed; git diff --check |
| Storage | PASS | Real SQLite terminal transaction, refusal cases, reopen |
| API/protocol | PASS | Real local HTTP result application/replay; provider fixtures |
| Scheduler | PASS | Real tick after terminal result; stale nonterminal gate retained |
| Provider transport | NOT_RUN | No fresh bounded live Goal dispatched |
| UI completion | NOT_RUN | Existing UI loaded, completed Goal not exercised |
| Resident lifecycle | NOT_RUN | User service not restarted; recovery tests are contract evidence |
| Distribution/update | NOT_RUN | No commit, release, deployment, or push in this task |

Run the focused regression with:

```text
python -X utf8 -m unittest discover -s tests -p test_universe_server.py -k goal -v
```

On Windows this is invoked through the structured windows-native-cli runner.
The live probe needs an explicitly chosen valid Goal. A separate, file-change-free
one-task test Goal was proposed to the user; no Goal Start receipt was synthesized
from a stale product Goal or from an agent-authored approval.

## Remaining work candidates

1. P0: isolated provider-backed Goal probe, then pause/retry/restart evidence.
2. P0: Source Review Worker access to exact candidate diff/source (separate scope).
3. P0: WebSocket disconnect/recovery if reproduced on the Goal path.
4. P1: PTY/Hook/Anchor/resume and tail-first replay after correctness is stable.
5. P1: Collector/RAG/failure analysis and automatic Bench coverage.
6. P1: Skill Pack separation and Intent-to-Skill routing.

Do not mark the project P0 Todo complete from contract-only test evidence.
