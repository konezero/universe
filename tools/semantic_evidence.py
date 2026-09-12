"""Canonical digest of exact transient semantic text (observer wire contract)."""
import hashlib
import json

def semantic_text_digest(text: str) -> str:
    encoded = json.dumps(text, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
