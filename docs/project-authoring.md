# Shared project goal and plan drafts

Status: implemented, fixture-validated and production Actions active; real Provider editing remains untested.

Fleet opens a project description/goal/plan editor above the node/Todo canvas. It is not a modal: the conversation dock remains available. New-project entry no longer requests a forecast or a technology route. Existing projects can have scoped drafts; unattached drafts remain drafts and do not register a project, write source files, adopt nodes or execute work.

## One shared object

`project_authoring_revision` in the Universe database owns the append-only draft revisions. Human form edits and LLM/API edits use the same server Actions. The fields are plain text: title, domain, description, goal, target_users, scenarios, structure, capabilities, validation, constraints and project_root. Domain-neutral labels support software and other industries. A project folder is optional draft data, not filesystem authorization.

The server resolves the actor. A non-null project_id must identify a registered project and cannot change after the first save. All fields must be present on save; title/domain have 160-character limits, other fields 12,000. Unknown fields and invalid values are rejected.

## Action contract

All calls use POST /v1/actions with action_id and request.

- project.draft.read: `{ "draft_id": "draft_example" }`. An unknown id returns revision 0 with empty fields; it creates no draft record.
- project.draft.list: `{}` or `{ "project_id": "TEST" }` returns current revisions.
- project.draft.save: `{ "draft_id": "draft_example", "project_id": null, "expected_revision": 0, "request_id": "unique-request", "fields": { ...all fields... } }`.
- project.draft.register: `{ "draft_id": "draft_example", "revision": 1, "request_id": "register-request", "idempotency_key": "register-key", "project_id": "PROJECT", "project_root": "C:/project", "accepted_fields": ["title", "goal"] }`. The server reads the canonical revision, materializes through the project registration gateway, and records acceptance only after success. Replays return the durable result; changed keys and stale revisions conflict.

An exact repeated request returns its original revision without another write. Reusing its request id with different content fails. A stale expected_revision returns PROJECT_DRAFT_REVISION_CONFLICT (409). Earlier revisions remain available in the database audit history.

## Concurrent editing

The editor retains unsaved input while polling for updates from another editor. Clean forms receive newer saved revisions. Dirty forms show a comparison action; they never silently overwrite input. The user compares server values and explicitly prepares the current form as a merge before saving against the inspected revision. A further concurrent edit still conflicts. In-flight saves do not mark subsequently typed text as saved. Polling does not block the Save button. Switching drafts preserves dirty forms in the current page session; a browser refresh can lose unsaved form input.

Saved drafts can be reopened from the list. When an expanded saved draft is in the active Fleet view, conductorUiContext sends only its id. At provider dispatch, the server loads the canonical current revision and adds the read/save Action contract. Both resident Provider prompts and the planning Runtime context receive it. Client-supplied draft snapshots/authority are rejected. Provider invocation was not performed during validation; a second API client exercised the same edit contract.

The existing FRESH_PROJECT_DRAFT response can seed a new shared draft via its Review button. Legacy composition/refinement storage remains for previously recorded adoptions and handoffs. The old prediction-first wizard is no longer a Fleet entry point. Remove its unused UI helpers when the project materialization/adoption flow is migrated; do not delete recorded adoption history.

## Validation

Storage/API tests cover revision history, replay, stale updates, immutable project scope, schema validation, server actor resolution, rejection of client-injected snapshots and both resident Provider prompt formats. Browser testing covers new entry without /v1/future-paths, save, a second client update, dirty-input preservation, conflict comparison/merge, clean refresh and the draft id in conversation context. Screenshot: `.artifacts/ui/project-draft-shared-20260914.png`.

The test server used a temporary database. No real LLM request or production draft write was made. Browser assertions passed; fixture teardown reported a background request-handler timeout and a closed-socket error, so it is not evidence of clean production shutdown.

## Structured plan items

Draft fields stay plain text. A plan entry that needs real relations is saved as a plan item in `project_plan_item_revision` (append-only revisions) with a current-revision reverse index in `project_plan_item_link`. Implementation: `tools/universe_plan_item_actions.py`.

