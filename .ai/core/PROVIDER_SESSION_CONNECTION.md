# Provider Session Connection

Status: Active Core Runtime Contract
Scope: Host connection to a provider-owned conversation session

## Purpose

This contract defines the minimum coordinate that a Host may retain when it
connects a Mode session to a provider.

It does not define Provider session currentness, Mode currentness, Anchor
currentness, authority, Assignment, or execution permission.

## Connection Coordinate

For each connection target, retain exactly one coordinate:

```yaml
last_provider: <provider-or-UNKNOWN>
last_session_ref: <provider-session-ref-or-UNKNOWN>
```

The target may be a Universe Conductor or a Project Master. The coordinate is
target-keyed, not Provider-keyed and not Mode-keyed.

Do not retain a parallel session map per Provider. A newly opened Provider
Session replaces the previous coordinate for that target.

## Open And Resume

The Host supplies the last coordinate when opening a Provider Session. The
Provider reports the Provider and Session Ref that it actually opened.

```text
same Provider + same Session Ref
  -> REUSED
  -> resume the opened Session
  -> no Mode greeting

missing coordinate, changed Provider, or changed Session Ref
  -> NEW or REPLACED
  -> store the opened coordinate
  -> send the requested Mode greeting once
```

The greeting carries Mode intent only. The opened Session resolves the Mode
through the Mode Registry and performs its own preparation and currentness
checks.

The Host must not infer that a requested Mode became active.

## Mode Session And Task Frame Boundary

A connected Conductor or Master is a persistent Mode Session connection.

A Task Frame Boss or Worker is a bounded execution and must be ephemeral:

```text
MODE_SESSION
  -> LAST_COORDINATE

TASK_FRAME_BOSS
TASK_FRAME_WORKER
  -> EPHEMERAL
  -> do not read, replace, or persist the target's last coordinate
```

Closing an ephemeral execution must not leave a durable Provider conversation
owned by the Host. If the Provider cannot attest this boundary, the Host must
report it as `UNKNOWN`; it must not claim ephemeral cleanup.

## Universe Entry Intent

The common intent mapping is:

```text
Universe application entry -> requested Mode: CONDUCTOR
Project Master connection   -> requested Mode: MASTER
```

Universe stores and forwards the connection coordinate and requested Mode. It
does not implement Mode, BOOT, Anchor, currentness, authority, or Assignment.

## Non-Authority Rule

```text
Provider Session Connection != currentness
Provider Session Connection != Anchor
Provider Session Connection != authority
Provider Session Connection != execution permission
```

Provider connection evidence is routing evidence only.
