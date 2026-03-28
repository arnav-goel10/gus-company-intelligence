from __future__ import annotations

import unittest

from gus_reporter.models import SourceJob
from gus_reporter.tinyfish_client import TinyfishClient


class FakeTinyfishClient(TinyfishClient):
    def __init__(self) -> None:
        super().__init__(
            api_key="test",
            base_url="https://agent.tinyfish.ai/v1",
            poll_interval_seconds=0,
            poll_timeout_seconds=5,
            batch_size=20,
            retry_with_stealth=True,
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
            retry_with_stealth=True,
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

    def test_run_jobs_retries_blocked_financial_job_with_stealth(self) -> None:
        client = FakeTinyfishClient()
        job = SourceJob(
            label="Investor relations",
            category="financials",
            url="https://example.com/investors",
            goal="Extract financial disclosures",
            source_type="financial_investor_relations",
            browser_profile="lite",
            proxy_country_code="US",
            allow_stealth_fallback=True,
        )
        records = client.run_jobs([job])
        self.assertEqual(1, len(records))
        self.assertEqual(["lite", "stealth"], client.started_profiles)
        self.assertEqual("COMPLETED", records[0].status)
        self.assertEqual(2, len(records[0].attempts))
        self.assertEqual("stealth", records[0].attempts[-1]["browser_profile"])


if __name__ == "__main__":
    unittest.main()
