from __future__ import annotations

import unittest

from gus_reporter.models import ReportRequest, VerifiedClaim
from gus_reporter.pipeline import compute_publish_decision, seed_source_jobs


class SeedSourceJobsTests(unittest.TestCase):
    def test_seed_jobs_from_company_website(self) -> None:
        request = ReportRequest(
            company_name="Example Corp",
            company_website="https://example.com",
            primary_jurisdiction="US",
        )
        jobs = seed_source_jobs(request, "stealth")
        self.assertEqual(6, len(jobs))
        self.assertEqual("https://example.com", jobs[0].url)
        self.assertEqual("https://example.com/leadership", jobs[1].url)
        self.assertEqual("stealth", jobs[0].browser_profile)
        self.assertEqual("company_homepage", jobs[0].source_type)
        self.assertEqual("financial_investor_candidate", jobs[4].source_type)
        self.assertEqual("lite", jobs[4].browser_profile)

    def test_financial_seed_jobs_include_ir_and_sec_when_available(self) -> None:
        request = ReportRequest(
            company_name="Example Corp",
            company_website="https://example.com",
            primary_jurisdiction="US",
            ticker="EXMPL",
            investor_relations_url="https://example.com/investors",
            financial_source_urls=["https://example.com/annual-report"],
            focus_areas=["financials"],
        )
        jobs = seed_source_jobs(request, "stealth")
        financial_jobs = [job for job in jobs if job.category == "financials"]
        financial_urls = [job.url for job in financial_jobs]
        self.assertIn("https://example.com/investors", financial_urls)
        self.assertIn("https://example.com/annual-report", financial_urls)
        self.assertIn(
            "https://www.sec.gov/edgar/browse/?CIK=EXMPL&owner=exclude",
            financial_urls,
        )
        self.assertTrue(all(job.enable_agent_memory for job in financial_jobs))
        self.assertTrue(all(job.allow_stealth_fallback for job in financial_jobs))
        self.assertTrue(all(job.browser_profile == "lite" for job in financial_jobs))

    def test_request_level_tinyfish_settings_propagate_to_seeded_jobs(self) -> None:
        request = ReportRequest(
            company_name="Example Corp",
            company_website="https://example.com",
            primary_jurisdiction="US",
            focus_areas=["financials"],
            tinyfish_use_vault=True,
            tinyfish_credential_item_ids=["item_abc123"],
            tinyfish_enable_agent_memory=True,
        )
        jobs = seed_source_jobs(request, "stealth")
        self.assertTrue(jobs)
        self.assertTrue(all(job.use_vault for job in jobs))
        self.assertEqual(["item_abc123"], jobs[0].credential_item_ids)
        self.assertTrue(all(job.enable_agent_memory for job in jobs))


class PublishDecisionTests(unittest.TestCase):
    def test_publish_decision_blocks_weak_coverage(self) -> None:
        claims = [
            VerifiedClaim(
                claim_id="c1",
                section="company_overview",
                statement="Example Corp sells software.",
                status="supported",
                confidence=0.9,
                rationale="Matches evidence.",
                evidence_ids=["e1"],
                blocking_issues=[],
                source_quote="Example quote",
                importance="high",
                as_of="2026-03-28",
            )
        ]
        decision, reason = compute_publish_decision(
            ["company_overview", "financials", "legal", "news"], claims
        )
        self.assertEqual("blocked", decision)
        self.assertIn("minimum required", reason)

    def test_publish_decision_passes_with_broad_coverage(self) -> None:
        claims = []
        for idx, section in enumerate(
            ["company_overview", "financials", "legal", "news"], start=1
        ):
            claims.append(
                VerifiedClaim(
                    claim_id=f"c{idx}",
                    section=section,
                    statement=f"Supported claim for {section}",
                    status="supported",
                    confidence=0.9,
                    rationale="Matches evidence.",
                    evidence_ids=[f"e{idx}"],
                    blocking_issues=[],
                    source_quote="Example quote",
                    importance="high",
                    as_of="2026-03-28",
                )
            )
        decision, _reason = compute_publish_decision(
            ["company_overview", "financials", "legal", "news"], claims
        )
        self.assertEqual("publish", decision)


if __name__ == "__main__":
    unittest.main()
