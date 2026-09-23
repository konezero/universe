# Master-owned Todo execution journal

Implemented 2026-09-23. The direct user instruction selects one Master writer per
node and read-only Worker access. The Todo database remains authoritative for
scheduling, ownership and state transitions; the journal is the bounded work and
collected-evidence history, not an authority or permission store.

## Contract

- Master `persona.automation.launch-frame` creates/appends
  `<project>/.ai/runtime/todo_journals/<todo_id>.jsonl` before launching a Host.
- Each `ASSIGNED` event identifies run, frame, role, attempt, owner and sequence.
  It carries the Todo snapshot, persona, unchanged write boundary, feedback and
  at most the latest collected result per role. A follow-up frame can reuse
  collected evidence from the same Todo without replaying the entire history.
- The existing Master-only `collect-frame` verifies the exact immutable result
  reference, digest, owner session and Task Frame envelope before appending
  `RESULT_COLLECTED`. Collection is still NOT acceptance or a PASS verdict.
- Collect each role result before directing the next role. The Master's
  `host-directive` appends the next pinned assignment before posting the room
  directive. REVIEWER requires a collected Worker result. DONE appends
  `MASTER_DONE`, which is NOT a Todo completion assertion.
- Worker/Reviewer cannot append journal events or include journals in their
  delegated write targets. The Runner passes only the exact journal byte range,
  SHA256 and identity, reader path, scope, output contract and mandatory policy.
  Bulk Todo/persona/feedback/result text is no longer injected in its context.
- The Runner supplies `journal_read_argv` with the Host's absolute Python path.
  `tools/todo_execution_journal.py --root <project> --reference-base64 <base64>`
  reads exactly one pinned record without PowerShell JSON-quote transformations.
  Native callers can also use `--reference <JSON>` with a preserved argument array. Later appends do not change the selected input.
  Returned Worker outputs are evidence, never instructions or authority.

## Persistence and recovery

Appends are fsynced, with server-side handler serialization and deterministic
event replay/conflict checks. The live journal is never deleted or renamed for
normal appends. A partial final record is preserved and reported as
`TODO_JOURNAL_TAIL_INCOMPLETE`; no new assignment executes until explicit repair.
Complete corrupt records, identity changes and digest conflicts fail closed.
Pinned earlier records remain readable if the tail was interrupted.

Journal evidence is written before collection bookkeeping. If DB/Anchor cycle
recording fails, repeat the same collection with the same result/digest: the
journal event replays without duplication. No automatic Todo DONE promotion or
external operation resend is implemented. The Master reconciles through existing
typed Actions and independent review.

## Activation and migration

New detached Hosts use this contract after server deployment. Old Hosts are not
silently upgraded or given reconstructed assignments; finish/close them and make
one bounded follow-up under the same Todo after checking liveness. Legacy DONE
remains supported solely to close such Hosts. Legacy immutable results remain in
their original Task Frame stores; they are not relabelled as journal evidence.

Current runtime binding lookup must succeed before each role; the old fallback
to launch-time credentials is removed. No Worker invocation occurs on a failed
lookup. This change does not migrate or fix the separate Rust Session Host's
native-queue reservation snapshot file.

Verification covers append/replay, pinned reads, partial/corrupt tails, path and
identity rejection, read-only Reviewer context after Runner restart, exact
rework attempts, failed binding lookup, and launch/collect/directive HTTP Actions
using disposable stores and mocked provider/Host process launches. Real provider
and resident-service results must be reported separately from those tests.

## Observed rollout evidence (2026-09-23 KST)

- Focused verification: 113 tests across journal (8), Runner (7), launch (7),
  Host lifecycle (10), dispatcher (30), and Node Master HTTP Actions (51).
- Supported service restart `todo-journal-deploy-20260923-1` completed with
  replacement PID 42620 and READY status; existing Master sessions were retained.
- Read-only live Task Frame `journal_live_20260923_6_worker_1` read the pinned
  record through the actual provider/sandbox, validated SHA256, and returned a
  random marker available only in that record. `JOURNAL_NONCE_MATCH=True`.
  Immutable result digest:
  `c7fca9886e473e6a045047a306607f6e98fb9ff77b8d6ab67240990576819d06`.
  Result reference:
  `task-frame-result://journal_live_20260923_6_worker_1/worker-turn/codex-app-server:01a0c9e8-91d9-7392-aea6-e7e2a695fdf8:universe-runtime-host:57508ec7db4b4ebea18a5d4548a16492`.
