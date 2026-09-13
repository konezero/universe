# Shared project goal and plan drafts

Status: implemented and validated on a fixture HTTP server; production activation pending.

Fleet opens a project description/goal/plan editor above the node/Todo canvas. It is not a modal: the conversation dock remains available. New-project entry no longer requests a forecast or a technology route. Existing projects can have scoped drafts; unattached drafts remain drafts and do not register a project, write source files, adopt nodes or execute work.

## One shared object

`project_authoring_revision` in the Universe database owns the append-only draft revisions. Human form edits and LLM/API edits use the same server Actions. The fields are plain text: title, domain, description, goal, target_users, scenarios, structure, capabilities, validation, constraints and project_root. Domain-neutral labels support software and other industries. A project folder is optional draft data, not filesystem authorization.

The server resolves the actor. A non-null project_id must identify a registered project and cannot change after the first save. All fields must be present on save; title/domain have 160-character limits, other fields 12,000. Unknown fields and invalid values are rejected.

## Action contract

All calls use POST /v1/actions with action_id and request.

- project.draft.read: `{ "draft_id": "draft_example" }`. An unknown id returns revision 0 with empty fields; it creates no draft record.
- project.draft.list: `{}` or `{ "project_id": "TEST" }` returns current revisions.
- project.draft.save: `{ "draft_id": "draft_example", "project_id": null, "expected_revision": 0, "request_id": "unique-request", "fields": { ...all fields... } }`.

An exact repeated request returns its original revision without another write. Reusing its request id with different content fails. A stale expected_revision returns PROJECT_DRAFT_REVISION_CONFLICT (409). Earlier revisions remain available in the database audit history.

## Concurrent editing

The editor retains unsaved input while polling for updates from another editor. Clean forms receive newer saved revisions. Dirty forms show a comparison action; they never silently overwrite input. The user compares server values and explicitly prepares the current form as a merge before saving against the inspected revision. A further concurrent edit still conflicts. In-flight saves do not mark subsequently typed text as saved. Polling does not block the Save button. Switching drafts preserves dirty forms in the current page session; a browser refresh can lose unsaved form input.

Saved drafts can be reopened from the list. When an expanded saved draft is in the active Fleet view, conductorUiContext sends only its id. At provider dispatch, the server loads the canonical current revision and adds the read/save Action contract. Both resident Provider prompts and the planning Runtime context receive it. Client-supplied draft snapshots/authority are rejected. Provider invocation was not performed during validation; a second API client exercised the same edit contract.

The existing FRESH_PROJECT_DRAFT response can seed a new shared draft via its Review button. Legacy composition/refinement storage remains for previously recorded adoptions and handoffs. The old prediction-first wizard is no longer a Fleet entry point. Remove its unused UI helpers when the project materialization/adoption flow is migrated; do not delete recorded adoption history.

## Validation

Storage/API tests cover revision history, replay, stale updates, immutable project scope, schema validation, server actor resolution, rejection of client-injected snapshots and both resident Provider prompt formats. Browser testing covers new entry without /v1/future-paths, save, a second client update, dirty-input preservation, conflict comparison/merge, clean refresh and the draft id in conversation context. Screenshot: `.artifacts/ui/project-draft-shared-20260914.png`.

The test server used a temporary database. No real LLM request or production draft write was made. Browser assertions passed; fixture teardown reported a background request-handler timeout and a closed-socket error, so it is not evidence of clean production shutdown.

## Remaining delivery boundaries

- Connect accepted drafts to project materialization/registration, with the required source/lifecycle authority path.
- Link structured plan items to node and detailed Todo references; the current fields are project-level text.
- Validate a real LLM editing the shared draft. The server version and draft Actions are now active.
- Use the strict Universe lifecycle Host adapter described in service-restart-execution.md for agent-issued restart; a product control token alone is insufficient.

## Test isolation incident

The reused MemoryCandidateApiTests fixture originally supplied only its database path, so service cleanup used the default production remote gateway/connector paths. Browser teardown attempted to stop/remove that gateway; the production API subsequently reported gateway and connector OFFLINE, while its startup resume record says REMOTE_ACCESS_STARTED. The local server remained responsive. The persisted connector configuration has no temporary-directory paths.

The fixture now explicitly supplies temporary service, gateway, connector-state and connector-configuration paths; a regression asserts that each belongs to the fixture root. The subsequent receipt-aware service restart restored the saved production remote connection. Both gateway and connector now report READY. The fixture isolation fix and restoration were verified separately; real Provider draft editing remains untested.
