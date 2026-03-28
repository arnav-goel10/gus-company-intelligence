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


if __name__ == "__main__":
    unittest.main()
