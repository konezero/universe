# P0 Receipt-Aware Streaming and Server Modularization

Status: IMPLEMENTATION_IN_PROGRESS
Priority: P0
Observed by: Universe dogfood

Progress at 2026-08-10:

- Runtime streaming payload store, receipt descriptor binding, byte-splice apply,
  CLI transport, compatibility path, failure cleanup, and 233-test Runtime
  regression: complete in immutable ai-career commit
  `1b882d40842abc93646745bc62b95c7f82e465e7`.
- Universe OS_UPDATE: PASS / VERIFIED with lifecycle receipt
  `host-runtime-lifecycle-8a4a393a364d2992a10aeddd` and validation ID
  `16ba3f04db51353cb47e3c3c4799ab589b7331569bdd255ffa4b53e578635ca9`.
- Live dogfood: a MODIFY whose Base64 postimage exceeded the former 1 MiB
  request cap and a 1,245,184-byte CREATE both applied successfully. Interrupted
  upload, wrong digest, stale preimage, expired receipt, receipt and payload
  replay, and payload cleanup all failed closed without target residue.
- Universe connection/auth/HTTP transport, SSE hubs, and Memory batch
  configuration service: extracted behind the compatibility entrypoint with
  dedicated unit contracts.
- Bench aggregation and comparison are now extracted to
  `tools/universe_app/bench_service.py` behind the legacy server entrypoint,
  with focused contract coverage.
- Regression: focused Bench/server 121 tests + 8 subtests and full 601 tests +
  40 subtests; all passed on 2026-08-11.
- Remaining extraction: Memory execution, Bench persistence, Session/Provider,
  storage, API routes, and CLI/bootstrap boundaries.

## Problem

Universe currently sends a complete postimage as Base64 inside a generic JSON
request for every receipt-aware file mutation. The installed Runtime CLI rejects
request files above 1 MiB before the Host mutation gateway can consume the
receipt. A 968,394-byte Python source file expands to about 1.23 MiB before JSON
metadata is added.

The same dogfood pass exposed two compounding costs:

- `tools/universe_server.py` is about 23,000 lines and 946 KiB, so unrelated
  domains share one mutation and test boundary;
- the complete Python regression suite runs 553 tests in about five minutes,
  making small changes expensive to validate.

## Invariants

1. Payload transport and mutation semantics are separate.
2. Payload bytes use a bounded streaming upload; JSON carries metadata only.
3. Upload completes before the one-time Mutation Receipt is issued.
4. Text modification defaults to an exact delta patch bound to preimage,
   patch, and expected postimage SHA-256 values.
5. New files, binary files, and inefficient deltas use streamed full content.
6. Apply is atomic and fails closed on digest, size, path, receipt, preimage,
   expiry, or replay mismatch.
7. Existing inline Base64 requests remain a bounded compatibility path.
8. Public Universe API, SQLite, CLI, and packaging contracts remain stable
   while modules are extracted.

## Delivery Sequence

### P0.1 Runtime payload staging

- Add a Runtime-owned, loopback-only streaming payload endpoint.
- Store staged payloads under the Runtime tmp root with opaque references.
- Record byte length and SHA-256 while streaming with a fixed upper bound.
- Reject path traversal, reparse points, digest mismatch, truncation, and reuse.
- Bind Guard receipts only after staging has completed.

### P0.2 Receipt-aware patch apply

- Add a deterministic text patch format and exact preimage requirement.
- Stage patch bytes through the same streaming endpoint.
- Apply to a temporary sibling, verify expected postimage SHA-256, fsync, and
  atomically replace the target.
- Fall back to streamed full content for CREATE, binary content, or inefficient
  patches.

### P0.3 Universe dogfood

- OS_UPDATE Universe from the fixed ai-career Runtime source.
- Verify a source edit whose full postimage exceeds the old 1 MiB request cap.
- Verify unknown-size CREATE, interrupted upload, wrong digest, stale preimage,
  expired receipt, replay, and cleanup.

### P0.4 Server extraction

Keep `tools/universe_server.py` as a compatibility entrypoint and extract in
this order:

1. pure contracts and normalization;
2. Memory and Bench services;
3. Session and Provider services;
4. SQLite schema and repositories behind `UniverseStore`;
5. HTTP routes and SSE hubs;
6. CLI and service bootstrap.

Target package:

```text
tools/universe_app/
  contracts/
  storage/
  services/
  api/
  runtime/
  cli.py
```

### P0.5 Regression tiers

- changed-module unit suite: target <= 30 seconds;
- API/DB contract suite: target <= 90 seconds;
- full Python regression before push;
- real Provider, browser, restart, quota, and long-running probes in dogfood or
  scheduled validation;
- parallelize only tests that do not share ports, process lifetime, or SQLite
  state.

## Completion Criteria

- No normal source edit requires a Base64 full-file JSON request.
- Unknown-size CREATE is accepted through bounded streaming.
- Existing text modification transmits only a delta unless fallback is needed.
- Failed transport or apply leaves the target unchanged.
- Receipt, payload, and temporary objects are single-use and cleaned up.
- Existing API, DB, CLI, packaging, and full regression contracts pass.
- `tools/universe_server.py` becomes a thin compatibility entrypoint.
- Ordinary changed-module validation completes within the target tier.