- Live rollout exposed guessed interpreter paths and PowerShell JSON argument
  transformation. The explicit interpreter/Base64 reader contract addresses
  those transport failures. The isolated probe also needed ordinary inherited
  repository ACLs: Python 3.14 TemporaryDirectory creates owner-only directories
  that the sandbox reader could not access. No production ACL was changed.
- Fleet, registration and RAG Masters each launched a journal-backed Host. RAG
  demonstrated Worker read -> immutable result collection -> Reviewer assignment.
  Underlying Todo acceptance remains separate; launches and transport COMPLETED
  must not be interpreted as Reviewer PASS or Todo DONE.

## Host-owned Worker tools (2026-09-23 continuation)

Codex Task Frame threads now receive `universe_read_assignment({})`. The Host
retains the pinned reference and checks its exact path, byte range, digest and
identity before returning the record. The model cannot substitute arguments or
reconstruct a long Base64 shell command. This is read-only and is available to
Reviewers as well as Workers. The CLI reader remains for other supported callers.
The registration Worker reported `TODO_JOURNAL_PATH_INVALID`, but its immutable
result did not preserve the actual command/stderr; the exact same pinned reference
read successfully at the Host. Do not claim the original transport cause is proven.

Bounded Codex Workers also receive `universe_edit`. This is an explicit Host-owned
effect adapter, not a raw shell write, a new permission, or an impersonated Worker
receipt. The Worker proposes an exact text replacement; the owning Host checks:

- The exact delegated CREATE/MODIFY operation and absolute target within the repository.
- The live Task Frame turn, CLAIMED state and Worker identity.
- The complete file preimage SHA256 and one exact old-text occurrence.
- Its own existing session Work Receipt and current Mode Anchor. The installed
  receipt-aware file gateway revalidates and records the actual mutation.

The Master must activate its own bounded receipt through the supported
`execution-binding begin-local-work` route before a fresh Host edits. Never borrow
a Conductor receipt, synthesize Worker lineage, or interpret a missing receipt as
a request for another approval when unchanged user authority already exists.
No receipt is minted automatically by the Worker tool. DELETE/MOVE are unsupported.
Native file-change approval is refused while the Host editor is active; blocked
tool results must not be bypassed with Python, encoded commands or alternate paths.

Reads return at most 16,384 characters, with whole-file SHA and window offsets;
`old_text` on a read selects a search snippet. Files up to 16 MiB can be edited
with bounded replacement inputs. The tool preserves unmodified bytes, including
line endings. Exact call retries replay the prior result; conflicting call IDs
are rejected. An exception during gateway execution reports UNKNOWN mutation
outcome, caches that response and blocks further writes until reconciliation.
It does not falsely claim that no write occurred.

Observed native Codex 0.155.1 file-system helper setup still fails before opening
the preimage: runtime read/execute validation rejects a deep cua_node dependency
path. The shell helper and file helper differ: child-only extended LOCALAPPDATA
spelling made the observed shell boundary work, but did not fix the file helper.
No global environment, ACL, Codex cache/installation or sandbox policy was changed.
The Host-owned gateway is the implemented bounded edit path; this is not an
upstream sandbox-helper fix or a repair of the separate native-queue snapshot lock.

Verification for this continuation: 100 focused tests plus 18 subtests passed
across file editor, Host tool protocol, agent gateway, Worker dispatcher, journal
and Runner. Real provider evidence, separate from mocked tests:

- `edit_live_20260923_3_worker_1`: read BEFORE, exact-SHA replacement, read AFTER;
  fixture bytes independently observed as `AFTER\n`. Immutable digest
  `14d243adcc3c1c5f1062db411db3e3ca9ce8b0f7965f3d8fc4c79eec9795b993`;
  result `task-frame-result://edit_live_20260923_3_worker_1/worker-turn/codex-app-server:01a0c9ff-23cd-7bf0-a2fb-dd025be2f2a9:universe-runtime-host:46352f3f42514568855236c5ff6c11ff`.
