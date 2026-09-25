# Follow-up registration after completion

Status: Master/Worker operating instructions, using the existing candidate API.
This is not a new scheduler or a server-side parser of free-text results.

- Unmet original acceptance criteria remain defects: REWORK, not a new optional
  candidate used to justify PASS.
- Genuine additional improvements are recorded without another registration
  question as project IDEA candidates in REVIEW_REQUIRED.
- Existing authorized work inside an open Goal stays in that Goal's Todo workflow.
- Completed Goals remain completed. Candidate registration does not create a
  Goal, Todo, adoption, or execution grant. Adoption follows the existing
  Memory/Galaxy path and creates a separate Fleet Goal when appropriate.

The Worker/Reviewer reports follow-ups with proposed work, reason, acceptance
criteria and immutable evidence. The Master checks existing project candidates
and records/reuses them before its final report. It reports candidate IDs, not
just prose. A failure to persist must be reported as unregistered with the actual
error. No follow-up means NONE, not an empty candidate.

Use authenticated `POST /v1/projects/{project_id}/memory-candidates`:

```json
{
  "stage": "SYNTHESIZE",
  "kind": "IDEA",
  "state": "REVIEW_REQUIRED",
  "summary": "Title; proposed work; reason; acceptance criteria; original Goal/Todo references",
  "source_session": {
    "source_id": "<original run ID>",
    "source_ref": "universe://todo/<original Todo ID>"
  },
  "ref_digests": ["<actual immutable evidence SHA-256>"]
}
```

Keep the exact payload on retry. The existing content identity returns the same
candidate with MEMORY_CANDIDATE_ALREADY_RECORDED; semantic duplicates require
Master inspection, not invented automatic semantic matching. Do not rephrase
ignored/superseded suggestions to evade their recorded disposition. Summaries
are bounded to 1600 characters and must not include secrets or raw transcripts.

The common node-Master driver includes this rule for all providers. Newly
launched Worker/Reviewer roles receive the reporting rule. Existing immutable
assignments and already-delivered instructions are not rewritten, and historical
free-text follow-ups are not silently backfilled.
