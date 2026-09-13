"""Revisioned project descriptions and plans shared by UI and Action clients."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone

FIELDS = ('title', 'domain', 'description', 'goal', 'target_users', 'scenarios', 'structure', 'capabilities', 'validation', 'constraints', 'project_root')


class DraftError(ValueError):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(detail)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', value):
        raise DraftError('PROJECT_DRAFT_INVALID', 'draft_id/request_id must be 1–120 ASCII letters, digits, underscores or hyphens')
    return value


class ProjectDrafts:
    def __init__(self, connection_factory):
        self.connection = connection_factory

    @staticmethod
    def _exists(connection):
        return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_authoring_revision'").fetchone() is not None

    @staticmethod
    def empty(draft_id):
        return {'schema': 'universe.project-draft.v1', 'draft_id': draft_id, 'project_id': None, 'revision': 0, 'fields': {key: '' for key in FIELDS}}

    def read(self, draft_id):
        draft_id = identifier(draft_id)
        with self.connection() as connection:
            if not self._exists(connection):
                return self.empty(draft_id)
            row = connection.execute('SELECT record FROM project_authoring_revision WHERE draft_id=? ORDER BY revision DESC LIMIT 1', (draft_id,)).fetchone()
            return json.loads(row[0]) if row else self.empty(draft_id)

    def list(self, project_id=None):
        with self.connection() as connection:
            if not self._exists(connection):
                return []
            rows = connection.execute("SELECT r.record FROM project_authoring_revision r JOIN (SELECT draft_id, MAX(revision) revision FROM project_authoring_revision GROUP BY draft_id) h ON r.draft_id=h.draft_id AND r.revision=h.revision ORDER BY r.rowid DESC").fetchall()
            records = [json.loads(row[0]) for row in rows]
            return [r for r in records if project_id is None or r['project_id'] == project_id]

    def save(self, request, actor):
        if set(request) != {'draft_id', 'project_id', 'expected_revision', 'request_id', 'fields'}:
            raise DraftError('PROJECT_DRAFT_INVALID', 'save requires draft_id, project_id, expected_revision, request_id and fields')
        draft_id, request_id = identifier(request['draft_id']), identifier(request['request_id'])
        revision = request['expected_revision']
        if type(revision) is not int or revision < 0:
            raise DraftError('PROJECT_DRAFT_INVALID', 'expected_revision must be a nonnegative integer')
        if request['project_id'] is not None and (not isinstance(request['project_id'], str) or not request['project_id'].strip()):
            raise DraftError('PROJECT_DRAFT_INVALID', 'project_id must be null or a registered project id')
        fields = request['fields']
        if not isinstance(fields, dict) or set(fields) != set(FIELDS):
            raise DraftError('PROJECT_DRAFT_INVALID', 'fields must contain exactly: ' + ', '.join(FIELDS))
        if any(not isinstance(value, str) or len(value) > (160 if key in {'title', 'domain'} else 12000) for key, value in fields.items()):
            raise DraftError('PROJECT_DRAFT_INVALID', 'draft field is not text or exceeds its size limit')
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('CREATE TABLE IF NOT EXISTS project_authoring_revision (draft_id TEXT NOT NULL, revision INTEGER NOT NULL, request_id TEXT NOT NULL UNIQUE, request_digest TEXT NOT NULL, record TEXT NOT NULL, PRIMARY KEY(draft_id, revision))')
            prior = connection.execute('SELECT request_digest, record FROM project_authoring_revision WHERE request_id=?', (request_id,)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise DraftError('PROJECT_DRAFT_REPLAY_CONFLICT', 'request_id was already used for different content')
                return json.loads(prior[1])
            row = connection.execute('SELECT record FROM project_authoring_revision WHERE draft_id=? ORDER BY revision DESC LIMIT 1', (draft_id,)).fetchone()
            current = json.loads(row[0]) if row else self.empty(draft_id)
            if current['revision'] != revision:
                raise DraftError('PROJECT_DRAFT_REVISION_CONFLICT', 'another editor saved this draft; read the current revision before merging')
            if revision and current['project_id'] != request['project_id']:
                raise DraftError('PROJECT_DRAFT_SCOPE_CONFLICT', 'a saved draft cannot be moved to another project')
            record = {**current, 'project_id': request['project_id'], 'revision': revision + 1, 'fields': dict(fields), 'actor': dict(actor), 'updated_at': datetime.now(timezone.utc).isoformat()}
            connection.execute('INSERT INTO project_authoring_revision VALUES (?, ?, ?, ?, ?)', (draft_id, revision + 1, request_id, digest, json.dumps(record, ensure_ascii=False)))
            return record
