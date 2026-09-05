from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_server import UniverseError, UniverseStore  # noqa: E402

import unittest


class MasterMessageQueueTests(unittest.TestCase):
    """The Master mode-inbox: a claimable, project-scoped work queue.

    Mirrors conductor_room_message's QUEUED -> PROCESSING -> DONE/FAILED
    shape, but per-project (Master is project-local, unlike Conductor's
    single global queue) and with a genuinely race-safe claim — see
    test_concurrent_claims_on_a_single_item_only_one_wins, which is the
    scenario the whole parallel Conductor/Master session initiative
    actually needs (todo: "Master mode-inbox: replace file-drop delivery
    with a claimable DB queue").
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = UniverseStore(self.root / "universe.sqlite3")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def register_project(self, project_id: str) -> dict[str, Any]:
        project_root = self.root / project_id
        project_root.mkdir(parents=True, exist_ok=True)
        (project_root / "REPOSITORY_MANIFEST.md").write_text(
            f"# {project_id}\n", encoding="utf-8"
        )
        project, created = self.store.register_project(
            {
                "project_id": project_id,
                "project_root": str(project_root),
                "metadata": {},
                "attachment": {
                    "install_origin": "PROJECT_STANDALONE",
                    "universe_membership": "LINKED",
                    "runtime_host": "PROJECT_LOCAL",
                },
            }
        )
        self.assertTrue(created)
        return project

    @staticmethod
    def message_request(**overrides: Any) -> dict[str, Any]:
        request: dict[str, Any] = {
            "idempotency_key": "seed-v2",
            "title": "Prepare Universe project seed",
            "instruction": "Read the project and publish Seed assets.",
        }
        request.update(overrides)
        return request

    # -- creation / idempotency ------------------------------------------

    def test_create_is_idempotent_by_key_and_content(self) -> None:
        project = self.register_project("ALPHA")
        first, created_first = self.store.create_master_message(
            project["project_id"], self.message_request()
        )
        self.assertTrue(created_first)
        self.assertEqual("QUEUED", first["delivery_state"])

        second, created_second = self.store.create_master_message(
            project["project_id"], self.message_request()
        )
        self.assertFalse(created_second)
        self.assertEqual(first["message_id"], second["message_id"])

    def test_create_conflicts_on_same_key_different_content(self) -> None:
        project = self.register_project("ALPHA")
        self.store.create_master_message(project["project_id"], self.message_request())
        with self.assertRaises(UniverseError) as ctx:
            self.store.create_master_message(
                project["project_id"],
                self.message_request(title="A completely different task"),
            )
        self.assertEqual("MASTER_MESSAGE_IDEMPOTENCY_CONFLICT", ctx.exception.code)

    def test_create_is_scoped_per_project_not_globally_unique(self) -> None:
        alpha = self.register_project("ALPHA")
        beta = self.register_project("BETA")
        alpha_message, alpha_created = self.store.create_master_message(
            alpha["project_id"], self.message_request()
        )
        beta_message, beta_created = self.store.create_master_message(
            beta["project_id"], self.message_request()
        )
        self.assertTrue(alpha_created)
        self.assertTrue(beta_created)
        self.assertNotEqual(alpha_message["message_id"], beta_message["message_id"])

    # -- claim --------------------------------------------------------------

    def test_claim_returns_oldest_queued_first(self) -> None:
        project = self.register_project("ALPHA")
        first, _ = self.store.create_master_message(
            project["project_id"], self.message_request(idempotency_key="task-1")
        )
        self.store.create_master_message(
            project["project_id"], self.message_request(idempotency_key="task-2")
        )
        claimed = self.store.claim_master_message(
            project["project_id"], provider="CLAUDE"
        )
        self.assertEqual(first["message_id"], claimed["message_id"])
        self.assertEqual("PROCESSING", claimed["delivery_state"])
        self.assertEqual("CLAUDE", claimed["provider"])
        self.assertIn("started_at", claimed)

    def test_claim_returns_none_when_queue_is_empty(self) -> None:
        project = self.register_project("ALPHA")
        self.assertIsNone(
            self.store.claim_master_message(project["project_id"], provider="CLAUDE")
        )

    def test_claim_never_returns_another_projects_item(self) -> None:
        alpha = self.register_project("ALPHA")
        beta = self.register_project("BETA")
        self.store.create_master_message(alpha["project_id"], self.message_request())
        claimed = self.store.claim_master_message(beta["project_id"], provider="CLAUDE")
        self.assertIsNone(claimed)

    def test_concurrent_claims_on_a_single_item_only_one_wins(self) -> None:
        """The load-bearing test: N instances racing claim() on the SAME
        single QUEUED item must yield exactly one success. This is exactly
        what broke conductor_room_message's original (pre-fix) transition
        function once more than one caller exists - a plain read-then-write
        with no WHERE-clause guard on the observed state, safe only by
        accident because _conductor_worker_loop was ever the sole caller.
        """

        project = self.register_project("ALPHA")
        self.store.create_master_message(project["project_id"], self.message_request())

        results: list[dict[str, Any] | None] = []
        barrier = threading.Barrier(8)

        def attempt_claim() -> None:
            barrier.wait()
            results.append(
                self.store.claim_master_message(
                    project["project_id"], provider="CLAUDE"
                )
            )

        threads = [threading.Thread(target=attempt_claim) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        successes = [item for item in results if item is not None]
        self.assertEqual(1, len(successes), f"expected exactly one claim to win, got {results}")
        self.assertEqual(8, len(results))

    # -- complete / fail ------------------------------------------------

    def test_complete_transitions_processing_to_done(self) -> None:
        project = self.register_project("ALPHA")
        self.store.create_master_message(project["project_id"], self.message_request())
        claimed = self.store.claim_master_message(
            project["project_id"], provider="CLAUDE"
        )
        completed = self.store.complete_master_message(
            claimed["message_id"], provider="CLAUDE", result_ref="artifact://seed/1"
        )
        self.assertEqual("DONE", completed["delivery_state"])
        self.assertEqual("artifact://seed/1", completed["result_ref"])
        self.assertIn("completed_at", completed)

    def test_complete_rejects_a_message_still_queued(self) -> None:
        project = self.register_project("ALPHA")
        created, _ = self.store.create_master_message(
            project["project_id"], self.message_request()
        )
        with self.assertRaises(UniverseError) as ctx:
            self.store.complete_master_message(
                created["message_id"], provider="CLAUDE"
            )
        self.assertEqual("MASTER_MESSAGE_STATE_CONFLICT", ctx.exception.code)

    def test_fail_transitions_queued_or_processing_to_failed(self) -> None:
        project = self.register_project("ALPHA")
        created, _ = self.store.create_master_message(
            project["project_id"], self.message_request()
        )
        self.store.fail_master_message(
            created["message_id"], code="BLOCKED", reason="no live Master session"
        )
        failed = self.store.get_master_message(created["message_id"])
        self.assertEqual("FAILED", failed["delivery_state"])
        self.assertEqual("BLOCKED", failed["failure"]["code"])

    def test_fail_rejects_an_already_done_message(self) -> None:
        project = self.register_project("ALPHA")
        self.store.create_master_message(project["project_id"], self.message_request())
        claimed = self.store.claim_master_message(
            project["project_id"], provider="CLAUDE"
        )
        self.store.complete_master_message(claimed["message_id"], provider="CLAUDE")
        with self.assertRaises(UniverseError) as ctx:
            self.store.fail_master_message(
                claimed["message_id"], code="X", reason="too late"
            )
        self.assertEqual("MASTER_MESSAGE_STATE_CONFLICT", ctx.exception.code)

    # -- listing ----------------------------------------------------------

    def test_list_is_scoped_per_project_and_ordered_by_creation(self) -> None:
        alpha = self.register_project("ALPHA")
        beta = self.register_project("BETA")
        self.store.create_master_message(
            alpha["project_id"], self.message_request(idempotency_key="a1")
        )
        self.store.create_master_message(
            alpha["project_id"], self.message_request(idempotency_key="a2")
        )
        self.store.create_master_message(
            beta["project_id"], self.message_request(idempotency_key="b1")
        )
        alpha_messages = self.store.list_master_messages(alpha["project_id"])
        self.assertEqual(2, len(alpha_messages))
        self.assertEqual(
            ["a1", "a2"], [m["idempotency_key"] for m in alpha_messages]
        )


class ConductorRoomMessageClaimRaceRegressionTests(unittest.TestCase):
    """claim_conductor_room_message shares the same _cas_update_json_message
    fix as the Master queue above. Before the fix, _transition_conductor_
    room_message read the row then wrote it back with no WHERE-clause guard
    on the observed state — safe only because _conductor_worker_loop was
    ever the sole caller. This is a regression test for that fix, using the
    pre-existing global Conductor queue rather than the new Master one.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = UniverseStore(Path(self.temp.name) / "universe.sqlite3")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_concurrent_claims_on_a_single_conductor_message_only_one_wins(
        self,
    ) -> None:
        message, created = self.store.create_conductor_room_message(
            {"body": "race me", "idempotency_key": "race-1"}
        )
        self.assertTrue(created)

        results: list[dict[str, Any] | None] = []
        barrier = threading.Barrier(8)

        def attempt_claim() -> None:
            barrier.wait()
            results.append(
                self.store.claim_conductor_room_message(
                    message["message_id"], provider="CLAUDE"
                )
            )

        threads = [threading.Thread(target=attempt_claim) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        successes = [item for item in results if item is not None]
        self.assertEqual(1, len(successes), f"expected exactly one claim to win, got {results}")
        self.assertEqual(8, len(results))


if __name__ == "__main__":
    unittest.main()
