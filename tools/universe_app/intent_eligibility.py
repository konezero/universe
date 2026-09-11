"""Shared product-intent eligibility classification.

Historically `review_inbox_next_work._classify_text` decided on its own
whether a piece of review-inbox text was real product intent, test-only, or
generic filler, using a keyword blocklist. That blocklist misses anything it
was not written for -- e.g. a CLI self-check phrase like "return exactly
CODEX_CLI_OK with no additional text" reads as ordinary multi-token text and
classifies as PRODUCT_INTENT. Per
docs/memory-candidate-decision-contract-20260911.md §3, the fix is not more
per-workflow strings but one shared source/purpose boundary that every caller
converges on.

``origin_ref``-based detection is the intended long-term signal (a CLI
verification loop or fixture run should tag its own output at creation time,
e.g. ``universe://cli-probe/...``), but nothing in this codebase sets such a
tag yet -- confirming and wiring that is out of scope for this slice (see the
contract doc's "미확정" note). This module still takes ``origin_ref`` as an
explicit parameter so that day one caller change is additive, not another
rewrite: today it is always None/absent and every caller falls through to the
existing text heuristic unchanged.
"""

from __future__ import annotations

import re
from typing import Any

PRODUCT_INTENT = "PRODUCT_INTENT"
TEST_ONLY = "TEST_ONLY"
GENERIC = "GENERIC"

# A source/purpose tag known ahead of time to never carry product intent,
# regardless of its text content. Prefixes, matched against origin_ref.
TEST_ONLY_ORIGIN_PREFIXES = (
    "universe://cli-probe/",
    "universe://fixture/",
)

TEST_ONLY_MARKERS = (
    "test-only",
    "respond with",
    "do not implement",
    "unit test fixture",
)
GENERIC_MARKERS = (
    "generic summary",
    "no summary",
    "placeholder",
    "lorem ipsum",
)
_TOKEN_RE = re.compile(r"[a-z0-9가-힣]{2,}", re.IGNORECASE)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(value)}


def classify_intent_eligibility(
    *, source_kind: str = "", origin_ref: str | None = None, text: str = ""
) -> str:
    """PRODUCT_INTENT | TEST_ONLY | GENERIC -- the one classification point.

    ``source_kind`` is accepted for callers that already have it at hand and
    for future origin-aware rules; it is not yet used to decide anything on
    its own (only ``origin_ref`` prefixes are, when present).
    """

    normalized_origin = _text(origin_ref)
    if normalized_origin and any(
        normalized_origin.startswith(prefix) for prefix in TEST_ONLY_ORIGIN_PREFIXES
    ):
        return TEST_ONLY
    folded = _text(text).casefold()
    if not folded:
        return GENERIC
    if any(marker in folded for marker in TEST_ONLY_MARKERS):
        return TEST_ONLY
    if any(marker in folded for marker in GENERIC_MARKERS):
        return GENERIC
    if len(_tokens(folded)) < 2:
        return GENERIC
    return PRODUCT_INTENT