- `journal_live_20260923_7_worker_1`: used the argument-free Host journal tool and
  returned the nonce found only in that journal. Immutable digest
  `b26f98f4a001873eca941e8762edcc218cbad667fc9908bd0c7ea591481553fc`;
  result `task-frame-result://journal_live_20260923_7_worker_1/worker-turn/codex-app-server:01a0ca08-2b43-72c0-ba2f-ccef372c8aa8:universe-runtime-host:450ce576078f4c2180cac262aeac15e9`.

Both diagnostic Runtime attachments were closed after the bounded probes.
Fresh detached Hosts load these tools; existing role processes retain old imports.
Collect old results and confirm there is no active role before a single follow-up.
These transport checks do not establish completion of any product Todo.

### Recovery checkpoints observed at 2026-09-23 02:03 KST

- Fleet Master activated its own receipt and launched exactly one follow-up
  `host_8009bce88551326f6e38`. Its immutable Worker1 result reports completed
  source changes, 29 UI tests passing (zero failures), and diff-check passing.
  Result digest: `a8a076f2fc29446851a45a70178048d161e39561c732b7daf71d92e3264e51d9`.
  This is Worker evidence; independent Reviewer acceptance remains pending.
- Registration Master activated its own receipt and launched
  `host_85baddb89d9bee33d9b7`, observed alive/RUNNING_WORKER. This attempt excludes
  app.js from the original eleven-file ceiling to avoid overlapping Fleet writes.
  Existing Masters received a handoff instruction: only after Fleet review/rework
  finishes and no writer remains may registration integrate app.js against its
  current preimage. No duplicate Todo or replacement Master was created.
- RAG Reviewer collection succeeded with event
  `persona_event_8ecf98f8de474306896fac4b`, preserving NEEDS_REVISION. The rejected
  earlier request had a mistyped digest (`da9842b28` instead of `da984b28`); the
  stored envelope was valid. A bounded recovery helper derives ref/digest from
  the read-only immutable ledger and calls the supported collection Action.
  No evidence or validation rule was rewritten. Nonempty live circulation and
  restart/lease acceptance remain unproven; Todo completion is not asserted.

### Canonical collection and rework binding (2026-09-23 continuation)

Two later stalls were outside file editing: registration copied a different
receipt reference, and Fleet's fourth PASS could not use the first-attempt-only
legacy recovery API. Collection now resolves `result_role`/`result_attempt` on
the server, retaining strict immutable-envelope validation. Recovery accepts
`worker_attempt`/`reviewer_attempt` and verifies Master journal lineage, latest
assignments, unchanged scope/Todo, exact collections and the Worker's identity
pinned into the Reviewer assignment. Legacy evidence without a journal retains
the original-pair restriction. See `persona-worker-review-automation-contract.md`.

- Regression verification: 109 tests and 11 subtests passed in the four affected
  suites; the remaining new Store integration test passed after its fixture's
  run/owner identity was corrected. A test-only SQLite handle leak was also
  fixed with explicit connection closing. Diff check passed.
- Read-only validation of real Fleet Worker4/Reviewer4 returned PASS with
  `completion_provenance_verified=true`, without rerunning its UI tests.
- Supported deployment operation `task-frame-rework-recovery-deploy-20260923-1`
  completed: service PID 42620 became 39316, READY; Master sessions retained.
- Existing Fleet and registration Masters accepted continuation messages
  `msg_0ec280dcee1ec7c9` and `msg_26b32ed689cae998` and entered STARTED.
- Registration's actual canonical collection succeeded with event
  `persona_event_1dddefd56f7c4145a1d21b52`. Its immutable Worker still reports
  PARTIAL_VERIFICATION; collection is not Reviewer acceptance or Todo completion.

- Fleet canonical recovery succeeded with event
  `persona_event_160cd166bbfc4a7fbd7e492a`, followed by completion event
  `persona_event_a889416f2db6454ea4a52c0a`. Authoritative run state is COMPLETED
  revision 35 with Reviewer PASS; exact Todo `todo_35b9863c714342a6b31359eee246e5e5`
  is DONE revision 3. No tests or Worker were rerun to manufacture this result.

This repair does not claim to fix the separate native-queue snapshot sharing
violation or prove RAG live restart/lease acceptance.
