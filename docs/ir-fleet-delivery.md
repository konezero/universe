# Action IR and Fleet delivery

## Evidence and scope — 2026-09-14

The live Action catalogue has no server restart operation. The existing server CLI restart also restarts the PTY Supervisor; `restart_service` itself controls only the web server. A web-owned synchronous restart cannot return completion after terminating itself. Restart therefore needs an external lifecycle owner and a durable operation result, with fixed service targets, repeat-request protection, and reconnection on the same port.

Fleet `homeNodes()` currently includes only FEATURE nodes and nodes referenced by a Todo. Consequently adopted structural and domain nodes with no Todo disappear. `submitHomeNode()` also bypasses the existing feature.create Action. These are confirmed source owners; whether every missing project has an up-to-date projection is still unverified.

The new-project wizard currently invokes /v1/future-paths before composition. This conflicts with the accepted design in universe-design-and-bench-flow.md. Project authoring must be separated from Galaxy prediction.

## Track 1 — lifecycle Action IR

Implement service.status and service.restart through the server-resolved Action context. Restart accepts a request identifier and the observed server process identity, never an executable, token, database path or shell command. Persist acceptance before launching an external fixed helper, serialize concurrent restart operations, preserve the endpoint port and PTY Supervisor, and expose the same operation after reconnect. Failed or interrupted operations must retain structured status rather than claim success.

Validate schema rejection, duplicate replay/conflict, concurrent requests, helper failure, stale process identity, and successful restart with actual endpoint recovery. Add a UI command using the same Action contract available to LLM clients.

## Track 2 — Fleet and shared project authoring

1. Display adopted structural/domain work nodes even without Todos; keep predicted nodes in Galaxy and knowledge in memory/document surfaces. Route manual node registration through feature.create.
2. Introduce a revisioned project draft containing description/final goal, domain, scenarios, structure/process, capabilities, validation and constraints. Human edits and LLM edits use the same Actions and expected revision. Keep the draft visible above Fleet while conversation remains available; never overwrite unsaved input during refresh.
3. Create a project from the accepted draft without selecting a forecast, technology stack or route. Preserve explicit manual registration. Existing projects need an editable goal/plan with a provenance-labelled fallback when only source projection exists.
4. Link plans to work nodes and detailed Todos. Keep state transitions and kanban on their existing receipt-aware lifecycle path. Domain labels must remain configurable rather than impose software-only terminology.
5. Present the authoring Action identifiers, draft id and revision to the conversation context so an LLM can read and update the same draft. A suggestion is not an authorization to execute work.

## Completion evidence

Implemented in source: service.status/service.restart and Settings control, Fleet adopted structural/domain node visibility, and manual feature.create routing. Tests cover durable replay/conflict, stale process identity, concurrent requests, helper failure, replacement confirmation, and generic-domain Fleet nodes. Operational restart validation is pending.

Shared revisioned authoring is now implemented in source and tested on a fixture server; see project-authoring.md. New-project entry opens this shared draft without requesting a forecast. Actual registration/materialization and structured node/plan linking remain separate follow-up boundaries. Existing FRESH_PROJECT_DRAFT responses seed the editor. Do not equate saving a draft with creating a project or starting work.


## Verification and activation state

Final validation: 36 Python tests passed (service Actions 9, service control 18, Action Registry 9), Fleet JavaScript regression passed, JavaScript syntax and Git whitespace checks passed. Production restart/reconnection is not included in these passed checks.

Live browser inspection of the Universe projection found 69 graph nodes and displayed 29 Fleet work nodes, including 25 without Todos, across FEATURE, SURFACE, STRUCTURE, COMPONENT, CAPABILITY, FLOW, EXTERNAL_BOUNDARY, PRODUCT and APP. No page errors occurred. Screenshots: `.artifacts/ui/fleet-node-visibility-20260914.png` and `.artifacts/ui/service-restart-control-20260914.png`.

HTTP testing exposed a shared-boundary bug when token authorization ran after Action JSON parsing: rejection attempted to drain the same request body twice and waited indefinitely. The common body reader/drainer now tracks consumption per request headers object. An HTTP regression checks unauthorized Action, authorized Action and legacy unauthorized shutdown on successive requests. No lifecycle mutation executes in the unauthorized cases.

The user restarted the production server after the first delivery. Live verification now observes PID 53816, service.status READY, both service Actions registered, and missing-token service.restart rejected promptly with 401. The initial activation is complete.

The installed generic COMMAND route returns a file-only NOT_REQUIRED response and remains unsuitable for lifecycle execution. Universe now supplies the strict local Host adapter in tools/universe_service_execution.py: exact user-attested lifecycle binding, current Anchor/PID/state checks, a 30-second one-time permit, and fixed Action dispatch immediately after consumption. See service-restart-execution.md. The installed shared Runtime links were not modified. Actual agent-issued restart completed: PID 53816 became 38244 on the same endpoint, PTY Supervisor 18468 stayed running, and saved remote gateway/connector both returned READY.

Follow-up source validation passed 136 Python tests (drafts 6, service Actions 9, resident Project Master Host 112, Action Registry 9), the Fleet node regression, JavaScript syntax and Git whitespace checks. The shared-draft browser flow passed on a temporary server. Real Provider editing remains NOT_RUN. Production draft activation and actual restart/reconnection were subsequently verified through the strict lifecycle adapter. Browser fixture cleanup emitted a background request timeout/closed-socket warning; it is not a clean shutdown result.
