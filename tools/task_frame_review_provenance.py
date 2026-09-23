"""Validate rework selection against the Master-owned pinned Todo journal."""
from pathlib import Path
from typing import Any, Mapping

from todo_execution_journal import JournalError, journal_path, read_reference, records


def select_journal_review(root: Path, *, run_id: str, frame_id: str, todo_id: str,
                          owner_ref: str, launch_reference: Mapping[str, Any],
                          value: Mapping[str, Any], current_todo: Mapping[str, Any] | None):
    def reject(detail):
        raise JournalError('TASK_FRAME_RECOVERY_EVIDENCE_INVALID', detail)

    launch = read_reference(launch_reference, project_root=root)
    identity = (run_id, frame_id, todo_id, owner_ref)
    coordinates = lambda r: tuple(r.get(k) for k in ('run_id','task_frame_id','todo_id','owner_ref'))
    if coordinates(launch) != identity or launch['kind'] != 'ASSIGNED':
        reject('Launch journal identity mismatch')
    rows = [record for record, _ in records(journal_path(root, todo_id))
            if record['task_frame_id'] == frame_id]
    if not rows or rows[0] != launch or any(coordinates(r) != identity for r in rows):
        reject('Host journal owner, run or launch changed')
    if rows[-1]['kind'] != 'MASTER_DONE':
        reject('Journal must record the closed Host after all role results')
    original = launch['payload']
    selected, expected = {}, set()
    for role in ('worker', 'reviewer'):
        assignments = [r for r in rows if r['kind'] == 'ASSIGNED'
                       and r['payload'].get('role') == role.upper()]
        if not assignments or [r['payload'].get('attempt') for r in assignments] != list(range(1,len(assignments)+1)):
            reject('Role attempts are missing, repeated or out of order')
        for record in assignments:
            payload = record['payload']
            if payload.get('todo') != original.get('todo') or payload.get('worker_write_scope') != original.get('worker_write_scope'):
                reject('Todo content or delegated scope changed within this Host')
            expected.add(f"{frame_id}_{role}_{payload['attempt']}")
        chosen = assignments[-1]
        attempt = chosen['payload']['attempt']
        prefix = f'task-frame-result://{frame_id}_{role}_{attempt}/{role}-turn/'
        if not str(value.get(role+'_result_ref') or '').startswith(prefix):
            reject('Recovery must select the latest assigned role attempt')
        collections = [r for r in rows if r['kind']=='RESULT_COLLECTED'
                       and r['payload'].get('result_ref')==value[role+'_result_ref']
                       and r['payload'].get('result_digest')==value[role+'_result_digest']
                       and r['payload'].get('status')=='COMPLETED'
                       and r['payload'].get('role')==role.upper()
                       and r['payload'].get('attempt')==attempt]
        if len(collections)!=1 or collections[0]['sequence'] <= chosen['sequence']:
            reject('Selected role needs one exact subsequent collection')
        selected[role] = {'attempt':attempt,'assignment':chosen,'collection':collections[0]}
    worker, reviewer = selected['worker'], selected['reviewer']
    if reviewer['assignment']['sequence'] <= worker['collection']['sequence']:
        reject('Reviewer must be assigned after the selected Worker was collected')
    pinned = reviewer['assignment']['payload'].get('collected_results',{}).get('WORKER',{})
    if any(pinned.get(k) != value['worker_'+k] for k in ('result_ref','result_digest')):
        reject('Reviewer assignment is bound to a different Worker result')
    verified = current_todo is not None and original.get('todo') == dict(current_todo)
    if current_todo is not None and not verified:
        reject('Current Todo content differs from the pinned assignment')
    return {'attempts':{role:entry['attempt'] for role,entry in selected.items()},
            'expected_frames':expected,'completion_provenance_verified':verified}
