# Universe Connection Template

Status: active project contract template

## Purpose

This template defines the Project-owned boundary for connecting to one
Universe. It is a queue publication contract, not a Carrier installation,
scheduler instruction, execution grant, or source mutation route.

## Ownership

```text
Project Master
  owns Project-to-Universe publication approval and Project-local receipts.

Universe
  owns asynchronous ingest, Bench aggregation, and promotion candidates.

Career Carrier
  reads only Universe-to-Career promotion candidates.
```

Installing this template does not create a connection, queue record, endpoint,
token, scheduler, `.ai/universe/` asset set, or Runtime identity.

## Connection Record

After explicit Project approval, the Project may create a connection record
under its Project-owned Universe surface. The record must name the selected
Universe reference, permitted publication schemas, redaction policy, and
provider receipt requirement. It must not contain secrets or turn a Universe
reference into execution authority.

## Publication Boundary

Only bounded, redacted candidates may cross from Project to Universe:

```text
Project Task Frame or Project Master
  -> approved Project-to-Universe queue candidate
  -> provider queue receipt
  -> Universe queue consumer
  -> Universe Bench / composition input
```

The Project retains raw source, prompts, unredacted logs, Worker transcripts,
and source mutation authority. A scheduler may wake a publisher but does not
replace an approved queue candidate or receipt.

A queue receipt proves only that the redacted candidate reached the declared
Universe durable queue. It does not prove Bench ingestion, Career promotion,
Project archive append, source mutation, or execution authority.

## Non-Goals

- Do not install a Project Carrier.
- Do not create a direct Project-to-Career handoff.
- Do not let Universe execute Project source work.
- Do not publish without the applicable Project approval and provider evidence.
