from __future__ import annotations

import unittest

from gus_reporter.ui_state import RunStateStore


class RunStateStoreTests(unittest.TestCase):
    def test_stage_and_source_updates_are_projected_into_run_state(self) -> None:
        store = RunStateStore()
        run_id = store.create_run({"company_name": "Example Corp"})

        store.record_event(
            run_id,
            {
                "event": "pipeline_started",
                "run_dir": "/tmp/example-run",
                "openai_model": "gpt-5.4-mini",
            },
        )
        store.record_event(
            run_id,
            {
                "event": "stage_update",
                "stage": "planning",
                "status": "completed",
                "message": "Planned 2 source jobs.",
                "current": 2,
                "total": 2,
                "jobs": [
                    {
                        "label": "Investor relations",
                        "category": "financials",
                        "url": "https://example.com/investors",
                        "source_type": "financial_investor_relations",
                        "browser_profile": "lite",
                    }
                ],
            },
        )
        store.record_event(
            run_id,
            {
                "event": "source_job_update",
                "stage": "acquisition",
                "label": "Investor relations",
                "category": "financials",
                "url": "https://example.com/investors",
                "source_type": "financial_investor_relations",
                "status": "running",
                "message": "Tinyfish run started.",
                "run_id": "run_123",
                "browser_profile": "lite",
                "attempt_number": 1,
            },
        )

        run = store.get_run(run_id)
        assert run is not None
        self.assertEqual("running", run["status"])
        self.assertEqual("/tmp/example-run", run["run_dir"])
        self.assertEqual("gpt-5.4-mini", run["openai_model"])
        self.assertEqual("completed", run["stages"]["planning"]["status"])
        self.assertEqual(1, len(run["source_jobs"]))
        self.assertEqual("running", run["source_jobs"][0]["status"])
        self.assertEqual("run_123", run["source_jobs"][0]["run_id"])

    def test_pipeline_completed_captures_report(self) -> None:
        store = RunStateStore()
        run_id = store.create_run({"company_name": "Example Corp"})
        store.record_event(
            run_id,
            {
                "event": "pipeline_completed",
                "run_dir": "/tmp/example-run",
                "final_report": {"company_name": "Example Corp", "publish_decision": "blocked"},
            },
        )
        run = store.get_run(run_id)
        assert run is not None
        self.assertEqual("completed", run["status"])
        self.assertEqual("blocked", run["final_report"]["publish_decision"])

    def test_pipeline_failed_marks_active_stage_and_pending_jobs_failed(self) -> None:
        store = RunStateStore()
        run_id = store.create_run({"company_name": "Example Corp"})
        store.record_event(
            run_id,
            {
                "event": "stage_update",
                "stage": "acquisition",
                "status": "running",
                "message": "Running Tinyfish across discovered sources.",
                "current": 0,
                "total": 2,
                "jobs": [
                    {
                        "label": "Investor relations",
                        "category": "financials",
                        "url": "https://example.com/investors",
                        "source_type": "financial_investor_relations",
                        "browser_profile": "lite",
                    },
                    {
                        "label": "Annual report",
                        "category": "financials",
                        "url": "https://example.com/annual-report.pdf",
                        "source_type": "financial_document",
                        "browser_profile": "lite",
                    },
                ],
            },
        )
        store.record_event(
            run_id,
            {
                "event": "source_job_update",
                "stage": "acquisition",
                "label": "Investor relations",
                "category": "financials",
                "url": "https://example.com/investors",
                "source_type": "financial_investor_relations",
                "status": "running",
                "message": "Tinyfish run started.",
                "run_id": "run_123",
                "browser_profile": "lite",
                "attempt_number": 1,
            },
        )
        store.record_event(
            run_id,
            {
                "event": "pipeline_failed",
                "stage": "acquisition",
                "error": "Tinyfish request failed.",
            },
        )

        run = store.get_run(run_id)
        assert run is not None
        self.assertEqual("failed", run["status"])
        self.assertEqual("acquisition", run["current_stage"])
        self.assertEqual("failed", run["stages"]["acquisition"]["status"])
        self.assertEqual("Tinyfish request failed.", run["stages"]["acquisition"]["message"])
        self.assertEqual("failed", run["source_jobs"][0]["status"])
        self.assertEqual("Tinyfish run started.", run["source_jobs"][0]["message"])
        self.assertEqual("failed", run["source_jobs"][1]["status"])
        self.assertEqual(
            "Run aborted before completion.",
            run["source_jobs"][1]["message"],
        )


if __name__ == "__main__":
    unittest.main()
