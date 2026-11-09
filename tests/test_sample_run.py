"""The committed offline sample must replay to exactly the committed output."""

from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from gus_reporter import cli

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_run"
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)?")


def _read(path: Path) -> object:
    text = _TIMESTAMP.sub("<ts>", path.read_text(encoding="utf-8"))
    return json.loads(text) if path.suffix == ".json" else text


class SampleRunTests(unittest.TestCase):
    def test_replay_matches_committed_output_without_keys(self) -> None:
        with tempfile.TemporaryDirectory() as scratch, contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch.dict("os.environ", {}, clear=True), unittest.mock.patch(
                "gus_reporter.config.load_runtime_env"
            ):
                cli.main(
                    [
                        "--request", str(SAMPLE / "request.json"),
                        "--replay", str(SAMPLE / "cassette"),
                        "--output-dir", scratch,
                    ]
                )
            (run_dir,) = Path(scratch).iterdir()
            produced = sorted(p.relative_to(run_dir) for p in run_dir.rglob("*") if p.is_file())
            committed = sorted(
                p.relative_to(SAMPLE / "output") for p in (SAMPLE / "output").rglob("*") if p.is_file()
            )
            self.assertEqual(committed, produced)
            self.maxDiff = None
            for rel in produced:
                self.assertEqual(_read(SAMPLE / "output" / rel), _read(run_dir / rel), str(rel))

    def test_sample_shows_kept_and_suppressed_claims(self) -> None:
        claims = json.loads((SAMPLE / "output" / "verified_claims.json").read_text())
        statuses = {claim["status"] for claim in claims}
        self.assertIn("supported", statuses)
        self.assertTrue(statuses & {"unsupported", "contradicted", "insufficient_evidence"})
        report = json.loads((SAMPLE / "output" / "final_report.json").read_text())
        self.assertEqual("publish", report["publish_decision"])


if __name__ == "__main__":
    unittest.main()
