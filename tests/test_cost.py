from __future__ import annotations

import unittest

from gus_reporter.cost import estimate_run, model_price, request_shape, token_cost
from gus_reporter.models import ReportRequest


class CostTests(unittest.TestCase):
    def test_token_cost_bills_cached_input_at_the_cached_rate(self) -> None:
        # 1M uncached in at $0.75, 1M cached at $0.075, 1M out at $4.50.
        usd = token_cost(
            "gpt-5.4-mini", input_tokens=2_000_000, cached_input_tokens=1_000_000, output_tokens=1_000_000
        )
        self.assertAlmostEqual(0.75 + 0.075 + 4.50, usd)

    def test_dated_snapshot_uses_the_family_price(self) -> None:
        self.assertEqual(model_price("gpt-5.4-mini"), model_price("gpt-5.4-mini-2026-03-17"))
        self.assertNotEqual(model_price("gpt-5.4"), model_price("gpt-5.4-mini"))
        with self.assertRaises(KeyError):
            model_price("unknown-model")

    def test_request_shape_counts_specialists_and_seed_jobs_exactly(self) -> None:
        request = ReportRequest(company_name="Acme", company_website="https://acme.example")
        shape = request_shape(request, max_total_jobs=50)
        # 8 domains: overview 1, financials 5, legal 5, industry 1, leadership 1,
        # employees 1, news 4, risk 1.
        self.assertEqual(19, shape["extraction_calls"])
        self.assertEqual(8, shape["sections"])
        self.assertEqual(16, shape["seed_jobs"])

    def test_estimates_are_ordered_low_to_high(self) -> None:
        request = ReportRequest(company_name="Acme", company_website="https://acme.example")
        estimates = estimate_run(request, model="gpt-5.4-mini", judge_model="gpt-5.4")["estimates"]
        self.assertLess(estimates["low"]["total_usd"], estimates["typical"]["total_usd"])
        self.assertLess(estimates["typical"]["total_usd"], estimates["high"]["total_usd"])
        self.assertGreater(estimates["typical"]["judge_usd"], 0)

    def test_per_claim_mode_reproduces_the_pre_batching_estimate(self) -> None:
        # The figures the README quoted before batched verification.
        request = ReportRequest(company_name="Acme", company_website="https://acme.example",
                                ticker="ACME", primary_jurisdiction="US")
        old = estimate_run(request, model="gpt-5.4-mini", verification_mode="per_claim")["estimates"]
        self.assertEqual((2.65, 18.21, 131.89), tuple(old[k]["total_usd"] for k in ("low", "typical", "high")))

    def test_batched_verification_is_cheaper_in_every_scenario(self) -> None:
        request = ReportRequest(company_name="Acme", company_website="https://acme.example")
        old = estimate_run(request, model="gpt-5.4-mini", verification_mode="per_claim")["estimates"]
        new = estimate_run(request, model="gpt-5.4-mini")["estimates"]
        for name in ("low", "typical", "high"):
            self.assertLess(new[name]["verification_usd"], old[name]["verification_usd"])
            self.assertEqual(new[name]["browse_usd"], old[name]["browse_usd"])


if __name__ == "__main__":
    unittest.main()
