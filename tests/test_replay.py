from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from golden_support import _RAW_PAGES, E2E_REQUEST, ScriptedModel, fake_tinyfish_run_jobs
from test_golden_behaviour import golden_config

from gus_reporter.cost import run_cost
from gus_reporter.models import ReportRequest
from gus_reporter.pipeline import ReportPipeline
from gus_reporter.raw_source_client import FetchedSource, RawSourceClient
from gus_reporter.render import render_markdown
from gus_reporter.replay import (
    Cassette,
    RecordingBrowser,
    RecordingFetcher,
    RecordingModel,
    ReplayBrowser,
    ReplayFetcher,
    ReplayMiss,
    ReplayModel,
    UsageMeter,
)


class _Browser:
    def run_jobs(self, jobs):  # type: ignore[no-untyped-def]
        return fake_tinyfish_run_jobs(jobs)


class _Fetcher(RawSourceClient):
    def fetch(self, url: str) -> FetchedSource:
        if url not in _RAW_PAGES:
            return FetchedSource(error="HTTP 404: missing")
        content_type, body = _RAW_PAGES[url]
        return FetchedSource(
            final_url=url, content_type=content_type, body=body, retrieved_at="2026-01-01T00:00:00+00:00"
        )


class _UsageModel(ScriptedModel):
    def structured_response_with_usage(self, **kwargs):  # type: ignore[no-untyped-def]
        payload = self.structured_response(**kwargs)
        return payload, {
            "model": "gpt-5.4-mini",
            "input_tokens": 1000,
            "cached_input_tokens": 200,
            "output_tokens": 100,
            "reasoning_tokens": 0,
        }


def _outputs(run_dir: str) -> dict:
    root = Path(run_dir)
    return {
        name: json.loads((root / name).read_text())
        for name in ("extracted_claims.json", "verified_claims.json", "evidence.json")
    }


class ReplayRoundTripTests(unittest.TestCase):
    def test_replay_reproduces_a_recorded_run_with_no_live_clients(self) -> None:
        request = ReportRequest.from_dict(E2E_REQUEST, "lite")
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            cassette = Cassette(temp_path / "cassette")
            recorder = ReportPipeline(
                golden_config(temp_path / "runs"),
                browser=RecordingBrowser(_Browser(), cassette),
                fetcher=RecordingFetcher(_Fetcher(), cassette),
                llm=RecordingModel(_UsageModel(), cassette),
            )
            recorded_report, recorded_dir = recorder.run(request)
            cassette.save()

            loaded = Cassette(temp_path / "cassette")
            meter = UsageMeter(ReplayModel(loaded))
            replayer = ReportPipeline(
                golden_config(temp_path / "replays"),
                browser=ReplayBrowser(loaded),
                fetcher=ReplayFetcher(loaded),
                llm=meter,
            )
            replayed_report, replayed_dir = replayer.run(request)
            meter.write(Path(replayed_dir))

            self.assertEqual(_outputs(recorded_dir), _outputs(replayed_dir))
            recorded = recorded_report.to_dict()
            replayed = replayed_report.to_dict()
            recorded.pop("generated_at")
            replayed.pop("generated_at")
            self.assertEqual(recorded, replayed)

            # Recorded usage comes back on replay, so the run's cost is reproducible.
            cost = run_cost(Path(replayed_dir))
            successful_calls = sum(1 for entry in loaded.model.values() if "response" in entry)
            self.assertEqual(successful_calls * 1000, cost["input_tokens"])
            self.assertGreater(cost["model_usd"], 0)

            markdown = render_markdown(Path(replayed_dir))
            self.assertIn("## Suppressed claims", markdown)
            self.assertIn("contradicted", markdown)

    def test_replay_fails_loudly_when_the_prompt_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cassette = Cassette(Path(temp))
            model = ReplayModel(cassette)
            with self.assertRaises(ReplayMiss):
                model.structured_response(
                    schema_name="claim_verification",
                    schema={},
                    system_prompt="changed",
                    user_content=[{"type": "input_text", "text": "x"}],
                )


if __name__ == "__main__":
    unittest.main()
