---
name: conversation-recall
description: Retrieve bounded archive context as candidate evidence for Current Anchor realignment.
---

# Conversation Recall

Invocation class: `REFERENCE_RUNTIME_READ_ONLY`

Invoke `conversation-recall query` only over an explicit caller-supplied
archive or conversation index. The deterministic runtime performs bounded
lexical retrieval and returns source-linked candidate records.

The reasoning layer may summarize the returned records, but recalled material
remains historical evidence until the Current Parent adopts it. Recall must not
silently activate an old Anchor, create authority, or claim semantic matches
that the deterministic query did not return.
