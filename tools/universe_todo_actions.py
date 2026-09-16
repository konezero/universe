"""Server-owned Todo commands; callers never reconstruct private session rows."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone

STATES = ('BACKLOG', 'READY', 'IN_PROGRESS', 'BLOCKED', 'DONE')
READ_ID, LIST_ID, STATE_ID = 'todo.read', 'todo.list', 'todo.state'
ID_SCHEMA = {'type': 'string', 'minLength': 1, 'maxLength': 160}
SCOPE_SCHEMA = {'type': ['string', 'null'], 'minLength': 1, 'maxLength': 160}
VALIDATION_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['status', 'evidence_ref'],
    'properties': {'status': {'const': 'PASSED'},
                   'evidence_ref': {'type': 'string', 'minLength': 1, 'maxLength': 1000}},
}
REQUEST_SCHEMAS = {
    READ_ID: {'type': 'object', 'additionalProperties': False,
              'required': ['todo_id'], 'properties': {'todo_id': ID_SCHEMA}},
    LIST_ID: {'type': 'object', 'additionalProperties': False,
              'required': ['project_id'], 'properties': {
                  'project_id': SCOPE_SCHEMA, 'node_ref': ID_SCHEMA,
                  'include_done': {'type': 'boolean', 'default': False},
                  'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
                  'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 100}}},
    STATE_ID: {'type': 'object', 'additionalProperties': False,
               'required': ['todo_id', 'project_id', 'expected_revision', 'request_id', 'state'],
               'properties': {'todo_id': ID_SCHEMA, 'project_id': SCOPE_SCHEMA,
                   'expected_revision': {'type': 'integer', 'minimum': 1},
                   'request_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{8,100}$'},
                   'state': {'enum': list(STATES)}, 'validation': VALIDATION_SCHEMA},
               'allOf': [{'if': {'properties': {'state': {'const': 'DONE'}}},
                          'then': {'required': ['validation']}}]},
}


class TodoActionError(ValueError):
    def __init__(self, code, detail, status=400):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def _object(value, required, optional=()):
    if not isinstance(value, Mapping) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise TodoActionError('TODO_ACTION_REQUEST_INVALID',
                              'Required/allowed request fields do not match the published Action schema')
    return dict(value)


def _text(value, name, maximum=160):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise TodoActionError('TODO_ACTION_REQUEST_INVALID', f'{name} must be nonempty text, at most {maximum} characters')
    return value.strip()


def _integer(value, name, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or (maximum is not None and value > maximum):
        raise TodoActionError('TODO_ACTION_REQUEST_INVALID', f'{name} is outside its integer range')
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def normalize_state(request):
    value = _object(request, ('todo_id', 'project_id', 'expected_revision', 'request_id', 'state'), ('validation',))
    value['todo_id'] = _text(value['todo_id'], 'todo_id')
    if value['project_id'] is not None:
        value['project_id'] = _text(value['project_id'], 'project_id')
    _integer(value['expected_revision'], 'expected_revision', 1)
    if not isinstance(value['request_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', value['request_id']):
        raise TodoActionError('TODO_ACTION_REQUEST_INVALID', 'request_id must be 8–100 ASCII letters, digits, underscores or hyphens')
    if value['state'] not in STATES:
        raise TodoActionError('TODO_STATE_INVALID', 'state must be one of the published Todo states')
    validation = value.get('validation')
    if value['state'] == 'DONE' and validation is None:
        raise TodoActionError('TODO_COMPLETION_VALIDATION_REQUIRED', 'DONE requires PASSED validation and its evidence_ref', 409)
    if 'validation' in value:
        validation = _object(validation, ('status', 'evidence_ref'))
        if validation['status'] != 'PASSED':
            raise TodoActionError('TODO_COMPLETION_VALIDATION_REQUIRED', 'Completion validation must be PASSED', 409)
        validation['evidence_ref'] = _text(validation['evidence_ref'], 'validation.evidence_ref', 1000)
        value['validation'] = validation
    return value


class TodoActions:
    def __init__(self, store):
        self.store = store
        # The server may bind the Persona automation completion gate after
        # constructing this action gateway.  Direct TodoActions fixtures keep
        # the callback unset and retain their standalone contract.
        self.completion_gate = None
        with store._connection() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS todo_state_action (
                request_id TEXT PRIMARY KEY, request_json TEXT NOT NULL,
                actor_ref TEXT NOT NULL, result_json TEXT NOT NULL,
                propagation_json TEXT, created_at TEXT NOT NULL)''')

    def read(self, request):
        request = _object(request, ('todo_id',))
        todo = self.store.get_todo(_text(request['todo_id'], 'todo_id'))
        return {'schema': 'universe.todo-read-action.v1', 'action_id': READ_ID,
                'status': 'TODO_READ', 'todo': todo}

    def list(self, request):
        request = _object(request, ('project_id',), ('node_ref', 'include_done', 'offset', 'limit'))
        project_id = request['project_id']
        if project_id is not None:
            project_id = _text(project_id, 'project_id')
            self.store.get_project(project_id)
        node_ref = _text(request['node_ref'], 'node_ref') if 'node_ref' in request else None
        include_done = request.get('include_done', False)
        if not isinstance(include_done, bool):
            raise TodoActionError('TODO_ACTION_REQUEST_INVALID', 'include_done must be boolean')
        offset = _integer(request.get('offset', 0), 'offset')
        limit = _integer(request.get('limit', 100), 'limit', 1, 200)
        rows = [row for row in self.store.list_todos() if row['project_id'] == project_id
                and (node_ref is None or row['node_ref'] == node_ref)
                and (include_done or row['state'] != 'DONE')]
        return {'schema': 'universe.todo-list-action.v1', 'action_id': LIST_ID,
                'status': 'TODOS_LISTED', 'project_id': project_id, 'node_ref': node_ref,
                'include_done': include_done, 'offset': offset, 'limit': limit,
                'total': len(rows), 'has_more': offset + limit < len(rows),
                'todos': rows[offset:offset + limit]}

    def change_state(self, request, actor):
        value = normalize_state(request)
        if not isinstance(actor, Mapping) or actor.get('kind') != 'USER' or not actor.get('actor_ref'):
            raise TodoActionError('ACTION_ACTOR_RESOLUTION_FAILED', 'A transport-authenticated operator is required', 403)
        request_json = _json(value)
        now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
        # Request replay and revision/current state are checked in the same write transaction.
        with self.store._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = connection.execute('SELECT * FROM todo_state_action WHERE request_id = ?', (value['request_id'],)).fetchone()
            if existing is not None:
                if existing['request_json'] != request_json or existing['actor_ref'] != actor['actor_ref']:
                    raise TodoActionError('TODO_STATE_REQUEST_CONFLICT', 'request_id already identifies a different command', 409)
                result = json.loads(existing['result_json'])
                result['replayed'] = True
            else:
                row = connection.execute('SELECT * FROM project_todo WHERE todo_id = ?', (value['todo_id'],)).fetchone()
                if row is None:
                    raise TodoActionError('TODO_NOT_FOUND', 'Todo does not exist', 404)
                if row['project_id'] != value['project_id']:
                    raise TodoActionError('TODO_SCOPE_CONFLICT', 'Todo belongs to a different project scope', 409)
                if row['revision'] != value['expected_revision']:
                    raise TodoActionError('TODO_REVISION_CONFLICT', f"Todo revision changed; current revision is {row['revision']}", 409)
                if value['state'] == 'DONE' and row['project_id'] and callable(self.completion_gate):
                    gate = self.completion_gate(
                        str(row['project_id'] or ''),
                        str(row['todo_id']),
                        str(row['node_ref'] or '') or None,
                    )
                    if gate is not None:
                        raise TodoActionError(
                            'TODO_AUTOMATION_REVIEW_REQUIRED',
                            'a WORKER_REVIEW automation run requires an independent PASS verdict before this Todo can be completed',
                            409,
                        )
                previous = row['state']
                changed = previous != value['state']
                if changed:
                    connection.execute('UPDATE project_todo SET state = ?, revision = revision + 1, updated_at = ? WHERE todo_id = ? AND revision = ?',
                                       (value['state'], now, value['todo_id'], value['expected_revision']))
                todo = self.store._todo_row(connection.execute('SELECT * FROM project_todo WHERE todo_id = ?', (value['todo_id'],)).fetchone())
                result = {'schema': 'universe.todo-state-action.v1', 'action_id': STATE_ID,
                          'status': 'TODO_STATE_CHANGED' if changed else 'TODO_STATE_UNCHANGED',
                          'request_id': value['request_id'], 'previous_state': previous,
                          'state_changed': changed, 'replayed': False, 'todo': todo,
                          'actor': dict(actor), 'validation': value.get('validation'),
                          'execution_assignment_created': False, 'task_frame_created': False}
                connection.execute('INSERT INTO todo_state_action(request_id, request_json, actor_ref, result_json, created_at) VALUES (?, ?, ?, ?, ?)',
                                   (value['request_id'], request_json, actor['actor_ref'], _json(result), now))
                if todo['project_id'] is not None:
                    # Existing history consumers see the same event type, with an immutable applied revision.
                    event_id = 'todo-state-' + hashlib.sha256(value['request_id'].encode()).hexdigest()[:32]
                    event = {'todo_id': todo['todo_id'], 'action_id': value['request_id'],
                             'outcome': {'DONE': 'COMPLETED', 'BLOCKED': 'FAILED', 'IN_PROGRESS': 'STARTED', 'READY': 'REOPENED', 'BACKLOG': 'BACKLOGGED'}[todo['state']],
                             'source': 'ACTION_IR', 'state': todo['state'],
                             'evidence_ref': (value.get('validation') or {}).get('evidence_ref', 'action://todo.state/' + value['request_id']),
                             'validation': value.get('validation', {}), 'applied_revision': todo['revision'], 'actor': dict(actor)}
                    connection.execute('INSERT INTO project_event(event_id, project_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)',
                                       (event_id, todo['project_id'], 'TODO_ACTION_APPLIED', _json(event), now))
        return self._propagate(result)

    def _propagate(self, result):
        todo = result['todo']
        if not result['state_changed'] or todo['project_id'] is None or todo['state'] not in ('DONE', 'BLOCKED'):
            result['result_propagation'] = {'status': 'NOT_APPLICABLE'}
            return result
        with self.store._connection() as connection:
            saved = connection.execute('SELECT propagation_json FROM todo_state_action WHERE request_id = ?', (result['request_id'],)).fetchone()
        if saved['propagation_json']:
            result['result_propagation'] = json.loads(saved['propagation_json'])
            return result
        try:
            fanout = self.store.record_todo_result_fanout(todo['project_id'], todo['todo_id'], 'COMPLETED' if todo['state'] == 'DONE' else 'FAILED', todo['state'])
            propagation = {'status': 'RECORDED', 'result': fanout}
            with self.store._connection() as connection:
                connection.execute('UPDATE todo_state_action SET propagation_json = ? WHERE request_id = ?', (_json(propagation), result['request_id']))
        except (OSError, sqlite3.Error) as error:
            # State already committed. Replay resumes only the idempotent result propagation.
            propagation = {'status': 'PENDING', 'error_code': 'TODO_RESULT_PROPAGATION_UNAVAILABLE', 'error_type': type(error).__name__}
        result['result_propagation'] = propagation
        return result
