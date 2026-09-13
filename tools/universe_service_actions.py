"""Durable web-service restart operations; the PTY Supervisor stays independent."""
from __future__ import annotations

import json
from contextlib import contextmanager
import threading
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from typing import Any

from universe_service_control import load_state, restart_service, service_status, pid_is_running


class ServiceActionError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(detail)


class ServiceActions:
    def __init__(self, state_path: Path):
        self.state_path = state_path.resolve()
        self.database = self.state_path.with_suffix('.operations.sqlite3')

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE IF NOT EXISTS restart_operation (operation_id TEXT PRIMARY KEY, expected_pid INTEGER NOT NULL, record TEXT NOT NULL)")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _save(self, connection, record):
        connection.execute("UPDATE restart_operation SET record=? WHERE operation_id=?", (json.dumps(record), record['operation_id']))

    def status(self, request):
        if set(request) - {'operation_id'}:
            raise ServiceActionError('REQUEST_INVALID', 'service.status accepts only operation_id')
        operation_id = request.get('operation_id')
        if operation_id is not None and (not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', operation_id)):
            raise ServiceActionError('REQUEST_INVALID', 'invalid operation_id')
        live = service_status(self.state_path)
        result = {'schema': 'universe.service-status.v1', 'status': live.get('status'), 'pid': live.get('pid'), 'endpoint': live.get('endpoint')}
        if operation_id:
            if not self.database.exists():
                raise ServiceActionError('SERVICE_OPERATION_NOT_FOUND', 'restart operation does not exist')
            with self._connect() as connection:
                row = connection.execute('SELECT record FROM restart_operation WHERE operation_id=?', (operation_id,)).fetchone()
            if row is None:
                raise ServiceActionError('SERVICE_OPERATION_NOT_FOUND', 'restart operation does not exist')
            operation = json.loads(row['record'])
            if operation['status'] in {'ACCEPTED', 'RUNNING'} and time.time() - operation['updated_at'] > 180 and not pid_is_running(operation.get('helper_pid', 0)):
                operation = {**operation, 'status': 'INTERRUPTED', 'recorded_status': operation['status'], 'error': {'code': 'HELPER_INTERRUPTED', 'operation': 'service.restart', 'detail': 'helper exited without a completion record; inspect service.status before a new request'}}
            result['operation'] = operation
        return result

    def restart(self, request, actor):
        if set(request) != {'request_id', 'expected_pid'}:
            raise ServiceActionError('REQUEST_INVALID', 'service.restart requires request_id and expected_pid')
        key, expected = request['request_id'], request['expected_pid']
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', key):
            raise ServiceActionError('REQUEST_INVALID', 'invalid request_id')
        if type(expected) is not int or expected <= 0:
            raise ServiceActionError('REQUEST_INVALID', 'expected_pid must be a positive integer')
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT expected_pid, record FROM restart_operation WHERE operation_id=?', (key,)).fetchone()
            if row:
                if row['expected_pid'] != expected:
                    raise ServiceActionError('SERVICE_REPLAY_CONFLICT', 'request_id already identifies another process')
                return json.loads(row['record'])
            current = load_state(self.state_path) or {}
            if current.get('pid') != expected or expected != os.getpid():
                raise ServiceActionError('SERVICE_INSTANCE_CHANGED', 'refresh service.status before restarting')
            for row in connection.execute('SELECT record FROM restart_operation'):
                active = json.loads(row['record'])
                if active['status'] in {'ACCEPTED', 'RUNNING'}:
                    if time.time() - active['updated_at'] < 180 or pid_is_running(active.get('helper_pid', 0)):
                        raise ServiceActionError('SERVICE_RESTART_IN_PROGRESS', 'another restart operation is active')
                    active.update(status='INTERRUPTED', error={'code': 'HELPER_INTERRUPTED', 'operation': 'service.restart', 'detail': 'helper exited without a completion record'}, updated_at=time.time())
                    self._save(connection, active)
            record = {'schema': 'universe.service-restart.v1', 'operation_id': key, 'expected_pid': expected, 'status': 'ACCEPTED', 'updated_at': time.time(), 'actor': dict(actor), 'preserves_pty_supervisor': True}
            connection.execute('INSERT INTO restart_operation VALUES (?, ?, ?)', (key, expected, json.dumps(record)))
        # Commit acceptance before the external owner can stop this process.
        flags = 0
        if os.name == 'nt':
            flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(self.state_path), key], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags, start_new_session=os.name != 'nt')
            threading.Thread(target=child.wait, daemon=True, name='service-restart-helper-reaper').start()
            with self._connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                row = connection.execute('SELECT record FROM restart_operation WHERE operation_id=?', (key,)).fetchone()
                record = json.loads(row['record'])
                record['helper_pid'] = child.pid
                self._save(connection, record)
        except OSError as error:
            record.update(status='FAILED', error={'code': 'SERVICE_HELPER_START_FAILED', 'operation': 'service.restart', 'detail': str(error)}, updated_at=time.time())
            with self._connect() as connection:
                self._save(connection, record)
        return record

    def run(self, key):
        # Permit the HTTP acceptance response to flush before shutdown begins.
        time.sleep(1)
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT record FROM restart_operation WHERE operation_id=?', (key,)).fetchone()
            if not row:
                return
            record = json.loads(row['record'])
            if record['status'] != 'ACCEPTED':
                return
            record.update(status='RUNNING', helper_pid=os.getpid(), updated_at=time.time())
            self._save(connection, record)
        try:
            previous = load_state(self.state_path) or {}
            if previous.get('pid') != record['expected_pid']:
                raise ServiceActionError('SERVICE_INSTANCE_CHANGED', 'target changed before shutdown')
            database = previous.get('database')
            if not isinstance(database, str) or not database:
                raise ServiceActionError('SERVICE_STATE_INVALID', 'database missing from service state')
            outcome = restart_service(state_path=self.state_path, database_path=Path(database), open_ui=False, preserve_control_token=True)
            deadline = time.monotonic() + 90
            live = service_status(self.state_path)
            while outcome.get('status') in {'READY', 'START_ISSUED'} and live.get('status') != 'READY' and time.monotonic() < deadline:
                time.sleep(.5)
                live = service_status(self.state_path)
            if live.get('status') != 'READY' or live.get('pid') == record['expected_pid'] or live.get('endpoint') != previous.get('endpoint'):
                raise ServiceActionError('SERVICE_RESTART_UNCONFIRMED', 'restart did not produce a ready replacement on the original endpoint: ' + str(outcome.get('status')))
            record.update(status='COMPLETED', pid=live.get('pid'), endpoint=live.get('endpoint'))
        except Exception as error:
            record.update(status='FAILED', error={'code': getattr(error, 'code', 'SERVICE_RESTART_FAILED'), 'operation': 'service.restart', 'detail': str(error)})
        record['updated_at'] = time.time()
        with self._connect() as connection:
            self._save(connection, record)


if __name__ == '__main__':
    ServiceActions(Path(sys.argv[1])).run(sys.argv[2])
