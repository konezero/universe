# Universe Goal Plan Design QA

Date: 2026-08-12

## Reference

- Desktop direction: Option 1 information architecture with Option 2 color balance.
- Mobile direction: single-column Goal plan with compact Conductor dock.
- Product hierarchy: Project -> Goal -> Milestone -> Todo.

## Functional Verification

- Project selection opens the project Goal plan.
- Work and Map navigation switch in both directions without reloading.
- Goal creation dialog exposes title, outcome, owner, and state.
- Goal and Milestone persistence is execution-neutral: no Task Frame or Execution Assignment is created.
- Todo can bind to a Goal and Milestone only within the same project.
- Goal updates use revision conflict detection.
- Delegate Goal prepares a Conductor instruction draft and does not dispatch automatically.

## Visual Verification

### Desktop 1440 x 1024

- Reference shell is represented as four explicit layers: tool rail, project/session rail, Goal Plan, and Inspector.
- Work Spine is the selected home surface and Graph remains a reversible secondary surface.
- Goal Plan uses dense inline readiness, progress, owner, and milestone metrics rather than dashboard cards.
- Coral is reserved for planning calls to action; teal communicates navigation and current state.
- The Conductor composer is docked across the bottom of the working canvas.
- No horizontal page overflow.
- Goal summary and unassigned work remain scannable without nested cards.

### Mobile 390 x 844

- No horizontal page overflow.
- Project and Inspector rails are removed from the primary mobile canvas.
- Goals, Sessions, and Activity are exposed as compact top tabs.
- Goal actions collapse to the fixed Delegate, Edit plan, and Add milestone bar.
- Conductor remains reachable immediately above the action bar.

## Automated Results

- Goal Plan focused API tests: 3 passed.
- Browser verification: no console errors; Home -> Graph -> Home works without reload.
- Desktop and mobile screenshots were checked at 1440 x 1024 and 390 x 844.
- Server and Session Observatory regression: 139 tests run, 137 passed.
- Two failures are pre-existing expectation drift from automatic `metadata.node_tag` enrichment:
  - `test_legacy_project_record_without_attachment_normalizes_on_read`
  - `test_registration_exposes_attachment_and_preserves_origin_on_refresh`

## Result

Goal Plan implementation and visual QA: PASSED.

Repository-wide regression: PASSED for this change, with two unrelated existing expectation failures recorded above.
