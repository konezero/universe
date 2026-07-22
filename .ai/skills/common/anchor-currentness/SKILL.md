---
name: anchor-currentness
description: Advance Current Anchor physical observation time through a Host adapter and evaluate source-backed currentness without creating authority.
---

# Anchor Currentness

Invocation class: `REFERENCE_RUNTIME_ADAPTER`

On every observed user input, when a Session Boot endpoint is available, invoke:

```text
anchor-currentness observe
  -> Host captures physical input_at
  -> verifies session_id + frame_id + anchor_id
  -> compares previous observed_at
  -> updates observed_at only
```

The LLM must not manufacture `input_at`. If the Host observation capability is
unavailable, report temporal advancement as `UNKNOWN`; do not claim that the
Current Anchor was touched.

Invoke `anchor-currentness evaluate` with the observed Anchor snapshot, the
current `session_id + frame_id`, and Host physical `checked_at` time.

The runtime may calculate elapsed time. It may classify a source-backed
`stale_after` deadline as `RECHECK_REQUIRED`; it must not invent a global TTL or
turn time passage alone into `STALE`. Identity mismatch, restored origin,
explicit replacement evidence, or unresolved provenance routes to
`RECHECK_REQUIRED`, `STALE`, or `UNKNOWN` as declared by the installed
continuity profile.

Beyond recall remains `CANDIDATE`. Adoption creates a new Current Anchor at
current Host physical time; it does not reactivate the old coordinate.

Currentness is not authority, execution assignment, write permission, or proof
that a previous active operation may continue.
