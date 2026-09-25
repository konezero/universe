"""Master-owned per-Todo append journal; Workers receive pinned read-only slices.

The Todo DB remains the scheduling/state authority. This journal carries bounded
assignments and collected evidence, never credentials, permission or a new task.
Only server-owned launch/directive/collection and Conductor-request adapters
call append(). Workers receive pinned read-only slices.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Mapping

SCHEMA = 'universe.todo-execution-journal.v1'
MAX_RECORD_BYTES = 256 * 1024
READ_TOOL = {'type': 'function', 'name': 'universe_read_assignment',
    'description': 'Read the exact pinned Master Todo journal record for this role attempt. '
                   'No arguments: Host preserves path/range/identity and verifies SHA256. '
                   'Read this FIRST; do not reconstruct shell commands or references.',
    'inputSchema': {'type':'object','properties':{},'additionalProperties':False}}
_LOCK = threading.RLock()  # Single server writer; serialize its HTTP handlers.


class JournalError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code, self.detail, self.status = code, detail, 409
        super().__init__(detail)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def journal_path(project_root: Path, todo_id: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', todo_id):
        raise JournalError('TODO_JOURNAL_ID_INVALID', 'invalid Todo identifier')
    root = Path(project_root).resolve()
    path = root / '.ai/runtime/todo_journals' / (todo_id + '.jsonl')
    if path.resolve().parent != root / '.ai/runtime/todo_journals':
        raise JournalError('TODO_JOURNAL_PATH_INVALID', 'journal path must not be redirected')
    return path


def _reference(path: Path, offset: int, raw: bytes, record: Mapping[str, Any]) -> dict[str, Any]:
    return {'path': str(path), 'offset': offset, 'length': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest(), 'sequence': record['sequence'],
            'todo_id': record['todo_id'], 'run_id': record['run_id'],
            'task_frame_id': record['task_frame_id'], 'event_id': record['event_id']}


def records(path: Path) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows, offset = [], 0
    if not path.exists():
        return rows
    with path.open('rb') as stream:
        for raw in stream:
            if not raw.endswith(b'\n'):
                raise JournalError('TODO_JOURNAL_TAIL_INCOMPLETE', 'partial final record; no role may start')
            try:
                record = json.loads(raw)
                if (not isinstance(record, dict) or record.get('schema') != SCHEMA
                        or record.get('sequence') != len(rows) + 1):
                    raise ValueError('schema/sequence')
                expected = record.pop('record_digest')
                if digest(record) != expected:
                    raise ValueError('digest')
                record['record_digest'] = expected
            except (ValueError, KeyError, TypeError) as error:
                raise JournalError('TODO_JOURNAL_CORRUPT', f'invalid record at byte {offset}') from error
            rows.append((record, _reference(path, offset, raw, record)))
            offset += len(raw)
    return rows


def append(path: Path, *, todo_id: str, owner_ref: str, run_id: str,
           task_frame_id: str, event_id: str, kind: str,
           payload: Mapping[str, Any], _transfer_from: str | None = None) -> dict[str, Any]:
    """Called only after Master ownership validation at the Action boundary.

