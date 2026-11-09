from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from golden_support import (
    E2E_REQUEST,
    ScriptedModel,
    assert_golden,
    discovery_request,
    discovery_runs,
    fake_tinyfish_run_jobs,
    fake_urlopen,
    normalise,
    seed_requests,
)
from gus_reporter.config import AppConfig
from gus_reporter.models import ReportRequest
from gus_reporter.openai_client import OpenAIClient
from gus_reporter.tinyfish_client import TinyfishClient
from gus_reporter.pipeline import (
    ReportPipeline,
    build_raw_financial_source_refs,
    build_raw_legal_source_refs,
    build_raw_news_source_refs,
    build_spawned_financial_source_jobs,
    build_spawned_legal_source_jobs,
    build_spawned_news_source_jobs,
    seed_source_jobs,
)


def golden_config(output_dir: Path) -> AppConfig:
    return AppConfig(
        openai_api_key="unused",
        tinyfish_api_key="unused",
        openai_base_url="https://api.openai.com/v1",
        openai_model="gpt-5.4-mini",
        tinyfish_base_url="https://agent.tinyfish.ai/v1",
        report_output_dir=output_dir,
        tinyfish_default_browser_profile="lite",
        tinyfish_poll_interval_seconds=0,
        tinyfish_poll_timeout_seconds=5,
        tinyfish_request_timeout_seconds=5,
        raw_source_timeout_seconds=5,
        openai_request_timeout_seconds=5,
        openai_request_max_retries=0,
        openai_request_retry_backoff_seconds=0.1,
        openai_extraction_max_workers=1,
        openai_verification_max_workers=1,
        tinyfish_batch_size=10,
        tinyfish_max_total_jobs=30,
        tinyfish_max_acquisition_waves=3,
        tinyfish_request_max_retries=0,
        tinyfish_request_retry_backoff_seconds=0.1,
    )


class PlanningGoldenTests(unittest.TestCase):
    def test_seed_source_jobs(self) -> None:
        actual = {
            name: [job.to_dict() for job in seed_source_jobs(request, "lite")]
            for name, request in seed_requests().items()
        }
        assert_golden(self, "seed_source_jobs", actual)


class DiscoveryGoldenTests(unittest.TestCase):
    def test_spawned_jobs_and_raw_refs(self) -> None:
        request = discovery_request()
        runs = discovery_runs()
        existing = {"https://example.com/investors/overview", "https://news.example.org/a"}
        actual = {
            "spawned_financial": [
                job.to_dict()
                for job in build_spawned_financial_source_jobs(request, runs, "lite", existing)
            ],
            "spawned_legal": [
                job.to_dict()
                for job in build_spawned_legal_source_jobs(request, runs, "lite", existing)
            ],
            "spawned_news": [
                job.to_dict()
                for job in build_spawned_news_source_jobs(request, runs, "lite", existing)
            ],
            "existing_urls_after": sorted(existing),
            "raw_financial": build_raw_financial_source_refs(request, runs),
            "raw_legal": build_raw_legal_source_refs(request, runs),
            "raw_news": build_raw_news_source_refs(request, runs),
        }
        assert_golden(self, "discovery", actual)


class EndToEndGoldenTests(unittest.TestCase):
    def test_full_run_artifacts_model_calls_and_events(self) -> None:
        events: list[dict] = []
        model = ScriptedModel()
        # Patch at class level so the test does not depend on how the
        # pipeline wires its clients together.
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            OpenAIClient,
            "structured_response",
            lambda _self, **kwargs: model.structured_response(**kwargs),
        ), patch.object(
            TinyfishClient, "run_jobs", lambda _self, jobs: fake_tinyfish_run_jobs(jobs)
        ), patch("gus_reporter.raw_source_client.urllib.request.urlopen", fake_urlopen):
            pipeline = ReportPipeline(golden_config(Path(temp_dir)), progress_callback=events.append)
            request = ReportRequest.from_dict(E2E_REQUEST, "lite")
            report, run_dir = pipeline.run(request)

            artifacts = {}
            for path in sorted(Path(run_dir).rglob("*")):
                if path.is_file():
                    rel = str(path.relative_to(run_dir))
                    if path.suffix == ".json":
                        artifacts[rel] = json.loads(path.read_text(encoding="utf-8"))
                    else:
                        artifacts[rel] = path.read_bytes().decode("utf-8", errors="replace")

            stripped_events = [
                {k: v for k, v in event.items() if k != "timestamp"} for event in events
            ]
            actual = normalise(
                {
                    "artifacts": artifacts,
                    "model_calls": model.calls,
                    "events": stripped_events,
                    "report_matches_artifact": report.to_dict()
                    == artifacts["final_report.json"],
                },
                run_dir=run_dir,
            )
        assert_golden(self, "end_to_end", actual)


if __name__ == "__main__":
    unittest.main()
