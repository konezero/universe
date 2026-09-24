# Galaxy proposals, Memory sources, and Fleet Goal nodes

Status: product design decision (2026-09-23); partial implementation, reconciliation ongoing.
Scope: project-local proposal intake, prediction display, Goal work, and Memory/RAG automation.

## Distinct identities

| Object | Purpose | Surface |
|---|---|---|
| Memory source | Preserve an idea, forecast, or product/Goal proposal with its provenance and rationale. | Project Memory menu |
| Galaxy node / predicted path | Show a possible future or direction, including its assumptions and source Memory. It is not a work queue. | Galaxy; the same project's Memory menu shows its proposal and decision state. |
| Fleet Goal node | A finite work unit with completion criteria and its own Todos, execution, and outcome. | Fleet |
| RAG reference | Source-grounded operational knowledge such as Bench evidence, development documents, and cautions. | Retrieval and relevant knowledge/reference views |

Galaxy and Fleet nodes are not the same record or lifecycle. A Fleet Goal records
which Galaxy proposal/path it was derived from, when there is one. Manual or
LLM-authored Goals need not invent a Galaxy origin. A Galaxy proposal can remain
after a related Goal finishes and can lead to more than one distinct follow-up
Goal over time. Keep the source and Goal identities, project ownership, and
lineage explicit; never infer the relation from matching titles.

## Project-local flow

```text
scheduled collection (assigned LLM) -> LLM noise removal / classification
  -> Memory: project-local ideas, predictions, and proposals
       -> one proposal and decision state visible in Memory and Galaxy
       -> user accepts from either surface
       -> create one linked, finite Fleet Goal node for that accepted direction
       -> define its completion criteria and Todos -> execute -> verify outcome
       -> Goal DONE -> stop that Goal's automation; retain history and lineage
       -> later follow-up work -> a new linked Goal, not reopening the DONE Goal
  -> RAG: Bench, development references, cautions, and other operational evidence
       -> validate provenance, applicability, and currentness
       -> register for bounded retrieval without a per-item user-adoption gate
```

Memory and Galaxy are two views/actions over the same project proposal. An
acceptance from either must be visible in both and must not create duplicate
Goals on retry. Rejection or deferral does not start a Goal or execution. A
substantively new follow-up Goal is a new work identity even if it concerns the
same Galaxy node; it links to the source proposal and, when useful, to its
predecessor Goal. Completion of all Todo work does not make the source Galaxy
prediction disappear. Prediction-versus-outcome evidence can feed the next
forecast without reopening completed work.

Fleet shows accepted or otherwise explicitly created Goal work nodes, their
Todos, and execution status. A proposed Galaxy node is not automatically a
Fleet work card. A Goal is not the Galaxy node renamed. No Feature Node is a
mandatory intermediary between proposal acceptance and Goal creation. The
existing `FEATURE`/`CAPABILITY` graph and `feature.create` operations describe
the current software-domain implementation, not this product identity rule.

## Two automation lifecycles

The project Memory/RAG collector is configured and observed in Ops: stage LLM,
schedule, quota/budget, failure and retry state. It runs independently of any
Fleet Goal, Todo, or Feature Node lifecycle. No DONE Todo needs reopening to
make collection recur. The Goal executor instead operates only while that
Goal has authorized, unfinished work and terminates when the Goal completes.

The user decision is whether to pursue a proposal as a Goal, not a checkpoint
in every collection batch. An operational RAG reference is not a prediction to
accept in Galaxy; registration requires the appropriate source checks and does
not grant execution authority. The retrieval layer must not present an
unverified forecast as an established implementation fact.

## Reconciliation with the current implementation

This is a target product contract, not a claim that existing APIs or data have
been migrated. Current Memory Candidate `KEEP` and `rag.adopt` review a
`MEMORY` candidate and create canonical project Memory; they do not implement
the Galaxy-proposal-to-Fleet-Goal transition above. Current Memory-to-Feature
automation proposes a `Feature Node` and then a separate Goal. The unified
graph document attaches Todo ownership to structural nodes. Fleet UI also
uses structural/Feature nodes today. A separate proposal-to-Goal Action and Fleet Goal UI projection now exist, as
does a paged Galaxy/Knowledge proposal-node projection. The Memory menu now
shows those same project-local proposals rather than the operational RAG queue;
Ops retains the old manual RAG review in a labeled legacy diagnostic. Accepted
Goals expose their source candidate ID/digest; manual Goals have no invented
origin. A USER-only `goal.create-follow-up` Action now creates a new Goal from a
DONE predecessor with preserved proposal lineage and an explicit predecessor ID;
Fleet exposes it on completed proposal-backed Goal cards. Replays with the same
Goal ID and payload return the original Goal. This does not reopen the predecessor,
create Todos, or start execution. The scheduled second LLM pass, automatic
source-checked RAG ingestion, complete Galaxy lineage graph, and old
Feature/Goal migration still require reconciliation before the full pipeline
can be called compliant.

Fleet Goal IDs can now own a Master Persona assignment and a bounded Persona
automation run directly. The run selects Todos by the exact `goal_id`; existing
Feature-bound runs continue to select by `node_ref`. Goal-scoped runs do not
grant a project-wide Master scope. This is distinct from the older Feature
Expected Path / Work Plan Goal automation surface, which still requires an
adopted plan and must not be presented as the state of the Persona run.

Do not silently reinterpret existing IDs/states, auto-adopt historical records,
reopen completed Goals, or migrate live data based on this document alone.