A plan item has a stable `plan_item_id`, owning `project_id`, `revision`, `kind` (CASE, STRUCTURE, PROCESS, WORK, VALIDATION, DEPENDENCY or PHASE), an optional display `label` such as `P1` or `V3`, `title`, `purpose`, `work`, `done_criteria`, `source` (PROJECT_DRAFT with draft_id/draft_revision/section, DOCUMENT with path/section, or MANUAL), `node_refs`, `todo_refs`, `depends_on` and `state` (ACTIVE or RETIRED). The label is display text only; relations exist only in the stored reference lists.

- plan.item.read: `{ "plan_item_id": "plan_example" }` returns the current revision and resolved links.
- plan.item.list: `{ "project_id": "PROJECT" }` with optional `node_ref`, `todo_id`, `draft_id` and `include_retired`.
- plan.item.save: `{ "plan_item_id": "plan_example", "project_id": "PROJECT", "expected_revision": 0, "request_id": "unique-request", "item": { ...all fields... } }`. The whole reference list is replaced; the response reports `link_changes` (added/removed per list).
- plan.item.trace: `{ "plan_item_id": "plan_example" }` navigates plan item -> nodes -> Todos -> results (result fan-outs and DONE/BLOCKED Todo evidence events) and returns each target's back-references to plan items plus plan-item dependents.

Save links existing rows only: it does not create nodes or Todos. A missing target, a node/Todo/plan item/source draft in another project, a newly linked ARCHIVED node, a duplicate reference, a self or cyclic dependency, a stale expected_revision, a project change and a reused request id with different content are rejected without a write. An exact replay returns the original revision. The server resolves the USER actor; UI and LLM clients use the same Actions.

Reads never fail on drift. A target removed later is reported as MISSING or PROJECT_MISMATCH and counted in `missing_targets`. A PROJECT_DRAFT source reports `stale` when the draft has a newer revision. `todo.read` returns the plan items that link that Todo.

Plan items never change Todo, node or execution state; Kanban continues to use the same Todo state. Trace completion is scoped to one plan item (NO_TODOS, NOT_STARTED, PARTIAL or COMPLETE over its linked Todos) with `project_completion: NOT_AGGREGATED`. Partial Todo completion is never reported as project completion.

Validation: `tests/test_universe_plan_item_actions.py` covers link add/change/remove, reverse lookup from node and Todo, result tracing, partial completion, missing/foreign/duplicate/archived targets, stale revision, replay, draft source scope and staleness, dependency cycles, target drift, actor/schema rejection and the POST /v1/actions round trip. The tests use temporary databases.

Real Universe case (not yet written): the live store contains the `draft_universe_goal_plan_20260914` revision 1, the existing node `feature_8889c55ff887f0a7cbc328f2` (Functional Node ownership and TODO hierarchy) and its Todo `todo_structured_plan_node_todo_links_20260914`. It has no plan items yet. The first live save should link these existing rows as a WORK item sourced from that draft revision, without creating a new node. Then trace it back from the node and the Todo.

## Remaining delivery boundaries

- Connect accepted drafts to project materialization/registration, with the required source/lifecycle authority path.
- Save and trace the first live Universe plan item (see Structured plan items), then show plan items in the Fleet draft editor. The Actions are server-only today; there is no UI panel yet.
- Validate a real LLM editing the shared draft. The server version and draft Actions are now active.
- Use the Universe lifecycle Host adapter in service-restart-execution.md for an already authorized agent restart. It records execution evidence without issuing a permission receipt.

## Test isolation incident

The reused MemoryCandidateApiTests fixture originally supplied only its database path, so service cleanup used the default production remote gateway/connector paths. Browser teardown attempted to stop/remove that gateway; the production API subsequently reported gateway and connector OFFLINE, while its startup resume record says REMOTE_ACCESS_STARTED. The local server remained responsive. The persisted connector configuration has no temporary-directory paths.

The fixture now explicitly supplies temporary service, gateway, connector-state and connector-configuration paths; a regression asserts that each belongs to the fixture root. The subsequent authorized service restart restored the saved production remote connection. Both gateway and connector now report READY. The fixture isolation fix and restoration were verified separately; real Provider draft editing remains untested.
