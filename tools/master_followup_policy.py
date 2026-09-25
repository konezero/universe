"""Provider-neutral completion guidance; candidate storage owns persistence."""

MASTER_FOLLOWUP_POLICY = (
    "Follow-up registration is part of your completion report. Do not ask whether to record "
    "an identified follow-up. First distinguish unmet original acceptance criteria from "
    "optional additional work: an original-scope defect requires REWORK, not a PASS hidden "
    "behind a follow-up. For genuine additional work, automatically record an unadopted "
    "project Memory IDEA candidate via POST /v1/projects/{project_id}/memory-candidates. "
    "Use stage=SYNTHESIZE, kind=IDEA, state=REVIEW_REQUIRED; summary (at most 1600 characters) "
    "must contain a title, proposed work, reason, acceptance criteria and original Goal/Todo "
    "references. Set source_session={source_id: run_id, source_ref: original Todo URI}, "
    "and ref_digests to the actual immutable Worker/Reviewer evidence digests. Read existing "
    "project candidates first; reuse an equivalent candidate and report its ID rather than "
    "creating duplicates. Preserve the exact payload on retry; the existing API reports "
    "MEMORY_CANDIDATE_RECORDED or MEMORY_CANDIDATE_ALREADY_RECORDED. Do not revive IGNORE or "
    "SUPERSEDED suggestions by rewording them. Never claim registration without a returned "
    "candidate_id. If registration fails, report the exact error and the unregistered "
    "follow-up explicitly; do not silently drop it or say it was saved. Do not reopen a "
    "completed Goal, adopt the candidate, create a new executable Goal/Todo, or start a "
    "Host solely because a follow-up exists. User adoption is the separate execution gate. "
    "Already-authorized remaining work inside the current Goal stays in its existing Todo "
    "workflow. If there is no follow-up, explicitly report NONE and create nothing. "
    "Final report: original acceptance outcome; registered/reused candidate IDs and source "
    "evidence; any registration errors; no automatic execution of new scope."
)

WORKER_FOLLOWUP_POLICY = (
    "In result_text (Worker) or note/next_action (Reviewer), distinguish original-scope "
    "acceptance defects from optional follow-up improvements. For each real improvement "
    "report proposed work, reason, acceptance criteria and evidence, or explicitly NONE. "
    "Do not register candidates, create new Goals/Todos, expand scope or weaken the verdict; "
    "the owning Master records follow-up candidates through the existing candidate API."
)
