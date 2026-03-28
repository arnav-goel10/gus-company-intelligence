from __future__ import annotations

import unittest

from gus_reporter.agents import DOMAIN_AGENTS, get_extraction_agents
from gus_reporter.models import EvidenceItem, ReportRequest, TinyfishRunRecord, VerifiedClaim
from gus_reporter.pipeline import (
    build_raw_financial_source_refs,
    build_spawned_financial_source_jobs,
    compute_publish_decision,
    normalize_tinyfish_runs,
    seed_source_jobs,
    select_evidence_for_extraction_agent,
)


class SeedSourceJobsTests(unittest.TestCase):
    def test_company_name_only_financial_request_seeds_search_discovery_jobs(self) -> None:
        request = ReportRequest(
            company_name="Apple",
            focus_areas=["financials"],
        )
        jobs = seed_source_jobs(request, "stealth")
        self.assertEqual(3, len(jobs))
        self.assertTrue(all(job.category == "financials" for job in jobs))
        self.assertTrue(all(job.source_type == "financial_search_discovery" for job in jobs))
        self.assertTrue(all(job.url.startswith("https://www.bing.com/search?q=") for job in jobs))

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

    def test_discovery_runs_spawn_child_financial_source_jobs(self) -> None:
        request = ReportRequest(
            company_name="Apple",
            focus_areas=["financials"],
        )
        runs = [
            TinyfishRunRecord(
                run_id="run_1",
                status="COMPLETED",
                goal="Discover sources",
                label="Financial source discovery",
                category="financials",
                url="https://www.bing.com/search?q=apple+investor+relations",
                source_type="financial_search_discovery",
                result={
                    "capture_mode": "search_discovery",
                    "sources": [
                        {
                            "page_title": "Apple Investor Relations",
                            "final_url": "https://investor.apple.com/",
                            "source_kind": "investor_relations_hub",
                            "linked_documents": [
                                {
                                    "title": "Q1 2026 10-Q",
                                    "url": "https://investor.apple.com/q1-2026-10q.pdf",
                                    "document_type": "quarterly_filing",
                                }
                            ],
                        }
                    ],
                },
            )
        ]
        existing_urls = {
            "https://www.bing.com/search?q=apple+investor+relations",
        }
        spawned = build_spawned_financial_source_jobs(
            request,
            runs,
            "stealth",
            existing_urls,
        )
        spawned_urls = [job.url for job in spawned]
        self.assertIn("https://investor.apple.com/", spawned_urls)
        self.assertNotIn("https://investor.apple.com/q1-2026-10q.pdf", spawned_urls)
        self.assertEqual("financial_investor_relations", spawned[0].source_type)
        self.assertTrue(all(job.category == "financials" for job in spawned))

    def test_raw_source_refs_include_direct_documents_without_tinyfish_fanout(self) -> None:
        request = ReportRequest(
            company_name="Apple",
            focus_areas=["financials"],
            financial_source_urls=["https://example.com/direct-annual-report.pdf"],
        )
        runs = [
            TinyfishRunRecord(
                run_id="run_1",
                status="COMPLETED",
                goal="Discover sources",
                label="Financial source discovery",
                category="financials",
                url="https://www.bing.com/search?q=apple+investor+relations",
                source_type="financial_search_discovery",
                result={
                    "capture_mode": "search_discovery",
                    "sources": [
                        {
                            "page_title": "Apple Investor Relations",
                            "final_url": "https://investor.apple.com/",
                            "source_kind": "investor_relations_hub",
                            "linked_documents": [
                                {
                                    "title": "Q1 2026 10-Q",
                                    "url": "https://investor.apple.com/q1-2026-10q.pdf",
                                    "document_type": "quarterly_filing",
                                }
                            ],
                        }
                    ],
                },
            )
        ]
        refs = build_raw_financial_source_refs(request, runs)
        ref_urls = [item["url"] for item in refs]
        self.assertIn("https://example.com/direct-annual-report.pdf", ref_urls)
        self.assertIn("https://investor.apple.com/", ref_urls)
        self.assertIn("https://investor.apple.com/q1-2026-10q.pdf", ref_urls)
        linked_ref = next(
            item for item in refs if item["url"] == "https://investor.apple.com/q1-2026-10q.pdf"
        )
        self.assertEqual("tinyfish_linked_document", linked_ref["discovery_origin"])
        self.assertEqual("financial_document", linked_ref["source_type"])


class FinancialSubAgentTests(unittest.TestCase):
    def test_financial_domain_fans_out_into_specialists(self) -> None:
        agents = get_extraction_agents(DOMAIN_AGENTS["financials"])
        self.assertEqual(5, len(agents))
        self.assertEqual("financial_document_locator", agents[0].name)
        self.assertEqual("financial_statement_agent", agents[1].name)
        self.assertEqual("capital_structure_agent", agents[2].name)
        self.assertEqual("funding_guidance_agent", agents[3].name)
        self.assertEqual("financial_risk_agent", agents[4].name)

    def test_financial_specialist_prefers_matching_source_types(self) -> None:
        specialist = get_extraction_agents(DOMAIN_AGENTS["financials"])[1]
        evidence = [
            EvidenceItem(
                evidence_id="e1",
                label="IR page",
                category="financials",
                url="https://example.com/investors",
                retrieved_at="2026-03-28T00:00:00Z",
                content="{}",
                metadata={"tinyfish_source_type": "financial_investor_relations"},
            ),
            EvidenceItem(
                evidence_id="e2",
                label="Newsroom",
                category="news",
                url="https://example.com/news",
                retrieved_at="2026-03-28T00:00:00Z",
                content="{}",
                metadata={"tinyfish_source_type": "newsroom_page"},
            ),
        ]
        selected = select_evidence_for_extraction_agent(specialist, evidence)
        self.assertEqual(["e1"], [item.evidence_id for item in selected])


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


class TinyfishEvidenceNormalizationTests(unittest.TestCase):
    def test_normalize_tinyfish_runs_preserves_raw_capture_material(self) -> None:
        runs = [
            TinyfishRunRecord(
                run_id="run_1",
                status="COMPLETED",
                goal="Collect raw financial sources",
                label="Financial source discovery",
                category="financials",
                url="https://www.bing.com/search?q=apple+investor+relations",
                source_type="financial_search_discovery",
                result={
                    "capture_mode": "search_discovery",
                    "sources": [
                        {
                            "page_title": "Apple Investor Relations",
                            "final_url": "https://investor.apple.com/",
                            "source_kind": "investor_relations_hub",
                            "reporting_period": "",
                            "captured_text": "Apple reported quarterly revenue of $124.3 billion.",
                            "linked_documents": [
                                {
                                    "title": "Q1 2026 10-Q",
                                    "url": "https://investor.apple.com/q1-2026-10q.pdf",
                                    "document_type": "quarterly_filing",
                                }
                            ],
                            "notes": "Official source",
                        }
                    ],
                    "coverage_gaps": [],
                    "error": None,
                },
            )
        ]
        evidence = normalize_tinyfish_runs(runs)
        self.assertEqual(1, len(evidence))
        self.assertIn("Apple Investor Relations", evidence[0].content)
        self.assertIn("$124.3 billion", evidence[0].content)
        self.assertIn("Q1 2026 10-Q", evidence[0].content)


if __name__ == "__main__":
    unittest.main()
