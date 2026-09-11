# Node hover Memory and RAG (2026-09-11)

Direct instruction: add documents and memories to each node hover, distinguish types and adoption states, permit explicit review and RAG adoption.

Evidence: canonical project memories carry node_ref and link_state. Candidate relations target other candidates, not functional nodes, and current candidates often have no node association. The existing rag.adopt Action Gateway requires MEMORY + KEEP with a persisted review and expected digest; canonical origin_ref identifies the exact adopted candidate. No matching by title or guessed semantic similarity is used.

Implementation: blue document links, purple memory links, green stored/adopted badges, yellow pending badges. Connected/proposed memories are scoped to their node; unlinked candidates and memories are on the project hover. A memory modal reads body/summary and offers only server-allowed KEEP/IGNORE; RAG adoption remains separate via rag.adopt. Refresh fetches the exact candidate in its project. Buttons disable during a request, errors retain their shared API details. No linking, review, or adoption is automatic.

Validation:
- PASS: node tests/test_hover_memory_ui.js, using production functions and isolated fake API/DOM. Covers scoped linked/proposed/unlinked entries, review error, KEEP without adoption, digest-bound gateway call, adopted duplicate suppression.
- PASS: node syntax and git diff --check.
- PASS: live read-only project memories (71 entries) and exact candidate GET (1 entry).
- NOT_RUN: mutation of real user candidates; these were intentionally left for user choice.

Limits: candidate hover currently fetches up to 200 entries and indicates the cap. Node-less candidates are not assigned to nodes. Source conversation is not reconstructed; the stored candidate summary is shown.

Browser PASS: real Terminal node showed its one document and four PROPOSED memories; project hover showed unlinked memories/candidates; clicking mem-08 displayed its stored body. No production review/adoption was performed.
