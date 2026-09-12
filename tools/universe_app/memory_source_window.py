"""Bound automatic collection to the extraction contract and retain a source cursor."""
import json
from memory_fast_extract import normalize_transient_semantic_evidence
from provider_session_observer import ProviderSessionObserverError
from .connection import UniverseError

RECOVERABLE_SOURCE_ERRORS = {
    "SOURCE_NOT_FOUND", "SEMANTIC_SOURCE_NOT_CURRENT", "SEMANTIC_EVIDENCE_EMPTY",
    "SEMANTIC_ACTIVITY_NOT_ATTESTED",
}

def select_source_window(store, project_id):
    inventory = store.list_provider_session_sources()
    source_ids = sorted({item["source_id"] for item in inventory
                         if item.get("enabled", True) and item.get("status") == "ACTIVE"})
    if not source_ids:
        raise UniverseError("MEMORY_BATCH_SOURCES_UNAVAILABLE", "No active registered collection sources", 409)
    position = store.get_memory_source_position(project_id)
    next_id = position.get("next_source_id")
    if next_id in source_ids:
        index = source_ids.index(next_id)
        source_ids = source_ids[index:] + source_ids[:index]
    selected = []
    skipped = []
    skipped_count = 0
    text_chars = 0
    excerpt_count = 0
    next_source_id = source_ids[0]
    for index, source_id in enumerate(source_ids):
        next_source_id = source_id
        if len(selected) >= 64 or text_chars >= 32000 or excerpt_count >= 256:
            break
        try:
            batch = store.prepare_provider_activity_batch(source_id)
            refs = batch.get("activity_refs") or []
            if not refs:
                code = "NO_ACTIVITY"
                evidence = None
            else:
                evidence = store.provider_session_observer.build_transient_semantic_evidence(source_id, refs)
                evidence = normalize_transient_semantic_evidence(evidence)
                code = None
        except (UniverseError, ProviderSessionObserverError) as error:
            if error.code not in RECOVERABLE_SOURCE_ERRORS:
                raise
            code = error.code
            evidence = None
        if code:
            skipped_count += 1
            if len(skipped) < 64:
                skipped.append({"source_id": source_id, "error_code": code})
        else:
            chars = sum(len(item["text"]) for item in evidence)
            if text_chars + chars > 32000 or excerpt_count + len(evidence) > 256:
                break
            selected.append(source_id)
            text_chars += chars
            excerpt_count += len(evidence)
        next_source_id = source_ids[(index + 1) % len(source_ids)]
    report = {
        "schema": "universe.memory-source-window.v1",
        "selected_source_ids": selected, "selected_count": len(selected),
        "skipped_count": skipped_count, "skipped": skipped,
        "deferred_count": len(source_ids) - len(selected) - skipped_count,
        "next_source_id": next_source_id,
    }
    if not selected:
        raise UniverseError("MEMORY_BATCH_SOURCES_UNAVAILABLE",
                            json.dumps(report, ensure_ascii=True, separators=(",", ":")), 409)
    return selected, report
