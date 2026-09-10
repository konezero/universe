"""Shared secret-like value checks for bounded knowledge summaries."""

import re


SECRET_VALUE_PATTERNS = (
    re.compile(r"\b(?:sk|xai|ghp|github_pat)-[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*\S{8,}",
        re.IGNORECASE,
    ),
)
