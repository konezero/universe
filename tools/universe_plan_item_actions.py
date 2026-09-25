"""Revisioned structured plan items linked to existing nodes, Todos and results.

A plan item is the stored form of one project-plan entry (case, structure,
process, work, validation, dependency or phase).  Its node / Todo / dependency
references are real rows validated at save time, not plan text such as "P1" or
"N1".  Human and LLM clients use the same Actions; the server resolves the
actor.  A plan item never changes Todo state and never aggregates partial Todo
completion into project completion.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone

SCHEMA = 'universe.plan-item.v1'
READ_ID, LIST_ID, SAVE_ID, TRACE_ID = 'plan.item.read', 'plan.item.list', 'plan.item.save', 'plan.item.trace'
ACTION_IDS = (READ_ID, LIST_ID, SAVE_ID, TRACE_ID)
KINDS = ('CASE', 'STRUCTURE', 'PROCESS', 'WORK', 'VALIDATION', 'DEPENDENCY', 'PHASE')
STATES = ('ACTIVE', 'RETIRED')
SOURCE_KINDS = ('PROJECT_DRAFT', 'DOCUMENT', 'MANUAL')
ITEM_FIELDS = ('kind', 'label', 'title', 'purpose', 'work', 'done_criteria',
               'source', 'node_refs', 'todo_refs', 'depends_on', 'state')
TEXT_LIMITS = {'label': 40, 'title': 160, 'purpose': 4000, 'work': 8000, 'done_criteria': 4000}
MAX_LINKS = 50
ERRORS = ('PLAN_ITEM_REQUEST_INVALID', 'PLAN_ITEM_NOT_FOUND', 'PLAN_ITEM_REVISION_CONFLICT',
          'PLAN_ITEM_REPLAY_CONFLICT', 'PLAN_ITEM_SCOPE_CONFLICT', 'PLAN_ITEM_LINK_DUPLICATE',
          'PLAN_ITEM_LINK_TARGET_NOT_FOUND', 'PLAN_ITEM_LINK_PROJECT_MISMATCH',
          'PLAN_ITEM_LINK_TARGET_ARCHIVED', 'PLAN_ITEM_DEPENDENCY_INVALID',
          'PLAN_ITEM_SOURCE_NOT_FOUND', 'PLAN_ITEM_SOURCE_PROJECT_MISMATCH')

_ID = {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,120}$'}
_REFS = {'type': 'array', 'maxItems': MAX_LINKS, 'uniqueItems': True, 'items': _ID}
ITEM_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': list(ITEM_FIELDS),
    'properties': {
        'kind': {'enum': list(KINDS)},
        'label': {'type': 'string', 'maxLength': TEXT_LIMITS['label']},
        'title': {'type': 'string', 'minLength': 1, 'maxLength': TEXT_LIMITS['title']},
        'purpose': {'type': 'string', 'maxLength': TEXT_LIMITS['purpose']},
        'work': {'type': 'string', 'maxLength': TEXT_LIMITS['work']},
        'done_criteria': {'type': 'string', 'maxLength': TEXT_LIMITS['done_criteria']},
        'source': {'type': 'object', 'additionalProperties': False, 'required': ['kind'], 'properties': {
            'kind': {'enum': list(SOURCE_KINDS)}, 'draft_id': _ID,
            'draft_revision': {'type': 'integer', 'minimum': 1},
            'section': {'type': 'string', 'maxLength': 120}, 'path': {'type': 'string', 'maxLength': 400}}},
        'node_refs': _REFS, 'todo_refs': _REFS, 'depends_on': _REFS,
        'state': {'enum': list(STATES)},
    },
}
REQUEST_SCHEMAS = {
    READ_ID: {'type': 'object', 'additionalProperties': False, 'required': ['plan_item_id'],
              'properties': {'plan_item_id': _ID}},
    LIST_ID: {'type': 'object', 'additionalProperties': False, 'required': ['project_id'],
              'properties': {'project_id': _ID, 'node_ref': _ID, 'todo_id': _ID, 'draft_id': _ID,
                             'include_retired': {'type': 'boolean', 'default': False}}},
    SAVE_ID: {'type': 'object', 'additionalProperties': False,
              'required': ['plan_item_id', 'project_id', 'expected_revision', 'request_id', 'item'],
              'properties': {'plan_item_id': _ID, 'project_id': _ID,
                             'expected_revision': {'type': 'integer', 'minimum': 0},
                             'request_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{8,100}$'},
                             'item': ITEM_SCHEMA}},
    TRACE_ID: {'type': 'object', 'additionalProperties': False, 'required': ['plan_item_id'],
               'properties': {'plan_item_id': _ID}},
}


class PlanItemError(ValueError):
    def __init__(self, code, detail, status=400):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def _identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', value):
        raise PlanItemError('PLAN_ITEM_REQUEST_INVALID', f'{name} must be 1-120 ASCII letters, digits, underscores or hyphens')
    return value


def _fields(value, name, required, optional=()):
    if not isinstance(value, Mapping) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise PlanItemError('PLAN_ITEM_REQUEST_INVALID', f'{name} fields do not match the published Action schema')
    return dict(value)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _refs(value, name):
    if not isinstance(value, list) or len(value) > MAX_LINKS:
        raise PlanItemError('PLAN_ITEM_REQUEST_INVALID', f'{name} must be a list of at most {MAX_LINKS} ids')
    refs = [_identifier(item, name) for item in value]
    if len(set(refs)) != len(refs):
        raise PlanItemError('PLAN_ITEM_LINK_DUPLICATE', f'{name} names the same target more than once')
    return refs


def normalize_item(value):
    item = _fields(value, 'item', ITEM_FIELDS)
    if item['kind'] not in KINDS:
        raise PlanItemError('PLAN_ITEM_REQUEST_INVALID', 'kind must be one of: ' + ', '.join(KINDS))
    if item['state'] not in STATES:
        raise PlanItemError('PLAN_ITEM_REQUEST_INVALID', 'state must be ACTIVE or RETIRED')
    for key, limit in TEXT_LIMITS.items():
        if not isinstance(item[key], str) or len(item[key]) > limit:
            raise PlanItemError('PLAN_ITEM_REQUEST_INVALID', f'{key} must be text of at most {limit} characters')
    item['title'] = item['title'].strip()
    if not item['title']:
        raise PlanItemError('PLAN_ITEM_REQUEST_INVALID', 'title is required')
    source = item['source']
    if not isinstance(source, Mapping) or source.get('kind') not in SOURCE_KINDS:
        raise PlanItemError('PLAN_ITEM_REQUEST_INVALID', 'source.kind must be PROJECT_DRAFT, DOCUMENT or MANUAL')
    allowed = {'PROJECT_DRAFT': (('kind', 'draft_id', 'draft_revision'), ('section',)),
               'DOCUMENT': (('kind', 'path'), ('section',)), 'MANUAL': (('kind',), ())}[source['kind']]
    source = _fields(source, 'source', *allowed)
    if 'draft_id' in source:
        _identifier(source['draft_id'], 'source.draft_id')
        if type(source['draft_revision']) is not int or source['draft_revision'] < 1:
            raise PlanItemError('PLAN_ITEM_REQUEST_INVALID', 'source.draft_revision must be a positive integer')
    for key, limit in (('section', 120), ('path', 400)):
        if key in source and (not isinstance(source[key], str) or not source[key].strip() or len(source[key]) > limit):
            raise PlanItemError('PLAN_ITEM_REQUEST_INVALID', f'source.{key} must be nonempty text of at most {limit} characters')
    item['source'] = source
    for key in ('node_refs', 'todo_refs', 'depends_on'):
        item[key] = _refs(item[key], key)
    return item


def _ensure_tables(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS project_plan_item_revision (
        plan_item_id TEXT NOT NULL, revision INTEGER NOT NULL, project_id TEXT NOT NULL,
        request_id TEXT NOT NULL UNIQUE, request_digest TEXT NOT NULL, actor_ref TEXT NOT NULL,
        record_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(plan_item_id, revision))''')
    # Current-revision reverse index: node/Todo/dependency -> plan items.
    connection.execute('''CREATE TABLE IF NOT EXISTS project_plan_item_link (
        plan_item_id TEXT NOT NULL, project_id TEXT NOT NULL, target_kind TEXT NOT NULL,
        target_id TEXT NOT NULL, PRIMARY KEY(plan_item_id, target_kind, target_id),
        CHECK(target_kind IN ('NODE', 'TODO', 'PLAN_ITEM')))''')
    connection.execute('CREATE INDEX IF NOT EXISTS project_plan_item_link_target ON project_plan_item_link(target_kind, target_id)')


def _table_exists(connection, name):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _current(connection, plan_item_id):
    row = connection.execute('SELECT record_json FROM project_plan_item_revision WHERE plan_item_id=? ORDER BY revision DESC LIMIT 1',
                             (plan_item_id,)).fetchone()
    return json.loads(row[0]) if row else None


def plan_item_refs_for(connection, target_kind, target_id):
    """Return current plan items that link one target; empty before first use."""
    if not _table_exists(connection, 'project_plan_item_link'):
        return []
    rows = connection.execute('SELECT plan_item_id FROM project_plan_item_link WHERE target_kind=? AND target_id=? ORDER BY plan_item_id',
                              (target_kind, target_id)).fetchall()
    refs = []
    for row in rows:
        item = _current(connection, row[0])
        if item is not None:
            refs.append({'plan_item_id': item['plan_item_id'], 'revision': item['revision'], 'kind': item['kind'],
                         'label': item['label'], 'title': item['title'], 'state': item['state']})
    return refs


class PlanItemActions:
    def __init__(self, store):
        self.store = store
        with store._connection() as connection:
            _ensure_tables(connection)

    def read(self, request):
        request = _fields(request, 'plan.item.read', ('plan_item_id',))
        plan_item_id = _identifier(request['plan_item_id'], 'plan_item_id')
        with self.store._connection() as connection:
            item = _current(connection, plan_item_id)
            if item is None:
                raise PlanItemError('PLAN_ITEM_NOT_FOUND', 'plan item does not exist', 404)
            return {'schema': 'universe.plan-item-read-action.v1', 'action_id': READ_ID, 'status': 'PLAN_ITEM_READ',
                    'plan_item': item, 'links': self._resolve(connection, item)}

    def list(self, request):
        request = _fields(request, 'plan.item.list', ('project_id',), ('node_ref', 'todo_id', 'draft_id', 'include_retired'))
        project_id = _identifier(request['project_id'], 'project_id')
        self.store.get_project(project_id)
        include_retired = request.get('include_retired', False)
        if not isinstance(include_retired, bool):
            raise PlanItemError('PLAN_ITEM_REQUEST_INVALID', 'include_retired must be boolean')
        filters = [(kind, _identifier(request[key], key)) for key, kind in (('node_ref', 'NODE'), ('todo_id', 'TODO')) if key in request]
        draft_id = _identifier(request['draft_id'], 'draft_id') if 'draft_id' in request else None
        with self.store._connection() as connection:
            rows = connection.execute('''SELECT r.record_json FROM project_plan_item_revision r
                JOIN (SELECT plan_item_id, MAX(revision) revision FROM project_plan_item_revision GROUP BY plan_item_id) h
                ON r.plan_item_id=h.plan_item_id AND r.revision=h.revision WHERE r.project_id=? ORDER BY r.plan_item_id''',
                                      (project_id,)).fetchall()
            items = [json.loads(row[0]) for row in rows]
            for kind, target in filters:
                linked = {row[0] for row in connection.execute(
                    'SELECT plan_item_id FROM project_plan_item_link WHERE target_kind=? AND target_id=?', (kind, target))}
                items = [item for item in items if item['plan_item_id'] in linked]
        items = [item for item in items if (include_retired or item['state'] == 'ACTIVE')
                 and (draft_id is None or item['source'].get('draft_id') == draft_id)]
        return {'schema': 'universe.plan-item-list-action.v1', 'action_id': LIST_ID, 'status': 'PLAN_ITEMS_LISTED',
                'project_id': project_id, 'total': len(items), 'plan_items': items}

    def save(self, request, actor):
        if not isinstance(actor, Mapping) or actor.get('kind') != 'USER' or not actor.get('actor_ref'):
            raise PlanItemError('ACTION_ACTOR_RESOLUTION_FAILED', 'plan.item.save requires the server-resolved USER actor', 403)
        value = _fields(request, 'plan.item.save', ('plan_item_id', 'project_id', 'expected_revision', 'request_id', 'item'))
        plan_item_id = _identifier(value['plan_item_id'], 'plan_item_id')
        project_id = _identifier(value['project_id'], 'project_id')
        expected = value['expected_revision']
        if type(expected) is not int or expected < 0:
            raise PlanItemError('PLAN_ITEM_REQUEST_INVALID', 'expected_revision must be a nonnegative integer')
        if not isinstance(value['request_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', value['request_id']):
            raise PlanItemError('PLAN_ITEM_REQUEST_INVALID', 'request_id must be 8-100 ASCII letters, digits, underscores or hyphens')
        item = normalize_item(value['item'])
        if plan_item_id in item['depends_on']:
            raise PlanItemError('PLAN_ITEM_DEPENDENCY_INVALID', 'a plan item cannot depend on itself')
        self.store.get_project(project_id)
        digest = hashlib.sha256(_json({**value, 'item': item}).encode()).hexdigest()
        with self.store._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            prior = connection.execute('SELECT request_digest, actor_ref, record_json FROM project_plan_item_revision WHERE request_id=?',
                                       (value['request_id'],)).fetchone()
            if prior is not None:
                if prior[0] != digest or prior[1] != actor['actor_ref']:
                    raise PlanItemError('PLAN_ITEM_REPLAY_CONFLICT', 'request_id was already used for a different command', 409)
                return self._saved(connection, json.loads(prior[2]), replayed=True)
            current = _current(connection, plan_item_id)
            revision = current['revision'] if current else 0
            if revision != expected:
                raise PlanItemError('PLAN_ITEM_REVISION_CONFLICT', f'plan item revision changed; current revision is {revision}', 409)
            if current and current['project_id'] != project_id:
                raise PlanItemError('PLAN_ITEM_SCOPE_CONFLICT', 'a saved plan item cannot move to another project', 409)
            previous_links = {key: set(current[key]) if current else set() for key in ('node_refs', 'todo_refs', 'depends_on')}
            self._validate_source(connection, project_id, item['source'])
            self._validate_links(connection, project_id, plan_item_id, item, previous_links)
            record = {'schema': SCHEMA, 'plan_item_id': plan_item_id, 'project_id': project_id,
                      'revision': revision + 1, **item, 'actor': dict(actor), 'updated_at': _now(),
                      'created_at': current['created_at'] if current else _now()}
            connection.execute('INSERT INTO project_plan_item_revision VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                               (plan_item_id, revision + 1, project_id, value['request_id'], digest,
                                actor['actor_ref'], _json(record), record['updated_at']))
            connection.execute('DELETE FROM project_plan_item_link WHERE plan_item_id=?', (plan_item_id,))
            for key, kind in (('node_refs', 'NODE'), ('todo_refs', 'TODO'), ('depends_on', 'PLAN_ITEM')):
                connection.executemany('INSERT INTO project_plan_item_link VALUES (?, ?, ?, ?)',
                                       [(plan_item_id, project_id, kind, target) for target in item[key]])
            changes = {key: {'added': sorted(set(item[key]) - previous_links[key]),
                             'removed': sorted(previous_links[key] - set(item[key]))}
                       for key in ('node_refs', 'todo_refs', 'depends_on')}
            return {**self._saved(connection, record, replayed=False), 'link_changes': changes}

    def trace(self, request):
        """Navigate plan item -> nodes -> Todos -> results and each back-reference."""
        request = _fields(request, 'plan.item.trace', ('plan_item_id',))
        plan_item_id = _identifier(request['plan_item_id'], 'plan_item_id')
        with self.store._connection() as connection:
            item = _current(connection, plan_item_id)
            if item is None:
                raise PlanItemError('PLAN_ITEM_NOT_FOUND', 'plan item does not exist', 404)
            links = self._resolve(connection, item)
            todos = []
            for todo in links['todos']:
                if todo['status'] != 'LINKED':
                    todos.append(todo)
                    continue
                todos.append({**todo, 'results': self._todo_results(connection, item['project_id'], todo['todo_id']),
                              'plan_items': plan_item_refs_for(connection, 'TODO', todo['todo_id']),
                              'node_in_plan_item': todo['node_ref'] in item['node_refs'] if todo['node_ref'] else None})
            nodes = [{**node, 'plan_items': plan_item_refs_for(connection, 'NODE', node['node_ref'])}
                     if node['status'] == 'LINKED' else node for node in links['nodes']]
            linked = [todo for todo in todos if todo['status'] == 'LINKED']
            done = sum(1 for todo in linked if todo['state'] == 'DONE')
            status = ('NO_TODOS' if not linked else 'COMPLETE' if done == len(linked)
                      else 'NOT_STARTED' if done == 0 and all(t['state'] in ('BACKLOG', 'READY') for t in linked) else 'PARTIAL')
            source = item['source']
            draft = None
            if source['kind'] == 'PROJECT_DRAFT' and _table_exists(connection, 'project_authoring_revision'):
                row = connection.execute('SELECT record FROM project_authoring_revision WHERE draft_id=? ORDER BY revision DESC LIMIT 1',
                                         (source['draft_id'],)).fetchone()
                current_revision = json.loads(row[0])['revision'] if row else None
                draft = {'draft_id': source['draft_id'], 'linked_revision': source['draft_revision'],
                         'current_revision': current_revision,
                         'stale': current_revision is not None and current_revision != source['draft_revision']}
            dependents = plan_item_refs_for(connection, 'PLAN_ITEM', plan_item_id)
        return {'schema': 'universe.plan-item-trace-action.v1', 'action_id': TRACE_ID, 'status': 'PLAN_ITEM_TRACED',
                'plan_item': item, 'source_draft': draft, 'nodes': nodes, 'todos': todos,
                'depends_on': links['depends_on'], 'dependents': dependents,
                'completion': {'status': status, 'linked_todos': len(linked), 'done_todos': done,
                               'missing_targets': sum(1 for group in (links['nodes'], links['todos'], links['depends_on'])
                                                      for entry in group if entry['status'] != 'LINKED'),
                               'scope': 'PLAN_ITEM_ONLY', 'project_completion': 'NOT_AGGREGATED'}}

    def _saved(self, connection, record, *, replayed):
        return {'schema': 'universe.plan-item-save-action.v1', 'action_id': SAVE_ID,
                'status': 'PLAN_ITEM_REPLAYED' if replayed else 'PLAN_ITEM_SAVED', 'replayed': replayed,
                'plan_item': record, 'links': self._resolve(connection, record),
                'effects': {'todo_state': 'NONE', 'node_state': 'NONE', 'execution_assignment': 'NONE', 'authority': 'NONE'}}

    @staticmethod
    def _validate_source(connection, project_id, source):
        if source['kind'] != 'PROJECT_DRAFT':
            return
        row = None
        if _table_exists(connection, 'project_authoring_revision'):
            row = connection.execute('SELECT record FROM project_authoring_revision WHERE draft_id=? AND revision=?',
                                     (source['draft_id'], source['draft_revision'])).fetchone()
        if row is None:
            raise PlanItemError('PLAN_ITEM_SOURCE_NOT_FOUND', 'source draft revision does not exist', 404)
        if json.loads(row[0]).get('project_id') != project_id:
            raise PlanItemError('PLAN_ITEM_SOURCE_PROJECT_MISMATCH', 'source draft belongs to a different project scope', 409)

    @staticmethod
    def _validate_links(connection, project_id, plan_item_id, item, previous):
        for node_ref in item['node_refs']:
            row = connection.execute('SELECT project_id, state FROM feature_node WHERE feature_id=?', (node_ref,)).fetchone()
            if row is None:
                raise PlanItemError('PLAN_ITEM_LINK_TARGET_NOT_FOUND', f'node {node_ref} does not exist', 404)
            if row[0] != project_id:
                raise PlanItemError('PLAN_ITEM_LINK_PROJECT_MISMATCH', f'node {node_ref} belongs to a different project', 409)
            if row[1] == 'ARCHIVED' and node_ref not in previous['node_refs']:
                raise PlanItemError('PLAN_ITEM_LINK_TARGET_ARCHIVED', f'node {node_ref} is archived; link an active node', 409)
        for todo_id in item['todo_refs']:
            row = connection.execute('SELECT project_id FROM project_todo WHERE todo_id=?', (todo_id,)).fetchone()
            if row is None:
                raise PlanItemError('PLAN_ITEM_LINK_TARGET_NOT_FOUND', f'Todo {todo_id} does not exist', 404)
            if row[0] != project_id:
                raise PlanItemError('PLAN_ITEM_LINK_PROJECT_MISMATCH', f'Todo {todo_id} belongs to a different project scope', 409)
        for dependency in item['depends_on']:
            target = _current(connection, dependency)
            if target is None:
                raise PlanItemError('PLAN_ITEM_LINK_TARGET_NOT_FOUND', f'plan item {dependency} does not exist', 404)
            if target['project_id'] != project_id:
                raise PlanItemError('PLAN_ITEM_LINK_PROJECT_MISMATCH', f'plan item {dependency} belongs to a different project', 409)
            if plan_item_id in _dependency_closure(connection, dependency):
                raise PlanItemError('PLAN_ITEM_DEPENDENCY_INVALID', f'depending on {dependency} would create a cycle', 409)

    @staticmethod
    def _resolve(connection, item):
        """Resolve each stored reference to its current target without failing on drift."""
        nodes = []
        for node_ref in item['node_refs']:
            row = connection.execute('SELECT project_id, title, state, revision FROM feature_node WHERE feature_id=?', (node_ref,)).fetchone()
            if row is None:
                nodes.append({'node_ref': node_ref, 'status': 'MISSING'})
            elif row[0] != item['project_id']:
                nodes.append({'node_ref': node_ref, 'status': 'PROJECT_MISMATCH'})
            else:
                nodes.append({'node_ref': node_ref, 'status': 'LINKED', 'title': row[1], 'state': row[2], 'revision': row[3]})
        todos = []
        for todo_id in item['todo_refs']:
            row = connection.execute('SELECT project_id, node_ref, title, state, revision FROM project_todo WHERE todo_id=?', (todo_id,)).fetchone()
            if row is None:
                todos.append({'todo_id': todo_id, 'status': 'MISSING'})
            elif row[0] != item['project_id']:
                todos.append({'todo_id': todo_id, 'status': 'PROJECT_MISMATCH'})
            else:
                todos.append({'todo_id': todo_id, 'status': 'LINKED', 'node_ref': row[1], 'title': row[2],
                              'state': row[3], 'revision': row[4]})
        dependencies = []
        for dependency in item['depends_on']:
            target = _current(connection, dependency)
            if target is None:
                dependencies.append({'plan_item_id': dependency, 'status': 'MISSING'})
            else:
                dependencies.append({'plan_item_id': dependency, 'status': 'LINKED', 'revision': target['revision'],
                                     'title': target['title'], 'state': target['state']})
        return {'nodes': nodes, 'todos': todos, 'depends_on': dependencies}

    @staticmethod
    def _todo_results(connection, project_id, todo_id):
        results = []
        if _table_exists(connection, 'work_loop_result_fanout'):
            for row in connection.execute('''SELECT fanout_id, outcome, created_at FROM work_loop_result_fanout
                    WHERE project_id=? AND source_kind='TODO' AND source_id=? ORDER BY created_at, fanout_id''', (project_id, todo_id)):
                results.append({'kind': 'RESULT_FANOUT', 'fanout_id': row[0], 'outcome': row[1], 'created_at': row[2]})
        if _table_exists(connection, 'project_event'):
            for row in connection.execute('''SELECT event_id, payload_json, created_at FROM project_event
                    WHERE project_id=? AND event_type='TODO_ACTION_APPLIED' ORDER BY created_at, event_id''', (project_id,)):
                try:
                    payload = json.loads(row[1])
                except (TypeError, ValueError):
                    continue
                if payload.get('todo_id') == todo_id and payload.get('state') in ('DONE', 'BLOCKED'):
                    results.append({'kind': 'TODO_STATE_EVIDENCE', 'event_id': row[0], 'state': payload.get('state'),
                                    'evidence_ref': payload.get('evidence_ref'), 'created_at': row[2]})
        return results


def _dependency_closure(connection, start):
    seen, pending = set(), [start]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(row[0] for row in connection.execute(
            "SELECT target_id FROM project_plan_item_link WHERE plan_item_id=? AND target_kind='PLAN_ITEM'", (current,)))
    return seen


def plan_item_refs_for_store(store, target_kind, target_id):
    try:
        with store._connection() as connection:
            return plan_item_refs_for(connection, target_kind, target_id)
    except sqlite3.Error:
        return []
