"""persona.* Actions: natural-language persona CRUD + exact-Anchor assignment.

Runs against a real in-process server/DB via the existing
MemoryCandidateApiTests fixture ([[persona-conductor-automation-plan]] P1).
Covers CRUD + CAS + idempotency, assignment/unassignment against REAL
SessionSupervisor-registered Anchors (not made-up strings), cross-anchor
isolation, immutable per-revision content snapshots, CAS-guarded applied
evidence, and Unicode/newline/quote/backslash argv-safety.

2026-09-14 Conductor review of the first P1 pass found 5 real defects, fixed
here:
  1. persona_prompt was silently dropped at the Supervisor HTTP boundary
     (tools/universe_pty_supervisor.py) -- covered by
     tests/test_pty_supervisor.py's HTTP-boundary tests, not here.
  2. resolve_active_persona_prompt read persona_definition's CURRENT body,
     so a later persona.update silently changed an already-pinned
     assignment's applied content -- fixed with persona_revision_snapshot,
     tested below.
  3. record_persona_applied had no CAS and assign/unassign never cleared
     stale applied evidence -- fixed, tested below.
  4. assign_persona never validated the Anchor exists / belongs to the
     project -- fixed via a real SessionSupervisor lookup, tested below.
  5. Unicode/newline/quote/backslash in persona bodies were unverified
     against the real argv/provider-config boundary -- tested below.
"""
from __future__ import annotations

import itertools
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import test_memory_candidates_and_delegations as fixtures

from universe_app.terminal_host import (  # noqa: E402
    TerminalHost,
    TerminalHostError,
    native_queue_persona_text,
    persona_delivery_mode,
    persona_delivery_supported,
    persona_native_queue_message_id,
    startup_argv,
)

_REQUEST_ID_SEQ = itertools.count()
_SESSION_ID_SEQ = itertools.count()


def next_request_id() -> str:
    return f"persona-test-{next(_REQUEST_ID_SEQ):08d}"


class PersonaActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MemoryCandidateApiTests()
        cls.fixture.setUp()
        cls.server = cls.fixture.server
        cls.request = cls.fixture.request

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDown()

    def act(self, action_id, request, request_id_key=True):
        body = dict(request)
        if request_id_key and "request_id" not in body:
            body["request_id"] = next_request_id()
        return self.request("POST", "/v1/actions", {"action_id": action_id, "request": body})

    def make_persona(self, title="노련한 프로젝트 팀장", body="목표와 골격을 이해하고 유지한다."):
        status, result = self.act("persona.create", {"title": title, "body": body})
        self.assertEqual(200, status, result)
        return result["persona"]

    def register_anchor(self, project_id="TEST", mode="MASTER", provider="CODEX") -> str:
        """A real SessionSupervisor-registered Session Anchor -- not a made-up string.

        persona.assign now validates against this exact registry
        (2026-09-14 Conductor finding #4), so every assignment test below
        targets an Anchor that genuinely exists.
        """
        session_id = f"persona-test-session-{next(_SESSION_ID_SEQ):08d}"
        material, _created = self.server.session_supervisor.register_session({
            "session_id": session_id, "node": project_id, "mode": mode, "provider": provider,
        })
        anchor = str(material["session_anchor_ref"])
        self.assertTrue(anchor)
        return anchor

    # -- CRUD -----------------------------------------------------------

    def test_create_read_list_roundtrip(self):
        persona = self.make_persona("팀장 A", "책임 본문 A")
        self.assertEqual(1, persona["revision"])
        self.assertEqual("ACTIVE", persona["state"])

        status, result = self.act("persona.read", {"persona_id": persona["persona_id"]}, request_id_key=False)
        self.assertEqual(200, status)
        self.assertEqual(persona["title"], result["persona"]["title"])

        status, result = self.act("persona.list", {}, request_id_key=False)
        self.assertEqual(200, status)
        self.assertIn(persona["persona_id"], [p["persona_id"] for p in result["personas"]])

    def test_human_and_llm_share_the_same_action_no_hardcoded_roster(self):
        for title, body in (
            ("QA 리드", "품질 기준과 회귀 경계를 판단한다."),
            ("데이터 분석가", "지표 추세와 이상치를 근거로 제시한다."),
        ):
            persona = self.make_persona(title, body)
            status, result = self.act("persona.read", {"persona_id": persona["persona_id"]}, request_id_key=False)
            self.assertEqual(200, status)
            self.assertEqual(title, result["persona"]["title"])
            self.assertEqual(body, result["persona"]["body"])

    def test_update_requires_matching_revision_and_is_idempotent(self):
        persona = self.make_persona()
        rid = next_request_id()
        status, result = self.act("persona.update", {
            "persona_id": persona["persona_id"], "expected_revision": 1, "title": "개정된 제목",
            "request_id": rid,
        }, request_id_key=False)
        self.assertEqual(200, status)
        self.assertEqual(2, result["persona"]["revision"])

        replay_status, replay_result = self.act("persona.update", {
            "persona_id": persona["persona_id"], "expected_revision": 1, "title": "개정된 제목",
            "request_id": rid,
        }, request_id_key=False)
        self.assertEqual(200, replay_status)
        self.assertTrue(replay_result.get("replayed"))
        self.assertEqual(2, replay_result["persona"]["revision"], "a replay must not double-apply")

        stale_status, stale_result = self.act("persona.update", {
            "persona_id": persona["persona_id"], "expected_revision": 1, "title": "다른 제목",
        }, request_id_key=True)
        self.assertEqual(409, stale_status)
        self.assertEqual("PERSONA_REVISION_CONFLICT", stale_result["error_code"])

    def test_same_request_id_different_payload_is_rejected(self):
        persona = self.make_persona()
        rid = next_request_id()
        self.act("persona.update", {
            "persona_id": persona["persona_id"], "expected_revision": 1, "title": "A",
            "request_id": rid,
        }, request_id_key=False)
        status, result = self.act("persona.update", {
            "persona_id": persona["persona_id"], "expected_revision": 1, "title": "B (다른 내용)",
            "request_id": rid,
        }, request_id_key=False)
        self.assertEqual(409, status)
        self.assertEqual("PERSONA_REQUEST_CONFLICT", result["error_code"])

    def test_archive_then_restore(self):
        persona = self.make_persona()
        status, result = self.act("persona.archive", {
            "persona_id": persona["persona_id"], "expected_revision": 1,
        })
        self.assertEqual(200, status)
        self.assertEqual("ARCHIVED", result["persona"]["state"])

        status, listed = self.act("persona.list", {}, request_id_key=False)
        self.assertNotIn(persona["persona_id"], [p["persona_id"] for p in listed["personas"]])
        status, listed_all = self.act("persona.list", {"include_archived": True}, request_id_key=False)
        self.assertIn(persona["persona_id"], [p["persona_id"] for p in listed_all["personas"]])

        status, result = self.act("persona.restore", {
            "persona_id": persona["persona_id"], "expected_revision": 2,
        })
        self.assertEqual(200, status)
        self.assertEqual("ACTIVE", result["persona"]["state"])

    def test_archived_persona_cannot_be_assigned(self):
        persona = self.make_persona()
        self.act("persona.archive", {"persona_id": persona["persona_id"], "expected_revision": 1})
        anchor = self.register_anchor()
        status, result = self.act("persona.assign", {
            "session_anchor_ref": anchor,
            "project_id": "TEST",
            "persona_id": persona["persona_id"],
            "expected_persona_revision": 2,
        })
        self.assertEqual(409, status)
        self.assertEqual("PERSONA_ARCHIVED", result["error_code"])

    # -- Anchor validation (2026-09-14 Conductor finding #4) --------------

    def test_assign_rejects_nonexistent_anchor(self):
        persona = self.make_persona()
        status, result = self.act("persona.assign", {
            "session_anchor_ref": "session_anchor_never_registered_anywhere",
            "project_id": "TEST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 1,
        })
        self.assertEqual(404, status)
        self.assertEqual("PERSONA_ASSIGNMENT_ANCHOR_NOT_FOUND", result["error_code"])

    def test_assign_rejects_anchor_from_a_different_project(self):
        other_root = Path(self.fixture.temp.name) / "OTHER"
        if not other_root.exists():
            other_root.mkdir()
            (other_root / "REPOSITORY_MANIFEST.md").write_text("# OTHER\n", encoding="utf-8")
            self.server.store.register_project({"project_id": "OTHER", "project_root": str(other_root)})
        other_anchor = self.register_anchor(project_id="OTHER")
        persona = self.make_persona()
        status, result = self.act("persona.assign", {
            "session_anchor_ref": other_anchor, "project_id": "TEST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 1,
        })
        self.assertEqual(409, status)
        self.assertEqual("PERSONA_ASSIGNMENT_ANCHOR_PROJECT_MISMATCH", result["error_code"])

    def test_wrong_project_is_rejected(self):
        # The Anchor (registered under TEST) genuinely cannot belong to a
        # project that does not exist, so the Anchor/project-ownership check
        # (finding #4) catches this before store.assign_persona's own
        # get_project() would -- same protection, the more specific error.
        persona = self.make_persona()
        anchor = self.register_anchor()
        status, result = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "DOES_NOT_EXIST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 1,
        })
        self.assertEqual(409, status)
        self.assertEqual("PERSONA_ASSIGNMENT_ANCHOR_PROJECT_MISMATCH", result["error_code"])

    # -- Assignment (real Anchors) -----------------------------------------

    def test_assign_read_change_unassign_exact_anchor(self):
        persona = self.make_persona("팀장 B", "본문 B")
        anchor = self.register_anchor()
        status, result = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 1,
        })
        self.assertEqual(200, status, result)
        self.assertEqual(1, result["assignment"]["assignment_revision"])
        self.assertEqual("ACTIVE", result["assignment"]["state"])

        status, read_back = self.act("persona.assignment-read", {"session_anchor_ref": anchor}, request_id_key=False)
        self.assertEqual(200, status)
        self.assertEqual(persona["persona_id"], read_back["assignment"]["persona_id"])

        persona2 = self.make_persona("팀장 C", "본문 C")
        status, changed = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona2["persona_id"], "expected_persona_revision": 1,
            "expected_assignment_revision": 1,
        })
        self.assertEqual(200, status, changed)
        self.assertEqual(2, changed["assignment"]["assignment_revision"])
        self.assertEqual(persona2["persona_id"], changed["assignment"]["persona_id"])

        status, unassigned = self.act("persona.unassign", {
            "session_anchor_ref": anchor, "expected_assignment_revision": 2,
        })
        self.assertEqual(200, status, unassigned)
        self.assertEqual("UNASSIGNED", unassigned["assignment"]["state"])

    def test_stale_assignment_revision_conflicts(self):
        persona = self.make_persona()
        anchor = self.register_anchor()
        self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 1,
        })
        status, result = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 1,
            "expected_assignment_revision": 0,
        })
        self.assertEqual(409, status)
        self.assertEqual("PERSONA_ASSIGNMENT_REVISION_CONFLICT", result["error_code"])

    def test_stale_persona_revision_conflicts(self):
        persona = self.make_persona()
        self.act("persona.update", {
            "persona_id": persona["persona_id"], "expected_revision": 1, "title": "새 제목",
        })
        anchor = self.register_anchor()
        status, result = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 1,
        })
        self.assertEqual(409, status)
        self.assertEqual("PERSONA_REVISION_CONFLICT", result["error_code"])

    def test_assigning_one_anchor_does_not_affect_another(self):
        persona_x = self.make_persona("X", "X 본문")
        persona_y = self.make_persona("Y", "Y 본문")
        anchor_x = self.register_anchor()
        anchor_y = self.register_anchor()
        self.act("persona.assign", {
            "session_anchor_ref": anchor_x, "project_id": "TEST",
            "persona_id": persona_x["persona_id"], "expected_persona_revision": 1,
        })
        self.act("persona.assign", {
            "session_anchor_ref": anchor_y, "project_id": "TEST",
            "persona_id": persona_y["persona_id"], "expected_persona_revision": 1,
        })
        _, x_read = self.act("persona.assignment-read", {"session_anchor_ref": anchor_x}, request_id_key=False)
        _, y_read = self.act("persona.assignment-read", {"session_anchor_ref": anchor_y}, request_id_key=False)
        self.assertEqual(persona_x["persona_id"], x_read["assignment"]["persona_id"])
        self.assertEqual(persona_y["persona_id"], y_read["assignment"]["persona_id"])

    def test_unassigned_anchor_reads_as_none(self):
        anchor = self.register_anchor()
        status, result = self.act("persona.assignment-read", {"session_anchor_ref": anchor}, request_id_key=False)
        self.assertEqual(200, status)
        self.assertIsNone(result["assignment"])

    def test_assignment_survives_simulated_host_restart(self):
        persona = self.make_persona("복원 확인", "재시작 후에도 유지되는 배정 본문")
        anchor = self.register_anchor()
        self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 1,
        })
        reread = self.server.store.read_persona_assignment(anchor)
        self.assertIsNotNone(reread)
        self.assertEqual(persona["persona_id"], reread["persona_id"])
        self.assertEqual("ACTIVE", reread["state"])

    # -- Immutable revision snapshot (2026-09-14 Conductor finding #2) ----

    def test_persona_update_does_not_change_an_already_pinned_assignment(self):
        persona = self.make_persona("스냅샷 팀장", "원본 본문 v1")
        anchor = self.register_anchor()
        self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 1,
        })
        resolved_before = self.server.store.resolve_active_persona_prompt(anchor)
        self.assertEqual("원본 본문 v1", resolved_before[0])

        # Edit the persona AFTER assignment -- the pinned assignment must
        # keep resolving to the v1 content, not silently pick up v2.
        self.act("persona.update", {
            "persona_id": persona["persona_id"], "expected_revision": 1, "body": "수정된 본문 v2",
        })
        resolved_after = self.server.store.resolve_active_persona_prompt(anchor)
        self.assertIsNotNone(resolved_after)
        self.assertEqual(
            "원본 본문 v1", resolved_after[0],
            "an assignment pinned at revision 1 must not pick up a later persona.update",
        )

        # A NEW assignment made at the new revision correctly sees v2.
        anchor2 = self.register_anchor()
        self.act("persona.assign", {
            "session_anchor_ref": anchor2, "project_id": "TEST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 2,
        })
        resolved_v2 = self.server.store.resolve_active_persona_prompt(anchor2)
        self.assertEqual("수정된 본문 v2", resolved_v2[0])

    # -- CAS-guarded applied evidence (2026-09-14 Conductor finding #3) ---

    def test_record_persona_applied_cas_succeeds_for_the_exact_assignment(self):
        persona = self.make_persona()
        anchor = self.register_anchor()
        assign_status, assign_result = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 1,
        })
        assignment = assign_result["assignment"]
        before = self.server.store.read_persona_assignment(anchor)
        self.assertIsNone(before["applied_at"])

        stamped = self.server.store.record_persona_applied(
            anchor, "term_evidence_test", persona["persona_id"], 1, assignment["assignment_revision"],
        )
        self.assertTrue(stamped)
        after = self.server.store.read_persona_assignment(anchor)
        self.assertIsNotNone(after["applied_at"])
        self.assertEqual("term_evidence_test", after["applied_terminal_id"])
        self.assertEqual(1, after["applied_persona_revision"])
        self.assertEqual(assignment["assignment_revision"], after["applied_assignment_revision"])

    def test_record_persona_applied_cas_no_ops_after_reassignment_race(self):
        # Simulates: resolve_active_persona_prompt() reads assignment A,
        # then BEFORE the launch's create() call returns, the anchor gets
        # reassigned to a different persona (assignment B). The stale
        # evidence stamp for A must not land on B's row.
        persona_a = self.make_persona("A", "A 본문")
        persona_b = self.make_persona("B", "B 본문")
        anchor = self.register_anchor()
        _, assign_a = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona_a["persona_id"], "expected_persona_revision": 1,
        })
        resolved_a = self.server.store.resolve_active_persona_prompt(anchor)
        self.assertEqual(persona_a["persona_id"], resolved_a[1]["persona_id"])

        # Race: reassign to B before A's spawn evidence is recorded.
        self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona_b["persona_id"], "expected_persona_revision": 1,
            "expected_assignment_revision": 1,
        })

        stamped = self.server.store.record_persona_applied(
            anchor, "term_stale_race", persona_a["persona_id"], 1,
            assign_a["assignment"]["assignment_revision"],
        )
        self.assertFalse(stamped, "a stale CAS must not silently apply")
        current = self.server.store.read_persona_assignment(anchor)
        self.assertEqual(persona_b["persona_id"], current["persona_id"])
        self.assertIsNone(
            current["applied_at"],
            "B's fresh assignment must not inherit A's stale applied stamp",
        )

    def test_reassign_clears_previous_applied_evidence(self):
        persona_a = self.make_persona("적용됨 A", "A 본문")
        persona_b = self.make_persona("적용됨 B", "B 본문")
        anchor = self.register_anchor()
        _, assign_a = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona_a["persona_id"], "expected_persona_revision": 1,
        })
        self.server.store.record_persona_applied(
            anchor, "term_a", persona_a["persona_id"], 1, assign_a["assignment"]["assignment_revision"],
        )
        applied_a = self.server.store.read_persona_assignment(anchor)
        self.assertIsNotNone(applied_a["applied_at"])

        self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona_b["persona_id"], "expected_persona_revision": 1,
            "expected_assignment_revision": applied_a["assignment_revision"],
        })
        after_reassign = self.server.store.read_persona_assignment(anchor)
        self.assertIsNone(
            after_reassign["applied_at"],
            "reassigning must clear the previous persona's applied evidence",
        )

    def test_unassign_clears_applied_evidence(self):
        persona = self.make_persona()
        anchor = self.register_anchor()
        _, assign_result = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 1,
        })
        self.server.store.record_persona_applied(
            anchor, "term_x", persona["persona_id"], 1, assign_result["assignment"]["assignment_revision"],
        )
        self.act("persona.unassign", {
            "session_anchor_ref": anchor,
            "expected_assignment_revision": assign_result["assignment"]["assignment_revision"],
        })
        after = self.server.store.read_persona_assignment(anchor)
        self.assertEqual("UNASSIGNED", after["state"])
        self.assertIsNone(after["applied_at"])

    # -- Real launch-argv application proof (isolated, no live session touched) --

    @staticmethod
    def _claude_persona_file_text(argv) -> str:
        # newline="" mirrors _write_persona_prompt_cache_file's own write:
        # disable universal-newline translation so a read-back does not
        # itself hide a real write-side mutation.
        path = argv[argv.index("--append-system-prompt-file") + 1]
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()

    def test_resolve_active_persona_prompt_and_argv_injection(self):
        persona = self.make_persona("적용 증거 팀장", "이 문자열이 실제 launch argv에 포함되어야 한다.")
        anchor = self.register_anchor()
        self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 1,
        })
        resolved = self.server.store.resolve_active_persona_prompt(anchor)
        self.assertIsNotNone(resolved)
        body_text, assignment = resolved
        self.assertEqual(persona["body"], body_text)

        # CLAUDE: MASTER framing stays inline, persona goes through the
        # file flag -- both present, independently.
        argv_claude = startup_argv("CLAUDE", "", mode="MASTER", project_id="TEST", persona_prompt=body_text)
        self.assertIn("--append-system-prompt", argv_claude)
        master_combined = argv_claude[argv_claude.index("--append-system-prompt") + 1]
        self.assertNotIn(persona["body"], master_combined)
        self.assertNotIn("PROJECT_ID", master_combined)
        self.assertIn("--append-system-prompt-file", argv_claude)
        self.assertEqual(persona["body"], self._claude_persona_file_text(argv_claude))

        argv_codex = startup_argv("CODEX", "", mode="", project_id="", persona_prompt=body_text)
        self.assertEqual("NATIVE_QUEUE", persona_delivery_mode("CODEX", body_text)[0])
        self.assertFalse(any(persona["body"] in a for a in argv_codex))

        argv_grok = startup_argv("GROK", "", mode="", project_id="", persona_prompt=body_text)
        self.assertIn("--rules", argv_grok)
        self.assertIn(persona["body"], argv_grok[argv_grok.index("--rules") + 1])

        argv_bare = startup_argv("CLAUDE", "", mode="", project_id="")
        self.assertNotIn("--append-system-prompt", argv_bare)
        self.assertNotIn("--append-system-prompt-file", argv_bare)

    # -- Unicode / newline / quote / backslash argv safety (finding #5) ---
    #
    # CLAUDE carries the persona body through --append-system-prompt-file --
    # an exact byte-for-byte file, so it never touches Windows argv/cmd
    # encoding at all. CODEX uses its Rust Session Host native queue for
    # bodies that cannot safely cross one managed argv value. GROK has no
    # equivalent exact transport for the interactive launch and remains
    # unsupported for those bodies.

    def test_claude_persona_body_with_unicode_emoji_newlines_quotes_backslashes_is_byte_exact(self):
        text = (
            "한국어·日本語·emoji 🚀 mixed unicode ünïcödé\n"
            'quotes "like this" and a \\ backslash\r\n'
            "- markdown list item\n"
            "```code fence```"
        )
        argv = startup_argv("CLAUDE", "", mode="", project_id="", persona_prompt=text)
        self.assertEqual(text, self._claude_persona_file_text(argv), "must be byte-for-byte, no mutation")

    def test_claude_persona_file_is_content_addressed_and_reused(self):
        text = "동일 본문은 같은 캐시 파일을 재사용한다."
        argv1 = startup_argv("CLAUDE", "", mode="", project_id="", persona_prompt=text)
        argv2 = startup_argv("CLAUDE", "", mode="", project_id="", persona_prompt=text)
        path1 = argv1[argv1.index("--append-system-prompt-file") + 1]
        path2 = argv2[argv2.index("--append-system-prompt-file") + 1]
        self.assertEqual(path1, path2)

    def test_codex_grok_persona_body_with_newlines_is_omitted_not_flattened(self):
        # 2026-09-14 round 4 Conductor finding: the previous behavior
        # silently flattened newlines to spaces and sent the mutated text
        # anyway. That is now itself a defect: startup_argv must leave the
        # persona fragment out entirely rather than mutate-and-deliver.
        text = "첫 줄\n둘째 줄\r\n셋째 줄"
        argv_codex = startup_argv("CODEX", "", mode="", project_id="", persona_prompt=text)
        self.assertFalse(
            any(a.startswith("developer_instructions=") for a in argv_codex),
            "native-queue persona text must be omitted from argv entirely",
        )
        argv_grok = startup_argv("GROK", "", mode="", project_id="", persona_prompt=text)
        self.assertNotIn("--rules", argv_grok)

    def test_codex_grok_persona_body_with_backslashes_reaches_argv_intact(self):
        text = r"경로 예시 C:\workspace\universe\tools 확인"
        argv_grok = startup_argv("GROK", "", mode="", project_id="", persona_prompt=text)
        self.assertIn(text, argv_grok[argv_grok.index("--rules") + 1])

    def test_codex_grok_persona_body_with_literal_quote_is_omitted_not_sent(self):
        # Same omission behavior as the newline case above, for the other
        # unsupported character (literal double quote).
        text = 'say "hello" please'
        argv_grok = startup_argv("GROK", "", mode="", project_id="", persona_prompt=text)
        self.assertNotIn("--rules", argv_grok)
        argv_codex = startup_argv("CODEX", "", mode="", project_id="", persona_prompt=text)
        self.assertFalse(any(a.startswith("developer_instructions=") for a in argv_codex))

    def test_managed_shell_still_fails_closed_on_a_literal_quote_as_defense_in_depth(self):
        # startup_argv no longer ever hands a quote-carrying persona string
        # to the managed-shell layer, but that layer's own fail-closed
        # rejection (documented: ambiguous across cmd and
        # CommandLineToArgvW) is verified directly here as a second,
        # independent guarantee -- this existing security boundary is left
        # unchanged by this feature.
        from universe_app.managed_shell import ManagedShellError, quote_windows_argument
        with self.assertRaises(ManagedShellError):
            quote_windows_argument('say "hello" please')

    # -- Typed unsupported, not flatten-and-mark-applied (round 4 Conductor finding) --

    def test_persona_delivery_supported_reports_per_provider(self):
        self.assertEqual((True, ""), persona_delivery_supported("CLAUDE", 'anything "with quotes"\nand newlines'))
        ok, reason = persona_delivery_supported("CODEX", "single line, no quotes")
        self.assertTrue(ok)
        ok, reason = persona_delivery_supported("CODEX", "line one\nline two")
        self.assertFalse(ok)
        self.assertIn("newline", reason)
        self.assertEqual("NATIVE_QUEUE", persona_delivery_mode("CODEX", "한글\nline\"quote")[0])
        ok, reason = persona_delivery_supported("GROK", 'has a "quote"')
        self.assertFalse(ok)
        self.assertIn("quote", reason)

    def test_codex_native_queue_mode_and_frame_preserve_exact_body(self):
        text = '  첫 줄\n둘째 "quoted" 😀\\path  \r\n'
        self.assertEqual("NATIVE_QUEUE", persona_delivery_mode("CODEX", text)[0])
        framed = native_queue_persona_text(text, message_id="persona-abc123")
        self.assertIn("Persona delivery message id: persona-abc123", framed)
        self.assertIn("--- PERSONA BODY BEGIN ---\n" + text + "\n--- PERSONA BODY END ---", framed)
        body = framed.split("--- PERSONA BODY BEGIN ---\n", 1)[1].rsplit("\n--- PERSONA BODY END ---", 1)[0]
        self.assertEqual(text, body)
        self.assertEqual("INLINE_FILE", persona_delivery_mode("CLAUDE", text)[0])
        self.assertEqual("UNSUPPORTED", persona_delivery_mode("GROK", text)[0])

    def test_codex_native_queue_message_id_is_bound_to_assignment_revision(self):
        values = {
            "persona_text": '동일한 "본문"\n두 번째 줄',
            "session_anchor_ref": "session_anchor_same",
            "persona_id": "persona_same",
            "persona_revision": 3,
        }
        first = persona_native_queue_message_id(**values, assignment_revision=7)
        replay = persona_native_queue_message_id(**values, assignment_revision=7)
        reassigned = persona_native_queue_message_id(**values, assignment_revision=8)
        self.assertEqual(first, replay)
        self.assertNotEqual(first, reassigned)

    def test_codex_native_queue_rejects_missing_or_invalid_assignment_message_id(self):
        host = TerminalHost.__new__(TerminalHost)
        host.get = Mock(return_value=SimpleNamespace(provider="CODEX"))
        for message_id in ("", "msg_wrong_namespace", "persona-has spaces"):
            with self.subTest(message_id=message_id):
                with self.assertRaises(TerminalHostError) as caught:
                    host.deliver_persona_native_queue(
                        "term-test",
                        '복합 "본문"\n둘째 줄',
                        message_id=message_id,
                    )
                self.assertEqual(
                    "PERSONA_NATIVE_QUEUE_MESSAGE_ID_INVALID",
                    caught.exception.code,
                )

    def test_server_records_codex_native_queue_acceptance_separately_from_applied(self):
        persona = self.make_persona(
            "Codex native queue",
            '한글\nquoted "body" 🚀',
        )
        session_id = f"persona-test-native-session-{next(_SESSION_ID_SEQ):08d}"
        anchor_material, _created = self.server.session_supervisor.register_session({
            "session_id": session_id,
            "node": "TEST",
            "mode": "MASTER",
            "provider": "CODEX",
            "provider_session_ref": "01a09ad0-7edc-75d0-8357-2dca0f074e8a",
        })
        anchor = str(anchor_material["session_anchor_ref"])
        _status, assigned = self.act("persona.assign", {
            "session_anchor_ref": anchor,
            "project_id": "TEST",
            "persona_id": persona["persona_id"],
            "expected_persona_revision": 1,
        })
        expected_message_id = persona_native_queue_message_id(
            persona["body"],
            session_anchor_ref=anchor,
            persona_id=persona["persona_id"],
            persona_revision=1,
            assignment_revision=assigned["assignment"]["assignment_revision"],
        )

        fake_host = Mock()
        fake_host.find_live.return_value = None
        fake_host.create.return_value = {
            "terminal_id": "term_persona_native_queue",
            "state": "LIVE",
            "provider": "CODEX",
            "host_reused_existing": False,
        }
        fake_host.deliver_persona_native_queue.return_value = {
            "status": "PERSONA_NATIVE_QUEUE_ACCEPTED",
            "message_id": expected_message_id,
            "persona_sha256": "digest",
            "delivery": {
                "message_id": expected_message_id,
                "phase": "NATIVE_QUEUED",
                "queued_submission_id": "queued-submission-test",
            },
        }
        original_host = self.server.terminal_host
        self.server.terminal_host = fake_host
        try:
            created = self.server.create_cli_terminal({
                "project_id": "TEST",
                "mode": "MASTER",
                "cwd": str(ROOT),
                "provider": "CODEX",
                "supervisor_session_id": session_id,
            })
        finally:
            self.server.terminal_host = original_host

        self.assertEqual("NATIVE_QUEUED", created["terminal"]["persona_delivery"]["status"])
        self.assertEqual(
            "PENDING_PROVIDER_PHASE",
            created["terminal"]["persona_delivery"]["application"],
        )
        fake_host.deliver_persona_native_queue.assert_called_once_with(
            "term_persona_native_queue",
            persona["body"],
            message_id=expected_message_id,
        )
        after = self.server.store.read_persona_assignment(anchor)
        self.assertIsNone(after["applied_at"], "queue receipt is not provider application")
        self.assertEqual("NATIVE_QUEUED", after["delivery_status"])
        self.assertEqual(expected_message_id, after["queued_message_id"])
        self.assertEqual("queued-submission-test", after["queued_submission_id"])
        self.assertIsNone(after["unsupported_at"])
        self.assertEqual(assigned["assignment"]["assignment_revision"], after["queued_assignment_revision"])

        # A later Host projection is the application boundary.  A mismatched
        # delayed message cannot promote this assignment.
        self.assertFalse(
            self.server.store.record_persona_applied(
                anchor,
                "term_persona_native_queue",
                persona["persona_id"],
                1,
                assigned["assignment"]["assignment_revision"],
                phase="PROMPT_SUBMITTED",
                message_id="different-message",
            )
        )
        self.assertTrue(
            self.server.store.record_persona_applied(
                anchor,
                "term_persona_native_queue",
                persona["persona_id"],
                1,
                assigned["assignment"]["assignment_revision"],
                phase="PROMPT_SUBMITTED",
                message_id=expected_message_id,
            )
        )
        applied = self.server.store.read_persona_assignment(anchor)
        self.assertEqual("APPLIED", applied["delivery_status"])
        self.assertEqual("PROMPT_SUBMITTED", applied["applied_phase"])

    def test_startup_argv_omits_unsupported_persona_text_rather_than_flatten_it(self):
        # The 2026-09-14 defect: a newline used to be silently flattened to
        # a space and sent anyway. Now it must be left OUT of argv entirely.
        text = "line one\nline two"
        argv_codex = startup_argv("CODEX", "", mode="MASTER", project_id="TEST", persona_prompt=text)
        combined = next((a for a in argv_codex if a.startswith("developer_instructions=")), "")
        self.assertNotIn("line one", combined, "unsupported persona text must not appear at all, flattened or not")
        self.assertIn("MASTER session", combined, "master framing (which IS deliverable) must still be present")

        argv_grok = startup_argv("GROK", "", mode="", project_id="", persona_prompt=text)
        self.assertNotIn("--rules", argv_grok, "no deliverable text at all for GROK here -> no --rules flag")

        # A safe, single-line, quote-free persona IS still delivered inline.
        safe_text = "single line persona, no special characters"
        argv_codex_safe = startup_argv("CODEX", "", mode="", project_id="", persona_prompt=safe_text)
        self.assertTrue(any(safe_text in a for a in argv_codex_safe))

    def test_record_persona_delivery_unsupported_cas_and_mutual_exclusion_with_applied(self):
        persona = self.make_persona("CODEX 미지원 테스트", "line one\nline two")
        anchor = self.register_anchor()
        _, assign_result = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona["persona_id"], "expected_persona_revision": 1,
        })
        assignment_revision = assign_result["assignment"]["assignment_revision"]

        stamped = self.server.store.record_persona_delivery_unsupported(
            anchor, "term_unsupported_test", persona["persona_id"], 1, assignment_revision,
            "CODEX", "contains a newline",
        )
        self.assertTrue(stamped)
        after = self.server.store.read_persona_assignment(anchor)
        self.assertIsNotNone(after["unsupported_at"])
        self.assertEqual("CODEX", after["unsupported_provider"])
        self.assertIn("newline", after["unsupported_reason"])
        self.assertIsNone(after["applied_at"], "unsupported and applied are mutually exclusive for one launch")

        # CAS: a stale (already-superseded) assignment_revision must no-op.
        stale = self.server.store.record_persona_delivery_unsupported(
            anchor, "term_stale", persona["persona_id"], 1, assignment_revision + 99,
            "CODEX", "stale write attempt",
        )
        self.assertFalse(stale)

    def test_reassign_clears_unsupported_evidence_too(self):
        persona_a = self.make_persona("A", "line one\nline two")
        persona_b = self.make_persona("B", "single line body")
        anchor = self.register_anchor()
        _, assign_a = self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona_a["persona_id"], "expected_persona_revision": 1,
        })
        self.server.store.record_persona_delivery_unsupported(
            anchor, "term_a", persona_a["persona_id"], 1, assign_a["assignment"]["assignment_revision"],
            "CODEX", "contains a newline",
        )
        self.assertIsNotNone(self.server.store.read_persona_assignment(anchor)["unsupported_at"])

        self.act("persona.assign", {
            "session_anchor_ref": anchor, "project_id": "TEST",
            "persona_id": persona_b["persona_id"], "expected_persona_revision": 1,
            "expected_assignment_revision": assign_a["assignment"]["assignment_revision"],
        })
        after = self.server.store.read_persona_assignment(anchor)
        self.assertIsNone(after["unsupported_at"], "reassigning must clear the previous unsupported stamp too")


if __name__ == "__main__":
    unittest.main()
