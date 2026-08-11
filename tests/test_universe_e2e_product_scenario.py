from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_e2e_smoke import (  # noqa: E402
    SCENARIO_ID,
    check_live,
    run_isolated,
)


class UniverseE2EProductScenarioTests(unittest.TestCase):
    def test_isolated_product_line_closes_seed_discovery_dispatch(self) -> None:
        report = run_isolated()
        self.assertEqual(SCENARIO_ID, report.scenario_id)
        self.assertEqual("run", report.mode)
        by_name = {step.name: step for step in report.steps}
        self.assertEqual("PASS", by_name["discovery_queue"].status)
        self.assertEqual("PASS", by_name["discovery_deliver"].status)
        self.assertEqual("PASS", by_name["discovery_complete"].status)
        for step_name in (
            "web_static",
            "web_health",
            "web_projects",
            "web_todos",
            "web_bench",
            "web_shutdown",
        ):
            self.assertEqual("PASS", by_name[step_name].status, step_name)
        self.assertEqual("PASS", report.overall)

    def test_smoke_report_is_json_serializable(self) -> None:
        report = run_isolated()
        payload = report.to_dict()
        raw = json.dumps(payload)
        self.assertIn(SCENARIO_ID, raw)
        self.assertIn("overall", payload)

    def test_live_check_is_optional_when_service_absent(self) -> None:
        missing = Path(self.id().replace(".", "_") + "-missing-server.json")
        report = check_live(missing, "GCS")
        self.assertEqual("FAIL", report.overall)
        self.assertEqual("FAIL", report.steps[0].status)


if __name__ == "__main__":
    unittest.main()
