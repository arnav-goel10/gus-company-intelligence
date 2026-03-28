from __future__ import annotations

import unittest
from unittest.mock import patch

from gus_reporter.ui_server import GusUIApplication


class GusUIApplicationTests(unittest.TestCase):
    def test_background_startup_failure_marks_run_failed(self) -> None:
        app = GusUIApplication()
        run_id = app.store.create_run({"company_name": "Example Corp"})

        with patch(
            "gus_reporter.ui_server.AppConfig.from_env",
            side_effect=RuntimeError("Broken configuration"),
        ):
            app._run_pipeline(run_id, {"company_name": "Example Corp"})

        run = app.store.get_run(run_id)
        assert run is not None
        self.assertEqual("failed", run["status"])
        self.assertEqual("planning", run["current_stage"])
        self.assertEqual("Broken configuration", run["error"])
        self.assertEqual("failed", run["stages"]["planning"]["status"])

    def test_normalize_request_payload_defaults_to_three_domains(self) -> None:
        app = GusUIApplication()

        payload = app._normalize_request_payload({"company_name": "Example Corp"})

        self.assertEqual(["financials", "legal", "news"], payload["focus_areas"])
        self.assertIn("multi-domain diligence report", payload["report_goal"])


if __name__ == "__main__":
    unittest.main()
