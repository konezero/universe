"""Bound automatic collection to the extraction contract and retain a source cursor."""
import json
from memory_fast_extract import FAST_EXTRACT_SOURCE_PROVIDERS, FastExtractError, normalize_transient_semantic_evidence
from provider_session_observer import ProviderSessionObserverError
from .connection import UniverseError

RECOVERABLE_SOURCE_ERRORS = {
    "SOURCE_NOT_FOUND", "SEMANTIC_SOURCE_NOT_CURRENT", "SEMANTIC_EVIDENCE_EMPTY",
    "SEMANTIC_ACTIVITY_NOT_ATTESTED", "SEMANTIC_EVIDENCE_WINDOW_LIMIT",
}

def select_source_window(store, project_id):
    inventory = store.list_provider_session_sources()
    ownership_visible = any("ownership_state" in item for item in inventory)
    active_sources = [
        item
        for item in inventory
        if item.get("enabled", True) and item.get("status") == "ACTIVE"
    ]
    ownership_filtered_count = sum(
        1
        for item in active_sources
        if ownership_visible
        and not (
            str(item.get("ownership_state") or "").upper() == "ASSIGNED"
            and str(item.get("owner_project_id") or "") == str(project_id)
        )
    )
    unassigned_count = sum(
        1
        for item in active_sources
        if ownership_visible
        and (
            str(item.get("ownership_state") or "").upper() != "ASSIGNED"
            or str(item.get("owner_project_id") or "") == "UNASSIGNED"
        )
    )
    source_ids = sorted({item["source_id"] for item in inventory
                         if item.get("enabled", True) and item.get("status") == "ACTIVE"
                         and (not ownership_visible or (
                             str(item.get("ownership_state") or "").upper() == "ASSIGNED"
                             and str(item.get("owner_project_id") or "") == str(project_id)
                         ))})
    if not source_ids:
        detail = json.dumps(
             {"active_count": len(active_sources),
              "unassigned_count": unassigned_count,
              "ownership_filtered_count": ownership_filtered_count,
              "project_id": project_id},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        raise UniverseError("MEMORY_BATCH_SOURCES_UNAVAILABLE", detail, 409)
    position = store.get_memory_source_position(project_id)
    resume = position.get("selection", {}).get("activity_resume")
    next_id = position.get("next_source_id")
    # Ownership changes can remove a still-active source from this project's
    # eligible set. Preserve its exact cursor without blocking healthy sources.
    suspended = dict(position.get("selection", {}).get("suspended_activity_resumes") or {})
    if resume and resume["source_id"] not in source_ids:
        suspended[resume["source_id"]] = resume
        resume = None
    if resume is None:
        restored_id = next((sid for sid in source_ids if sid in suspended), None)
        if restored_id is not None:
            resume = suspended.pop(restored_id)
            next_id = restored_id
    if next_id in source_ids:
        index = source_ids.index(next_id)
        source_ids = source_ids[index:] + source_ids[:index]
    selected = []
    windows = {}
    pending_resume = None
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
            refs = sorted(batch.get("activity_refs") or [], key=lambda ref: (ref.get("ordinal", 0), ref.get("activity_id", "")))
            if resume and resume["source_id"] == source_id:
                previous = resume["after"]
                if previous not in refs:
                    raise UniverseError("MEMORY_ACTIVITY_CURSOR_STALE", "The last processed activity no longer matches this source", 409)
                refs = refs[refs.index(previous) + (0 if resume.get("semantic_page") else 1):]
            page = None
            saved_page = resume.get("semantic_page") if resume and resume["source_id"] == source_id else None
            remaining_refs = refs
            refs = refs[:512]
            provider = str((batch.get("source") or {}).get("provider", "")).strip().upper()
            if provider and provider not in FAST_EXTRACT_SOURCE_PROVIDERS:
                code = "FAST_EXTRACT_PROVIDER_INVALID"
                evidence = None
            elif not refs:
                code = "NO_ACTIVITY"
                evidence = None
            else:
                offset = 0
                if saved_page:
                    refs = refs[:1]
                    page = dict(saved_page)
                while True:
                    try:
                        evidence = store.provider_session_observer.build_transient_semantic_evidence(source_id, refs, require_complete=True, **({"semantic_page": page} if page is not None else {}))
                        evidence = normalize_transient_semantic_evidence(evidence)
                        break
                    except ProviderSessionObserverError as error:
                        if error.code == "SEMANTIC_EVIDENCE_EMPTY" and offset + len(refs) < len(remaining_refs):
                            offset += len(refs)
                            refs = remaining_refs[offset:offset + 512]
                            continue
                        if error.code != "SEMANTIC_EVIDENCE_WINDOW_LIMIT" or page is not None:
                            raise
                        if len(refs) == 1:
                            page = {"start": 0}
                        else:
                            refs = refs[:max(1, len(refs) // 2)]
                code = None
        except (UniverseError, ProviderSessionObserverError, FastExtractError) as error:
            if (resume and resume["source_id"] == source_id) or error.code not in RECOVERABLE_SOURCE_ERRORS:
                if not isinstance(error, UniverseError):
                    raise UniverseError(error.code, error.detail, 409) from error
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
            windows[source_id] = {"source": batch["source"], "activity_refs": refs}
            if page is not None:
                windows[source_id]["semantic_page"] = {key: page[key] for key in ("start", "digest")}
            if page is not None and page["next_start"] is not None:
                pending_resume = {"source_id": source_id, "after": refs[-1],
                                  "semantic_page": {"start": page["next_start"], "digest": page["digest"]}}
            elif offset + len(refs) < len(remaining_refs):
                pending_resume = {"source_id": source_id, "after": refs[-1]}
            text_chars += chars
            excerpt_count += len(evidence)
        if pending_resume:
            next_source_id = source_id
            break
        next_source_id = source_ids[(index + 1) % len(source_ids)]
    report = {
        "schema": "universe.memory-source-window.v1",
        "selected_source_ids": selected, "selected_count": len(selected),
        "skipped_count": skipped_count, "skipped": skipped,
        "deferred_count": len(source_ids) - len(selected) - skipped_count,
         "ownership_filtered_count": ownership_filtered_count,
         "unassigned_count": unassigned_count,
        "ownership_project_id": project_id,
        "next_source_id": next_source_id,
        "activity_windows": windows,
        "activity_resume": pending_resume,
        "suspended_activity_resumes": suspended,
        "previous_run_id": position.get("last_run_id"),
    }
    if not selected:
        raise UniverseError("MEMORY_BATCH_SOURCES_UNAVAILABLE",
                            json.dumps(report, ensure_ascii=True, separators=(",", ":")), 409)
    return selected, report


def apply_activity_window(batch, window):
    """Revalidate a Host-selected window; HTTP callers cannot provide windows."""
    source = batch.get("source") or {}
    expected = window["source"]
    if any(source.get(key) != expected.get(key) for key in ("source_id", "provider", "provider_session_id", "origin_project_id", "owner_project_id", "ownership_state")):
        raise UniverseError("MEMORY_ACTIVITY_WINDOW_STALE", "Source identity changed after selection", 409)
    current = batch.get("activity_refs") or []
    refs = window["activity_refs"]
    if not refs or any(ref not in current for ref in refs):
        raise UniverseError("MEMORY_ACTIVITY_WINDOW_STALE", "Selected activity changed after selection", 409)
    return {**batch, "activity_refs": refs}
