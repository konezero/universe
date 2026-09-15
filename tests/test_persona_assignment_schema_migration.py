"""2026-09-15 live incident: persona.assignments-list dropped the connection
outright (no HTTP response at all) on a database created before
applied_assignment_revision/unsupported_* existed on session_persona_
assignment. Root cause: those columns were only ever in CREATE TABLE IF NOT
EXISTS, never in the additive ALTER TABLE migration loop -- a database that
already had the table (just missing those columns) never gained them, and
every read of row["unsupported_at"] raised IndexError on such a row. That
IndexError was not one of do_POST's caught exception types (ProviderSessionError,
SessionSupervisorError, UniverseError, (OSError, sqlite3.Error)), so it
propagated out of the handler entirely with no response ever written -- the
client saw the connection close, indistinguishable from a network failure.

Two independent fixes, both covered here:
1. The migration loop now adds the missing columns (root fix).
2. do_POST gained a final `except Exception` so ANY otherwise-uncaught
   application bug still returns a typed HTTP 500 instead of dropping the
   connection (defense in depth -- the next such bug, in any action, does
   not repeat this incident).
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import uuid
from http.client import RemoteDisconnected
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_server import create_server, UniverseStore  # noqa: E402


def _create_pre_migration_schema(database_path: Path) -> None:
    """Recreate exactly the live incident's schema: session_persona_assignment
    exists, but without applied_assignment_revision or any unsupported_* column
    -- everything else present, matching the columns observed on the actual
    broken live database (not a guess).
    """

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            CREATE TABLE session_persona_assignment (
                session_anchor_ref TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                persona_id TEXT NOT NULL,
                persona_revision INTEGER NOT NULL,
                scope TEXT NOT NULL DEFAULT '',
                assignment_revision INTEGER NOT NULL DEFAULT 1,
                state TEXT NOT NULL CHECK(state IN ('ACTIVE', 'UNASSIGNED')) DEFAULT 'ACTIVE',
                applied_at TEXT,
                applied_terminal_id TEXT,
                applied_persona_revision INTEGER,
                actor_ref TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


class PersonaAssignmentSchemaMigrationTests(unittest.TestCase):
    def test_missing_columns_are_backfilled_by_the_additive_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "universe.sqlite3"
            _create_pre_migration_schema(database_path)
            UniverseStore(database_path)  # runs _initialize()'s migration
            connection = sqlite3.connect(database_path)
            try:
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(session_persona_assignment)"
                    ).fetchall()
                }
            finally:
                connection.close()
            for expected in (
                "applied_assignment_revision",
                "unsupported_at",
                "unsupported_terminal_id",
                "unsupported_provider",
                "unsupported_reason",
                "host_sync_status",
            ):
                self.assertIn(expected, columns, f"migration did not backfill {expected}")

    def test_persona_assignments_list_no_longer_crashes_a_pre_migration_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "universe.sqlite3"
            _create_pre_migration_schema(database_path)
            # A real row surviving from before the migration, exactly like
            # the live incident's row (created under the old schema).
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    "INSERT INTO session_persona_assignment "
                    "(session_anchor_ref, project_id, persona_id, persona_revision, scope, "
                    "assignment_revision, state, actor_ref, created_at, updated_at) "
                    "VALUES ('anchor-pre-migration', 'TEST', 'persona-x', 1, '', 1, 'ACTIVE', "
                    "'actor-x', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z')"
                )
                connection.commit()
            finally:
                connection.close()

            project_root = root / "TEST"
            project_root.mkdir()
            (project_root / "REPOSITORY_MANIFEST.md").write_text("# TEST\n", encoding="utf-8")

            server = create_server(
                database_path=database_path,
                token="schema-migration-test-token",
                service_state_path=root / "server.json",
                auto_start_project_masters=False,
                auto_start_conductor_runtime=False,
                auto_start_goal_scheduler=False,
            )
            server.store.register_project({"project_id": "TEST", "project_root": str(project_root)})
            host, port = server.server_address[:2]
            endpoint = f"http://{host}:{port}"
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            thread.start()
            try:
                req = Request(
                    endpoint + "/v1/actions",
                    data=b'{"action_id": "persona.assignments-list", "request": {"project_id": "TEST"}}',
                    headers={
                        "Authorization": "Bearer schema-migration-test-token",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                try:
                    with urlopen(req, timeout=10) as response:
                        status = response.status
                except HTTPError as error:
                    status = error.code
                except (RemoteDisconnected, ConnectionResetError, URLError) as error:
                    self.fail(
                        "persona.assignments-list dropped the connection instead of "
                        f"responding (the exact 2026-09-15 live incident): {error}"
                    )
                self.assertEqual(200, status)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_do_post_returns_a_typed_error_instead_of_dropping_the_connection(self) -> None:
        """Defense-in-depth: even an exception type nobody anticipated (not
        just this specific IndexError) must still produce an HTTP response.
        Simulated by dropping a column AFTER the server's own migration ran
        (a stand-in for "some other unanticipated application bug"), proving
        the do_POST-level catch-all -- not just the one known root cause --
        actually holds.
        """

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "universe.sqlite3"
            project_root = root / "TEST"
            project_root.mkdir()
            (project_root / "REPOSITORY_MANIFEST.md").write_text("# TEST\n", encoding="utf-8")

            server = create_server(
                database_path=database_path,
                token="schema-migration-test-token-2",
                service_state_path=root / "server.json",
                auto_start_project_masters=False,
                auto_start_conductor_runtime=False,
                auto_start_goal_scheduler=False,
            )
            server.store.register_project({"project_id": "TEST", "project_root": str(project_root)})
            host, port = server.server_address[:2]
            endpoint = f"http://{host}:{port}"
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            thread.start()
            try:
                connection = sqlite3.connect(database_path)
                try:
                    connection.execute(
                        "ALTER TABLE session_persona_assignment DROP COLUMN unsupported_at"
                    )
                    connection.commit()
                finally:
                    connection.close()
                connection = sqlite3.connect(database_path)
                try:
                    connection.execute(
                        "INSERT INTO session_persona_assignment "
                        "(session_anchor_ref, project_id, persona_id, persona_revision, scope, "
                        "assignment_revision, state, actor_ref, created_at, updated_at, "
                        "applied_terminal_id, applied_persona_revision, applied_assignment_revision, "
                        "unsupported_terminal_id, unsupported_provider, unsupported_reason, "
                        "queued_at, queued_terminal_id, queued_provider, queued_message_id, "
                        "queued_submission_id, queued_phase, queued_persona_revision, "
                        "queued_assignment_revision, applied_phase, applied_message_id, node_ref, "
                        "host_sync_status, host_sync_assignment_revision, host_sync_at, host_sync_detail) "
                        "VALUES ('anchor-simulated-corruption', 'TEST', 'persona-x', 1, '', 1, 'ACTIVE', "
                        "'actor-x', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', "
                        "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
                        "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"
                    )
                    connection.commit()
                finally:
                    connection.close()

                req = Request(
                    endpoint + "/v1/actions",
                    data=b'{"action_id": "persona.assignments-list", "request": {"project_id": "TEST"}}',
                    headers={
                        "Authorization": "Bearer schema-migration-test-token-2",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                try:
                    with urlopen(req, timeout=10) as response:
                        status, result = response.status, response.read()
                except HTTPError as error:
                    status, result = error.code, error.read()
                except (RemoteDisconnected, ConnectionResetError, URLError) as error:
                    self.fail(
                        "an unanticipated application exception still dropped the "
                        f"connection instead of returning a typed HTTP error: {error}"
                    )
                self.assertEqual(500, status)
                self.assertIn(b"SERVICE_FAILURE", result)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
