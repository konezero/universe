# Verification annotations

These annotations do not rewrite frozen MODEL_RECALL packs.

## Pack freeze

All six SHA-256 values in `digests/frozen-pack-index.json` were independently checked against the frozen pack files and raw envelopes during Meeting Room round 2 (Claude). Result: 3/3 frozen packs match, 3/3 raw envelopes match.

## Meeting transport

- Codex fresh reviewer: `CODEX_TURN_TIMED_OUT` in both rounds.
- Grok fresh reviewer: `GROK_ACP_RESPONSE_MISSING` in both rounds.
- Claude round-2 packet truncated the round-1 excerpt mid-token when fanned back. Full round-1 text is preserved in `meeting/meeting-transcript.json`.

## Web verification

NOT_RUN in this experiment. Highest-value later checks named by the meeting, still unverified:

1. `claude-industrial-design-cockpit-mode-error-1980s` — investigators citing mode confusion.
2. `claude-dashboards-overview-first-alarm-1990s` — alarm rationalization.
3. `grok-dashboard-alarm-vs-status` — silenced annunciators as standing critique.

Do not promote those claims to adopted lineage until a separate annotation cites a source with an observation date.
