"""Explicit Master journal handoff, using authoritative run and Host state."""
from pathlib import Path

from todo_execution_journal import JournalError, journal_path, transfer_owner


def handoff(server, value, state_root, host_status):
    run = server.persona_automation.get_run(value['run_id'])
    todo = server.store.get_todo(value['todo_id'])
    if run.get('session_anchor_ref') != value['owner_ref']:
        raise JournalError('TODO_JOURNAL_OWNER_MISMATCH', 'caller must own the destination run')
    server._validate_persona_automation_anchor(run['project_id'], value['owner_ref'])
    if (not server._persona_todo_in_run_scope(run, todo) or todo.get('archived_at')):
        raise JournalError('TODO_JOURNAL_TRANSFER_SCOPE_INVALID', 'Todo is outside current run scope')
    root = Path(server.store.get_project(run['project_id'])['project_root'])

    def validate(rows):
        current = server.persona_automation.get_run(value['run_id'])
        if (current.get('session_anchor_ref') != value['owner_ref']
                or current.get('revision') != value['expected_revision']
                or current.get('state') != 'WAITING'):
            raise JournalError('TODO_JOURNAL_TRANSFER_RUN_CHANGED', 'requires exact WAITING run revision')
        for old_run_id in {row['run_id'] for row, _ in rows}:
            old = server.persona_automation.get_run(old_run_id)
            if (old.get('project_id') != current.get('project_id')
                    or old.get('node_ref') != current.get('node_ref')
                    or not server._persona_todo_in_run_scope(old, todo)
                    or old.get('state') not in {'WAITING', 'STOPPED', 'COMPLETED'}):
                raise JournalError('TODO_JOURNAL_TRANSFER_SOURCE_ACTIVE', 'source run must be quiescent in the same scope')
        for frame in {row['task_frame_id'] for row, _ in rows}:
            observed = host_status(state_root, frame)
            if not observed.get('known') or observed.get('alive') or observed.get('phase') != 'EXITED':
                raise JournalError('TODO_JOURNAL_TRANSFER_HOST_ACTIVE', 'every old Host must be known and exited')

    ref = transfer_owner(journal_path(root, value['todo_id']),
                         todo_id=value['todo_id'], owner_ref=value['owner_ref'],
                         run_id=value['run_id'], previous_owner_ref=value['previous_owner_ref'],
                         expected_sequence=value['expected_sequence'],
                         expected_digest=value['expected_digest'],
                         request_id=value['request_id'], validate=validate)
    return {'status': 'TODO_JOURNAL_OWNER_TRANSFERRED', 'todo_journal': ref}