No remove/rename of the live journal. Exact retries reuse one event. A torn
tail fails closed and is preserved for explicit recovery, never silently lost.
"""
    if not all((todo_id, owner_ref, run_id, task_frame_id, event_id)):
        raise JournalError('TODO_JOURNAL_IDENTITY_REQUIRED', 'complete Master coordinates required')
    with _LOCK:
        rows = records(path)
        active_owner = None
        for record, _ in rows:
            if record['todo_id'] != todo_id:
                raise JournalError('TODO_JOURNAL_OWNER_MISMATCH', 'Todo identity changed')
            if record['kind'] == 'OWNER_TRANSFERRED':
                if active_owner != record['payload'].get('previous_owner_ref'):
                    raise JournalError('TODO_JOURNAL_CORRUPT', 'invalid ownership chain')
                active_owner = record['owner_ref']
            elif active_owner is None:
                active_owner = record['owner_ref']
            elif record['owner_ref'] != active_owner:
                raise JournalError('TODO_JOURNAL_CORRUPT', 'owner changed without transfer')
        if kind == 'OWNER_TRANSFERRED' and not _transfer_from:
            raise JournalError('TODO_JOURNAL_TRANSFER_REQUIRED', 'use the ownership transfer route')
        if _transfer_from is None and active_owner not in (None, owner_ref):
            raise JournalError('TODO_JOURNAL_OWNER_MISMATCH', 'explicit ownership transfer required')
        data = {'schema': SCHEMA, 'todo_id': todo_id, 'owner_ref': owner_ref,
                'run_id': run_id, 'task_frame_id': task_frame_id,
                'event_id': event_id, 'kind': kind, 'payload': dict(payload)}
        for record, ref in rows:
            if record['event_id'] == event_id:
                if {k: record[k] for k in data} != data:
                    raise JournalError('TODO_JOURNAL_REPLAY_CONFLICT', 'event payload changed')
                return ref
        if _transfer_from is not None and active_owner != _transfer_from:
            raise JournalError('TODO_JOURNAL_OWNER_MISMATCH', 'transfer source is not current owner')
        data['sequence'] = len(rows) + 1
        data['record_digest'] = digest(data)
        raw = (json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n').encode()
        if len(raw) > MAX_RECORD_BYTES:
            raise JournalError('TODO_JOURNAL_RECORD_TOO_LARGE', 'use bounded evidence references')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('ab', buffering=0) as stream:
            offset = stream.tell()
            if stream.write(raw) != len(raw):
                raise JournalError('TODO_JOURNAL_WRITE_INCOMPLETE', 'partial append; inspect preserved tail')
            os.fsync(stream.fileno())
        return _reference(path, offset, raw, data)


def transfer_owner(path: Path, *, todo_id: str, owner_ref: str, run_id: str,
                   previous_owner_ref: str, expected_sequence: int,
                   expected_digest: str, request_id: str, validate) -> dict[str, Any]:
    """Append-only CAS transfer; the server validates live ownership and Hosts.

    Validation and append share the journal writer lock with new assignments.
    Old pinned byte ranges and result provenance are never rewritten.
    """
    with _LOCK:
        rows = records(path)
        if not rows or previous_owner_ref == owner_ref:
            raise JournalError('TODO_JOURNAL_TRANSFER_INVALID', 'existing distinct owner required')
        validate(rows)
        event_id = 'owner-transfer:' + request_id
        replay = any(row['event_id'] == event_id for row, _ in rows)
        if not replay and (rows[-1][0]['sequence'] != expected_sequence
                           or rows[-1][0]['record_digest'] != expected_digest):
            raise JournalError('TODO_JOURNAL_TRANSFER_CONFLICT', 'journal changed; inspect current tail')
        return append(path, todo_id=todo_id, owner_ref=owner_ref, run_id=run_id,
                      task_frame_id=rows[-1][0]['task_frame_id'] if not replay else next(
                          row['task_frame_id'] for row, _ in rows if row['event_id'] == event_id),
                      event_id=event_id, kind='OWNER_TRANSFERRED',
                      payload={'previous_owner_ref': previous_owner_ref,
                               'expected_sequence': expected_sequence,
                               'expected_digest': expected_digest},
                      _transfer_from=previous_owner_ref)


def read_reference(ref: Mapping[str, Any], *, project_root: Path) -> dict[str, Any]:
    path = journal_path(project_root, str(ref['todo_id']))
    if Path(str(ref['path'])).resolve() != path.resolve():
        raise JournalError('TODO_JOURNAL_PATH_INVALID', 'reference is not this Todo journal')
    offset, length = ref['offset'], ref['length']
    if type(offset) is not int or offset < 0 or type(length) is not int or not 0 < length <= MAX_RECORD_BYTES:
        raise JournalError('TODO_JOURNAL_RANGE_INVALID', 'invalid bounded byte range')
    with path.open('rb') as stream:
        stream.seek(offset)
        raw = stream.read(length)
    if len(raw) != length or not raw.endswith(b'\n') or hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise JournalError('TODO_JOURNAL_REFERENCE_CHANGED', 'pinned assignment bytes changed')
    record = json.loads(raw)
    if any(record.get(k) != ref.get(k) for k in ('todo_id', 'run_id', 'task_frame_id', 'event_id', 'sequence')):
        raise JournalError('TODO_JOURNAL_REFERENCE_CHANGED', 'assignment identity mismatch')
    return record


def read_assignment_tool(arguments: Mapping[str, Any], *, project_root: Path,
                         reference: Mapping[str, Any]) -> dict[str, Any]:
    """Host-pinned read: model cannot substitute path, identity, range or digest."""
    if arguments:
        return {'status':'HOST_JOURNAL_BLOCKED','error_code':'TODO_JOURNAL_ARGUMENTS_DENIED',
                'repository_write':False}
    try:
        record = read_reference(reference, project_root=project_root)
        return {'status':'HOST_JOURNAL_READ','record':record,'repository_write':False}
    except (JournalError, OSError, ValueError, KeyError, TypeError) as error:
        return {'status':'HOST_JOURNAL_BLOCKED','error_code':getattr(error,'code','TODO_JOURNAL_UNAVAILABLE'),
                'error_type':type(error).__name__,'repository_write':False}


def collected_context(path: Path) -> dict[str, Any]:
    """Bounded resume context: latest collected result per role for this Todo."""
    latest = {}
    for record, _ in records(path):
        if record['kind'] == 'RESULT_COLLECTED' and record['payload'].get('role') in {'WORKER', 'REVIEWER'}:
            latest[record['payload']['role']] = record['payload']
    return latest


def assignment_reference(path: Path, frame_id: str, role: str, attempt: int) -> dict[str, Any]:
    matches = [ref for record, ref in records(path)
               if record['task_frame_id'] == frame_id and record['kind'] == 'ASSIGNED'
               and record['payload']['role'] == role and record['payload']['attempt'] == attempt]
    if len(matches) != 1:
        raise JournalError('TODO_JOURNAL_ASSIGNMENT_REQUIRED', 'one exact Master assignment required')
    return matches[0]


def append_directive_assignment(path: Path, *, frame_id: str, request_id: str,
                                directive: str, role: str | None, feedback: str | None) -> dict[str, Any]:
    with _LOCK:
        rows = records(path)
        same = [r for r, _ in rows if r['task_frame_id'] == frame_id]
        first = next((r for r in same if r['kind'] == 'ASSIGNED'), None)
        if first is None:
            raise JournalError('TODO_JOURNAL_ASSIGNMENT_REQUIRED', 'launch assignment missing')
        event_id = f'{frame_id}:directive:{request_id}'
        prior = next((r for r in same if r['event_id'] == event_id), None)
        if prior:
            if (prior['payload'].get('directive'), prior['payload'].get('role'), prior['payload'].get('feedback')) != (directive, role, feedback):
                raise JournalError('TODO_JOURNAL_REPLAY_CONFLICT', 'directive changed')
            return next(ref for r, ref in rows if r['event_id'] == event_id)
        collected = [r['payload'] for r in same if r['kind'] == 'RESULT_COLLECTED']
        latest = dict(first['payload'].get('collected_results') or {})
        for result in collected:
            if result.get('role'):
                latest[result['role']] = result
        if role == 'REVIEWER':
            worker_assignments = [r for r in same if r['kind'] == 'ASSIGNED'
                                  and r['payload'].get('role') == 'WORKER']
            current_worker = worker_assignments[-1] if worker_assignments else None
            current_collection = next((r for r in reversed(same)
                                       if r['kind'] == 'RESULT_COLLECTED'
                                       and r['payload'].get('role') == 'WORKER'
                                       and r['payload'].get('status') == 'COMPLETED'
                                       and r['payload'].get('attempt') ==
                                           current_worker['payload'].get('attempt')
                                       and r['payload'].get('result_ref')
                                       and r['payload'].get('result_digest')),
                                      None) if current_worker else None
            if (current_collection is None
                    or current_collection['sequence'] <= current_worker['sequence']):
                raise JournalError('TODO_JOURNAL_WORKER_COLLECTION_REQUIRED',
                                   'Master must collect this Host Worker attempt before review')
        payload = {'directive': directive, 'role': role, 'feedback': feedback}
        if directive != 'DONE':
            payload.update({k: first['payload'][k] for k in ('todo', 'persona', 'worker_write_scope')})
            payload['attempt'] = 1 + sum(r['kind'] == 'ASSIGNED' and r['payload']['role'] == role for r in same)
            payload['collected_results'] = latest
        return append(path, **{k: first[k] for k in ('todo_id', 'owner_ref', 'run_id')},
                      task_frame_id=frame_id, event_id=event_id,
                      kind='MASTER_DONE' if directive == 'DONE' else 'ASSIGNED', payload=payload)


def append_conductor_rework_request(path: Path, *, frame_id: str, request_id: str,
                                    conductor_anchor_ref: str, feedback: str,
                                    based_on_result_ref: str,
                                    based_on_result_digest: str) -> dict[str, Any]:
    """Record a Conductor request for the Master, pinned to collected Worker evidence."""
    if not all((frame_id, request_id, conductor_anchor_ref, feedback,
                based_on_result_ref, based_on_result_digest)):
        raise JournalError('TODO_JOURNAL_REWORK_REQUEST_INVALID', 'complete rework evidence required')
    event_id = f'{frame_id}:conductor-rework:{request_id}'
    payload = {'role': 'WORKER', 'feedback': feedback,
               'conductor_anchor_ref': conductor_anchor_ref,
               'based_on_result_ref': based_on_result_ref,
               'based_on_result_digest': based_on_result_digest}
    with _LOCK:
        same = [(record, ref) for record, ref in records(path)
                if record['task_frame_id'] == frame_id]
        first = next((record for record, _ in same if record['kind'] == 'ASSIGNED'), None)
        if first is None:
            raise JournalError('TODO_JOURNAL_ASSIGNMENT_REQUIRED', 'launch assignment missing')
        for record, ref in same:
            if record['event_id'] == event_id:
                if record['kind'] != 'CONDUCTOR_REWORK_REQUESTED' or record['payload'] != payload:
                    raise JournalError('TODO_JOURNAL_REPLAY_CONFLICT', 'rework request changed')
                return ref
        latest = next((record['payload'] for record, _ in reversed(same)
                       if record['kind'] == 'RESULT_COLLECTED'
                       and record['payload'].get('role') == 'WORKER'), None)
        if latest is None:
            raise JournalError('TODO_JOURNAL_WORKER_COLLECTION_REQUIRED',
                               'Master must collect Worker evidence before rework request')
        if (latest.get('result_ref') != based_on_result_ref
                or latest.get('result_digest') != based_on_result_digest):
            raise JournalError('TODO_JOURNAL_REWORK_EVIDENCE_STALE',
                               'a newer Worker result has been collected')
        return append(path, **{k: first[k] for k in ('todo_id', 'owner_ref', 'run_id')},
                      task_frame_id=frame_id, event_id=event_id,
                      kind='CONDUCTOR_REWORK_REQUESTED', payload=payload)


def validate_conductor_rework_request(path: Path, reference: Mapping[str, Any],
                                      *, frame_id: str, feedback: str) -> dict[str, Any]:
    """Check that a Master directive still cites the current Worker result."""
    with _LOCK:
        record = read_reference(reference, project_root=path.parents[3])
        if (record['kind'] != 'CONDUCTOR_REWORK_REQUESTED'
                or record['task_frame_id'] != frame_id
                or record['payload'].get('feedback') != feedback):
            raise JournalError('TODO_JOURNAL_REWORK_REQUEST_MISMATCH',
                               'directive does not match the pinned Conductor request')
        latest = next((item['payload'] for item, _ in reversed(records(path))
                       if item['task_frame_id'] == frame_id
                       and item['kind'] == 'RESULT_COLLECTED'
                       and item['payload'].get('role') == 'WORKER'), None)
        if (latest is None
                or latest.get('result_ref') != record['payload']['based_on_result_ref']
                or latest.get('result_digest') != record['payload']['based_on_result_digest']):
            raise JournalError('TODO_JOURNAL_REWORK_EVIDENCE_STALE',
                               'a newer Worker result has been collected')
        return record


def read_role_result(repository_root: Path, session_id: str, anchor_ref: str,
                     frame_id: str, todo_id: str, result_ref: str, result_digest: str,
                     *, expected_source_ref: str | None = None) -> dict[str, Any]:
    """Read the exact immutable Task Frame record; never trust a posted verdict."""
    match = re.fullmatch(re.escape('task-frame-result://' + frame_id) + r'_(worker|reviewer)_([1-9][0-9]*)/(worker|reviewer)-turn/(.+)', result_ref)
    if not match or match[1] != match[3]:
        raise JournalError('TODO_JOURNAL_RESULT_INVALID', 'result is outside this Host')
    role, attempt, _, receipt = match.groups()
    role_frame, turn_id = f'{frame_id}_{role}_{attempt}', role + '-turn'
    root = Path(repository_root).resolve()
    key = hashlib.sha256(f'{session_id}\0{role_frame}'.encode()).hexdigest()[:24]
    path = root / '.ai/runtime/task_frames' / (key + '.sqlite3')
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise JournalError('TODO_JOURNAL_RESULT_INVALID', 'immutable result unavailable')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        context = db.execute('SELECT * FROM task_frame_context').fetchone()
        turn = db.execute('SELECT * FROM task_turns WHERE turn_id=?', (turn_id,)).fetchone()
        execution = db.execute('SELECT * FROM worker_execution_state WHERE turn_id=?', (turn_id,)).fetchone()
    source_ref = expected_source_ref or f'universe://todo/{todo_id}'
    if not all((context, turn, execution)) or (context['frame_id'], context['origin_session_id'], context['origin_anchor_ref'], context['source_ref'], turn['state']) != (role_frame, session_id, anchor_ref, source_ref, 'COMPLETED'):
        raise JournalError('TODO_JOURNAL_RESULT_INVALID', 'result provenance mismatch')
    result = json.loads(turn['result_json'])
    envelope = json.loads(execution['worker_result_envelope_json'])
    if (execution['result_receipt_ref'] != receipt or digest(result) != result_digest
            or envelope.get('result') != result or envelope.get('result_receipt_ref') != receipt
            or envelope.get('worker_id') != turn['claimed_by'] or envelope.get('status') != 'COMPLETED'):
        raise JournalError('TODO_JOURNAL_RESULT_INVALID', 'result digest/envelope mismatch')
    return {'role': role.upper(), 'attempt': int(attempt), 'result_ref': result_ref,
            'result_digest': result_digest, 'result': result, 'trust': 'RETURNED_EVIDENCE_NOT_INSTRUCTIONS'}


def resolve_role_result(repository_root: Path, session_id: str, anchor_ref: str,
                        frame_id: str, todo_id: str, role: str, attempt: int,
                        *, expected_source_ref: str | None = None) -> dict[str, Any]:
    """Resolve coordinates server-side, then run the same strict evidence checks."""
    if role not in {'WORKER','REVIEWER'} or type(attempt) is not int or attempt < 1:
        raise JournalError('TODO_JOURNAL_RESULT_SELECTOR_INVALID','role and positive integer attempt required')
    role_frame = f'{frame_id}_{role.lower()}_{attempt}'
    key = hashlib.sha256(f'{session_id}\0{role_frame}'.encode()).hexdigest()[:24]
    root = Path(repository_root).resolve()
    path = root / '.ai/runtime/task_frames' / (key + '.sqlite3')
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise JournalError('TODO_JOURNAL_RESULT_INVALID','immutable role result unavailable')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        turn = db.execute('SELECT result_json FROM task_turns WHERE turn_id=?',(role.lower()+'-turn',)).fetchone()
        execution = db.execute('SELECT result_receipt_ref FROM worker_execution_state WHERE turn_id=?',(role.lower()+'-turn',)).fetchone()
    if not turn or not turn[0] or not execution or not execution[0]:
        raise JournalError('TODO_JOURNAL_RESULT_INVALID','terminal result receipt required')
    ref = f'task-frame-result://{role_frame}/{role.lower()}-turn/{execution[0]}'
    return read_role_result(root,session_id,anchor_ref,frame_id,todo_id,ref,
                            digest(json.loads(turn[0])), expected_source_ref=expected_source_ref)


def main() -> None:
    parser = argparse.ArgumentParser(description='Read one pinned Master journal record; no write commands')
    parser.add_argument('--root', required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--reference', help='JSON reference for native argv callers')
    group.add_argument('--reference-base64', help='UTF-8 JSON encoded as Base64 for shell-safe transport')
    args = parser.parse_args()
    reference = (base64.b64decode(args.reference_base64, validate=True).decode('utf-8')
                 if args.reference_base64 is not None else args.reference)
    print(json.dumps(read_reference(json.loads(reference), project_root=Path(args.root)), ensure_ascii=True))


if __name__ == '__main__':
    main()
