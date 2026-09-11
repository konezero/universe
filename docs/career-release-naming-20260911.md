# Career release naming and daily work evidence ? 2026-09-11

## Release correction

Canonical source: `C:\workspace\career`, commit `5b79566`, package root
`runtime-source`. The obsolete build recipe in `docs/core-release-db.md` used
`C:\workspace\ai-career` and `konezero/ai-career`; it now names Career.
The original caller responsible for the legacy-labeled imported artifact has
not been established. The outdated recipe is a confirmed inconsistent input,
not proof of which caller used it.

New builds normalize supported Career source aliases to `career`, reject
legacy runtime-source labels, and reject arbitrary source labels when building
from the Career root. Actual UTC build start time is recorded in `built_at`;
`display_name` formats that instant in KST as `career-YYYYMMDD-HHmmss`.
Names are carried through verified API records and COPY/LINKED install state.
The immutable hash ID remains the action key. Existing artifacts keep original
provenance and explicitly show missing build time instead of an invented date.

Validation: core release suite 15 PASS; runtime install suite 18 PASS; focused
server import/plan and apply/idempotence tests 2 PASS. New API and install field
assertions and the additional source-root rejection check passed on targeted
rerun. Node syntax and git diff --check PASS. Isolated Chromium ran the actual
catalog renderer with new and legacy records and verified correct immutable
ID selection. This is fixture UI evidence, not a live service deployment test.

Build output: career-20260911-222420 (`core-5b79566bcb40-38709663166c`). Artifact verification passed.
The new artifact is under `.ai/runtime/tmp/career-named-release.sqlite3` with
its adjacent `.manifest.json`. Live catalog import, service restart, and
project Apply for this new artifact have not been performed. Existing live
installation remains unchanged by this source task.

## Today's other work (evidence scope)

- Career `5b79566`: LINKED CLI lexical package root and boot launcher fixes;
  installed source comparison and post-fix runtime attach were verified earlier.
- Career `bfc38f8`: managed mode changes resolved using Supervisor session
  identity; included in the earlier installed release delta.
- Universe `319eac3`: Session Bus pending instruction dispatch on reconnect.
- Universe `ef77a8a`: queued Master work wake on session hook/reconnect. Master A
  reported restart and unit coverage; the latest observed Codex delivery still
  reported CURRENT_TERMINAL_UNAVAILABLE. End-to-end completion is not established.
- Universe `c7fbe23`, `0f3c40e`: typed Memory decision contract and single-candidate
  GET for reopen eligibility after refresh. UI integration remained pending in
  the last observed Master B report; preserve its working-tree changes.
- Universe `8c9fcf7`: terminal reattach identity and dock focus (Git log evidence).
- Universe `6c147af`, `5577385`: RAG failure evidence recall/reuse feedback and
  governed batch retry integration (Git log evidence; not independently retested
  by this release-naming task).

No unrelated dirty Memory/UI/docs changes were reverted. No overall Memory or
queue TODO was marked DONE by this task.
