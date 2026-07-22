# universe Runtime Anchor Frame Contract

<!-- ai-career-project-runtime-overlay:start -->
## Managed ai-career Runtime Binding

This source-managed block augments the project-owned policy outside the
block. The project may keep richer local routing, but shared Runtime
package entry, capability, and execution-gate references in this block
remain source-bound. Edit project policy outside this block.

Schema: ai-career.project-runtime-anchor-frame.v1
Current Frame: `.ai/runtime/state/current_anchor_frame.md`

Currentness Key: session_id + frame_id
Temporal Contract: `.ai/core/ANCHOR_TEMPORAL_COORDINATE.md`
Input Observation: Host physical time advances observed_at only
Time Passage Alone: does not create STALE
Frame Store: cache, not authority
Source Authority: immutable Git commit

Mutation Guard: `.ai/skills/common/execution-guard/SKILL.md`
Receipt Binding: session_id + frame_id + anchor_id + target + operation
Receipt Reuse: forbidden

Authority: UNASSIGNED
Execution Assignment: UNASSIGNED
<!-- ai-career-project-runtime-overlay:end -->
