from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gus_reporter.evaluation import __main__ as eval_cli
from gus_reporter.evaluation.citations import check_claim
from gus_reporter.evaluation.judge import judge_claim
from gus_reporter.evaluation.labels import cohens_kappa, read_labels
from gus_reporter.evaluation.metrics import aggregate, score_run, wilson

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_OUTPUT = ROOT / "examples" / "sample_run" / "output"

EVIDENCE = {
    "e1": {
        "evidence_id": "e1",
        "label": "IR",
        "category": "financials",
        "url": "https://x.example/ir",
        "retrieved_at": "t",
        "content": "Revenue grew 12%   to $40 million.",
        "metadata": {},
    }
}


class FakeJudge:
    def __init__(self, verdict: str, sentence: str) -> None:
        self.verdict = verdict
        self.sentence = sentence
        self.prompts: list[str] = []

    def structured_response(self, **kwargs):  # type: ignore[no-untyped-def]
        self.prompts.append(kwargs["user_content"][0]["text"])
        return {"verdict": self.verdict, "supporting_sentence": self.sentence, "rationale": "r"}


def _claim(**overrides):  # type: ignore[no-untyped-def]
    claim = {
        "claim_id": "c1",
        "statement": "Revenue grew 12%.",
        "status": "supported",
        "evidence_ids": ["e1"],
        "source_quote": "revenue grew 12% to $40 million",
        "rationale": "ok",
    }
    claim.update(overrides)
    return claim


class CitationTests(unittest.TestCase):
    def test_quote_grounding_folds_whitespace_and_case(self) -> None:
        check = check_claim(_claim(), EVIDENCE)
        self.assertTrue(check.ids_resolve)
        self.assertTrue(check.quote_grounded)

    def test_dangling_evidence_id_fails_resolution(self) -> None:
        check = check_claim(_claim(evidence_ids=["e1", "ghost"]), EVIDENCE)
        self.assertFalse(check.ids_resolve)

    def test_quote_absent_from_source_is_not_grounded(self) -> None:
        self.assertFalse(check_claim(_claim(source_quote="net income rose"), EVIDENCE).quote_grounded)


class JudgeTests(unittest.TestCase):
    def test_judge_never_sees_the_pipeline_verdict(self) -> None:
        judge = FakeJudge("supported", "Revenue grew 12% to $40 million.")
        judge_claim(judge, _claim(rationale="SECRET RATIONALE"), EVIDENCE, Path("."))
        self.assertNotIn("SECRET RATIONALE", judge.prompts[0])
        self.assertNotIn("supported", judge.prompts[0].split("Evidence bundle:")[0])

    def test_supported_without_a_real_supporting_sentence_is_downgraded(self) -> None:
        result = judge_claim(FakeJudge("supported", "Revenue doubled."), _claim(), EVIDENCE, Path("."))
        self.assertEqual("not_supported", result["verdict"])
        self.assertTrue(result["downgraded"])

    def test_verbatim_sentence_keeps_the_verdict(self) -> None:
        result = judge_claim(
            FakeJudge("supported", "Revenue grew 12% to $40 million."), _claim(), EVIDENCE, Path(".")
        )
        self.assertEqual("supported", result["verdict"])


class StatisticsTests(unittest.TestCase):
    def test_cohens_kappa_matches_hand_computation(self) -> None:
        pairs = [("a", "a"), ("a", "b"), ("b", "b"), ("b", "b")]
        self.assertAlmostEqual(0.5, cohens_kappa(pairs))

    def test_wilson_interval(self) -> None:
        self.assertIsNone(wilson(0, 0))
        low, high = wilson(10, 10)
        self.assertAlmostEqual(0.722, low, places=3)
        self.assertEqual(1.0, high)


class ScoringTests(unittest.TestCase):
    def test_sample_run_scores_with_hand_labels(self) -> None:
        labels = read_labels(ROOT / "eval" / "labels" / "sample_run.csv")
        row = score_run("output", "real", SAMPLE_OUTPUT, labels)
        self.assertEqual(16, row["claims_extracted"])
        self.assertEqual(11, row["claims_kept"])
        self.assertEqual(11, row["kept_judged"])
        self.assertEqual(11, row["citations"]["quote_grounded"])

    def test_kept_claims_on_a_fictitious_company_are_counted_as_errors(self) -> None:
        row = score_run("fake", "fictitious", SAMPLE_OUTPUT)
        agg = aggregate([row])
        self.assertEqual(11, agg["fictitious"]["claims_kept"])
        self.assertEqual(1, agg["fictitious"]["runs_with_any_kept_claim"])

    def test_judge_run_writes_judgements_that_scoring_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "quill"
            shutil.copytree(SAMPLE_OUTPUT, run_dir)
            judge = FakeJudge("partially_supported", "")
            results = eval_cli.judge_run(run_dir, judge, audit_suppressed=2, sample=4)
            self.assertEqual(6, len(results))
            row = score_run("quill", "real", run_dir)
            self.assertEqual(4, row["kept_judged"])
            self.assertEqual(4, row["kept_partial"])
            self.assertEqual(2, row["suppressed_judged"])


class SpendGateTests(unittest.TestCase):
    def test_run_prints_estimate_and_spends_nothing_without_confirmation(self) -> None:
        env = {"OPENAI_API_KEY": "k", "TINYFISH_API_KEY": "k"}
        out = io.StringIO()
        with patch.dict("os.environ", env, clear=True), patch(
            "gus_reporter.config.load_runtime_env"
        ), patch.object(eval_cli, "live_clients") as live, patch(
            "sys.stdin", io.StringIO("")
        ), contextlib.redirect_stdout(out):
            code = eval_cli.main(
                ["run", "--suite", str(ROOT / "eval" / "suite.json"), "--max-usd", "1"]
            )
        self.assertEqual(1, code)
        live.assert_not_called()
        self.assertIn("TOTAL", out.getvalue())
        self.assertIn("--yes", out.getvalue())


if __name__ == "__main__":
    unittest.main()
