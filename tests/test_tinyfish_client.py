from __future__ import annotations

import unittest

from gus_reporter.models import SourceJob
from gus_reporter.tinyfish_client import TinyfishClient, _PendingRun


class FakeTinyfishClient(TinyfishClient):
    def __init__(self) -> None:
        super().__init__(
            api_key="test",
            base_url="https://agent.tinyfish.ai/v1",
            poll_interval_seconds=0,
            poll_timeout_seconds=5,
            batch_size=20,
            request_max_retries=0,
            request_retry_backoff_seconds=0.1,
        )
        self.started_profiles: list[str] = []

    def _start_async_run(self, job: SourceJob, browser_profile: str) -> str:
        run_id = f"run_{len(self.started_profiles) + 1}"
        self.started_profiles.append(browser_profile)
        return run_id

    def _fetch_runs_batch(self, run_ids: list[str]) -> dict[str, dict]:
        response: dict[str, dict] = {}
        for run_id in run_ids:
            if run_id == "run_1":
                response[run_id] = {
                    "run_id": run_id,
                    "status": "FAILED",
                    "error": {"message": "Blocked by anti-bot challenge"},
                }
            elif run_id == "run_2":
                response[run_id] = {
                    "run_id": run_id,
                    "status": "COMPLETED",
                    "result": {
                        "success": True,
                        "documents": [{"title": "Annual Report 2025"}],
                    },
                }
        return response


class TinyfishClientTests(unittest.TestCase):
    def test_build_payload_includes_vault_memory_and_proxy(self) -> None:
        client = TinyfishClient(
            api_key="test",
            base_url="https://agent.tinyfish.ai/v1",
            poll_interval_seconds=1,
            poll_timeout_seconds=5,
            batch_size=20,
            request_max_retries=0,
            request_retry_backoff_seconds=0.1,
        )
        job = SourceJob(
            label="Investor relations",
            category="financials",
            url="https://example.com/investors",
            goal="Extract financial disclosures",
            source_type="financial_investor_relations",
            browser_profile="lite",
            proxy_country_code="US",
            use_vault=True,
            credential_item_ids=["item_abc123"],
            enable_agent_memory=True,
        )
        payload = client._build_payload(job, "lite")
        self.assertEqual("https://example.com/investors", payload["url"])
        self.assertEqual("lite", payload["browser_profile"])
        self.assertEqual({"enable_agent_memory": True}, payload["feature_flags"])
        self.assertTrue(payload["use_vault"])
        self.assertEqual(["item_abc123"], payload["credential_item_ids"])
        self.assertEqual(
            {"enabled": True, "country_code": "US"},
            payload["proxy_config"],
        )

    def test_blocked_job_is_recorded_as_blocked_and_never_retried(self) -> None:
        events: list[dict] = []
        client = FakeTinyfishClient()
        client.event_callback = events.append
        job = SourceJob(
            label="Investor relations",
            category="financials",
            url="https://example.com/investors",
            goal="Extract financial disclosures",
            source_type="financial_investor_relations",
            browser_profile="lite",
        )
        records = client.run_jobs([job])
        self.assertEqual(1, len(records))
        self.assertEqual(["lite"], client.started_profiles)
        self.assertEqual("FAILED", records[0].status)
        self.assertTrue(records[0].access_blocked)
        self.assertEqual(1, len(records[0].attempts))
        finished = [e for e in events if e.get("status") == "failed"]
        self.assertIn("not retried", finished[-1]["message"])

    def test_clean_run_is_not_marked_blocked(self) -> None:
        client = FakeTinyfishClient()
        client.started_profiles.append("lite")  # next run id is run_2 (clean)
        job = SourceJob(
            label="Investor relations",
            category="financials",
            url="https://example.com/investors",
            goal="Extract financial disclosures",
            browser_profile="lite",
        )
        records = client.run_jobs([job])
        self.assertEqual("COMPLETED", records[0].status)
        self.assertFalse(records[0].access_blocked)

    def test_stealth_profile_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SourceJob.from_dict(
                {
                    "label": "x",
                    "category": "news",
                    "url": "https://example.com",
                    "goal": "g",
                    "browser_profile": "stealth",
                },
                "lite",
            )

    def test_completed_record_normalizes_string_none_streaming_url(self) -> None:
        client = TinyfishClient(
            api_key="test",
            base_url="https://agent.tinyfish.ai/v1",
            poll_interval_seconds=1,
            poll_timeout_seconds=5,
            batch_size=50,
            request_max_retries=0,
            request_retry_backoff_seconds=0.1,
        )
        job = SourceJob(
            label="Investor relations",
            category="financials",
            url="https://example.com/investors",
            goal="Extract financial disclosures",
            source_type="financial_investor_relations",
            browser_profile="lite",
        )
        pending = _PendingRun(
            index=0,
            job=job,
            run_id="run_1",
            browser_profile="lite",
            attempt_number=1,
        )
        record = client._record_from_run_data(
            data={
                "run_id": pending.run_id,
                "status": "COMPLETED",
                "streaming_url": "None",
                "result": {"success": True},
            },
            pending=pending,
        )
        self.assertEqual("", record.streaming_url)


if __name__ == "__main__":
    unittest.main()
