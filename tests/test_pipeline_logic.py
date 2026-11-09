from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gus_reporter.agents import DOMAIN_AGENTS, get_extraction_agents
from gus_reporter.config import AppConfig
from gus_reporter.models import (
    EvidenceItem,
    ExtractedClaim,
    ReportRequest,
    TinyfishRunRecord,
    VerifiedClaim,
)
from gus_reporter.assembly import (
    compute_publish_decision,
)
from gus_reporter.discovery import (
    classify_legal_source,
    classify_news_source,
    classify_financial_source,
    build_raw_financial_source_refs,
    build_raw_legal_source_refs,
    build_raw_news_source_refs,
    build_spawned_financial_source_jobs,
    build_spawned_legal_source_jobs,
    build_spawned_news_source_jobs,
)
from gus_reporter.evidence import (
    _build_openai_user_content,
    normalize_tinyfish_runs,
    select_evidence_for_extraction_agent,
)
from gus_reporter.pipeline import (
    ReportPipeline,
)
from gus_reporter.planning import (
    seed_source_jobs,
)


class SeedSourceJobsTests(unittest.TestCase):
    def test_company_name_only_financial_request_seeds_search_discovery_jobs(self) -> None:
        request = ReportRequest(
            company_name="Apple",
            focus_areas=["financials"],
        )
        jobs = seed_source_jobs(request, "lite")
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
        jobs = seed_source_jobs(request, "lite")
        # 3 base (homepage, leadership, careers)
        # + 1 newsroom (news category)
        # + 2 financial IR candidates
        # + 6 legal (4 search + CourtListener + OpenCorporates)
        # + 4 news search seeds
        self.assertEqual(16, len(jobs))
        self.assertEqual("https://example.com", jobs[0].url)
        self.assertEqual("https://example.com/leadership", jobs[1].url)
        self.assertEqual("lite", jobs[0].browser_profile)
        self.assertEqual("company_homepage", jobs[0].source_type)
        self.assertEqual("financial_investor_candidate", jobs[4].source_type)
        self.assertEqual("lite", jobs[4].browser_profile)
        # Verify legal and news categories are present
        legal_jobs = [j for j in jobs if j.category == "legal"]
        news_jobs = [j for j in jobs if j.category == "news"]
        self.assertEqual(6, len(legal_jobs))
        self.assertEqual(5, len(news_jobs))  # 1 newsroom + 4 search seeds
        self.assertTrue(all(j.proxy_country_code == "" for j in jobs))

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
        jobs = seed_source_jobs(request, "lite")
        financial_jobs = [job for job in jobs if job.category == "financials"]
        financial_urls = [job.url for job in financial_jobs]
        self.assertIn("https://example.com/investors", financial_urls)
        self.assertIn(
            "https://www.sec.gov/edgar/browse/?CIK=EXMPL&owner=exclude",
            financial_urls,
        )
        self.assertTrue(all(job.enable_agent_memory for job in financial_jobs))
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
        jobs = seed_source_jobs(request, "lite")
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
            "lite",
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


class RawDocumentRoutingTests(unittest.TestCase):
    def test_raw_documents_reach_specialists_alongside_browse_evidence(self) -> None:
        specialist = get_extraction_agents(DOMAIN_AGENTS["financials"])[1]
        browse = EvidenceItem(
            evidence_id="tinyfish_financials_001",
            label="IR page",
            category="financials",
            url="https://example.com/investors",
            retrieved_at="2026-03-28T00:00:00Z",
            content="{}",
            metadata={"tinyfish_source_type": "financial_investor_relations"},
        )
        annual_report = EvidenceItem(
            evidence_id="direct_financials_001",
            label="Annual report",
            category="financials",
            url="https://example.com/ar.pdf",
            retrieved_at="2026-03-28T00:00:00Z",
            content="Revenue was $10.",
            metadata={"source_type": "financial_document"},
        )
        selected = select_evidence_for_extraction_agent(specialist, [annual_report, browse])
        self.assertEqual(
            ["direct_financials_001", "tinyfish_financials_001"],
            [item.evidence_id for item in selected],
        )


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
        self.assertEqual("tinyfish_financials_001", evidence[0].evidence_id)
        self.assertIn("Apple Investor Relations", evidence[0].content)
        self.assertIn("$124.3 billion", evidence[0].content)
        self.assertIn("Q1 2026 10-Q", evidence[0].content)

    def test_build_openai_user_content_uses_local_pdf_artifact_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            artifact_dir = run_dir / "raw_sources"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_dir / "001-annual-report.pdf"
            artifact_path.write_bytes(b"%PDF-1.4 local test")

            evidence = [
                EvidenceItem(
                    evidence_id="direct_financials_001",
                    label="Annual report",
                    category="financials",
                    url="https://example.com/report.pdf",
                    retrieved_at="2026-03-28T00:00:00Z",
                    content="Annual report artifact",
                    metadata={
                        "openai_input_file_url": "https://example.com/report.pdf",
                        "openai_input_file_path": "raw_sources/001-annual-report.pdf",
                        "openai_input_filename": "001-annual-report.pdf",
                    },
                )
            ]

            user_content = _build_openai_user_content(
                preamble="Test preamble",
                evidence=evidence,
                run_dir=run_dir,
            )

        file_items = [item for item in user_content if item["type"] == "input_file"]
        self.assertEqual(1, len(file_items))
        self.assertTrue(file_items[0]["file_data"].startswith("data:application/pdf;base64,"))


class LegalSubAgentTests(unittest.TestCase):
    def test_legal_domain_fans_out_into_5_specialists(self) -> None:
        agents = get_extraction_agents(DOMAIN_AGENTS["legal"])
        self.assertEqual(5, len(agents))
        self.assertEqual("legal_case_locator", agents[0].name)
        self.assertEqual("legal_regulatory_agent", agents[1].name)
        self.assertEqual("legal_sanctions_agent", agents[2].name)
        self.assertEqual("legal_corporate_filings_agent", agents[3].name)
        self.assertEqual("legal_risk_agent", agents[4].name)
        self.assertTrue(agents[4].use_all_evidence)

    def test_legal_specialist_prefers_matching_source_types(self) -> None:
        specialist = get_extraction_agents(DOMAIN_AGENTS["legal"])[0]
        evidence = [
            EvidenceItem(
                evidence_id="e1",
                label="Court record",
                category="legal",
                url="https://courtlistener.com/docket/123",
                retrieved_at="2026-03-28T00:00:00Z",
                content="{}",
                metadata={"tinyfish_source_type": "legal_court_records"},
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


class NewsSubAgentTests(unittest.TestCase):
    def test_news_domain_fans_out_into_4_specialists(self) -> None:
        agents = get_extraction_agents(DOMAIN_AGENTS["news"])
        self.assertEqual(4, len(agents))
        self.assertEqual("news_event_locator", agents[0].name)
        self.assertEqual("news_negative_signal_agent", agents[1].name)
        self.assertEqual("news_sentiment_agent", agents[2].name)
        self.assertEqual("news_coverage_gap_agent", agents[3].name)
        self.assertTrue(agents[3].use_all_evidence)

    def test_news_specialist_prefers_matching_source_types(self) -> None:
        specialist = get_extraction_agents(DOMAIN_AGENTS["news"])[0]
        evidence = [
            EvidenceItem(
                evidence_id="e1",
                label="Reuters article",
                category="news",
                url="https://reuters.com/article/123",
                retrieved_at="2026-03-28T00:00:00Z",
                content="{}",
                metadata={"tinyfish_source_type": "news_aggregator"},
            ),
            EvidenceItem(
                evidence_id="e2",
                label="IR page",
                category="financials",
                url="https://example.com/investors",
                retrieved_at="2026-03-28T00:00:00Z",
                content="{}",
                metadata={"tinyfish_source_type": "financial_investor_relations"},
            ),
        ]
        selected = select_evidence_for_extraction_agent(specialist, evidence)
        self.assertEqual(["e1"], [item.evidence_id for item in selected])


class LegalSourceSeedingTests(unittest.TestCase):
    def test_legal_focus_area_seeds_search_discovery_jobs(self) -> None:
        request = ReportRequest(
            company_name="Acme Corp",
            focus_areas=["legal"],
        )
        jobs = seed_source_jobs(request, "lite")
        self.assertTrue(len(jobs) >= 6)
        self.assertTrue(all(j.category == "legal" for j in jobs))
        search_jobs = [j for j in jobs if j.source_type == "legal_search_discovery"]
        self.assertEqual(4, len(search_jobs))
        court_jobs = [j for j in jobs if j.source_type == "legal_court_records"]
        self.assertEqual(1, len(court_jobs))
        self.assertIn("courtlistener.com", court_jobs[0].url)
        registry_jobs = [j for j in jobs if j.source_type == "legal_corporate_registry"]
        self.assertEqual(1, len(registry_jobs))
        self.assertIn("opencorporates.com", registry_jobs[0].url)

    def test_legal_source_urls_hint_creates_direct_jobs(self) -> None:
        request = ReportRequest(
            company_name="Acme Corp",
            focus_areas=["legal"],
            legal_source_urls=["https://example.com/case-filing.pdf"],
        )
        jobs = seed_source_jobs(request, "lite")
        direct_jobs = [j for j in jobs if j.source_type == "legal_document"]
        self.assertEqual(1, len(direct_jobs))
        self.assertEqual("https://example.com/case-filing.pdf", direct_jobs[0].url)

    def test_ticker_adds_sec_enforcement_search_for_legal(self) -> None:
        request = ReportRequest(
            company_name="Acme Corp",
            ticker="ACME",
            focus_areas=["legal"],
        )
        jobs = seed_source_jobs(request, "lite")
        sec_jobs = [j for j in jobs if "SEC enforcement" in j.label]
        self.assertTrue(len(sec_jobs) >= 1)


class NewsSourceSeedingTests(unittest.TestCase):
    def test_news_focus_area_seeds_aggregator_and_press_jobs(self) -> None:
        request = ReportRequest(
            company_name="Acme Corp",
            focus_areas=["news"],
        )
        jobs = seed_source_jobs(request, "lite")
        self.assertEqual(4, len(jobs))
        self.assertTrue(all(j.category == "news" for j in jobs))
        labels = [j.label for j in jobs]
        self.assertIn("Recent news search", labels)
        self.assertIn("Negative news search", labels)
        self.assertIn("Press release search", labels)
        self.assertIn("Industry coverage search", labels)

    def test_news_source_urls_hint_creates_direct_jobs(self) -> None:
        request = ReportRequest(
            company_name="Acme Corp",
            focus_areas=["news"],
            news_source_urls=["https://reuters.com/acme-article"],
        )
        jobs = seed_source_jobs(request, "lite")
        direct_jobs = [j for j in jobs if j.source_type == "news_document"]
        self.assertEqual(1, len(direct_jobs))
        self.assertEqual("https://reuters.com/acme-article", direct_jobs[0].url)


class LegalSpawnedJobTests(unittest.TestCase):
    def test_discovery_runs_spawn_child_legal_source_jobs(self) -> None:
        request = ReportRequest(
            company_name="Acme Corp",
            focus_areas=["legal"],
        )
        runs = [
            TinyfishRunRecord(
                run_id="run_1",
                status="COMPLETED",
                goal="Discover legal sources",
                label="Litigation search",
                category="legal",
                url="https://www.bing.com/search?q=acme+lawsuit",
                source_type="legal_search_discovery",
                result={
                    "capture_mode": "legal_search_discovery",
                    "sources": [
                        {
                            "page_title": "Acme Corp v. Rival Inc - Docket",
                            "final_url": "https://www.courtlistener.com/docket/999/acme-v-rival/",
                            "source_kind": "court_docket",
                            "linked_documents": [
                                {
                                    "title": "Complaint",
                                    "url": "https://storage.courtlistener.com/recap/complaint.pdf",
                                    "document_type": "complaint",
                                }
                            ],
                        }
                    ],
                },
            )
        ]
        existing_urls: set[str] = set()
        spawned = build_spawned_legal_source_jobs(
            request, runs, "lite", existing_urls,
        )
        spawned_urls = [j.url for j in spawned]
        self.assertIn(
            "https://www.courtlistener.com/docket/999/acme-v-rival/",
            spawned_urls,
        )
        self.assertIn(
            "https://storage.courtlistener.com/recap/complaint.pdf",
            spawned_urls,
        )
        self.assertTrue(all(j.category == "legal" for j in spawned))


class NewsSpawnedJobTests(unittest.TestCase):
    def test_discovery_runs_spawn_child_news_source_jobs(self) -> None:
        request = ReportRequest(
            company_name="Acme Corp",
            focus_areas=["news"],
        )
        runs = [
            TinyfishRunRecord(
                run_id="run_1",
                status="COMPLETED",
                goal="Discover news sources",
                label="Recent news search",
                category="news",
                url="https://www.bing.com/news/search?q=acme",
                source_type="news_search_discovery",
                result={
                    "capture_mode": "news_search_discovery",
                    "sources": [
                        {
                            "page_title": "Acme Corp announces layoffs",
                            "final_url": "https://reuters.com/acme-layoffs",
                            "source_kind": "news_article",
                            "linked_documents": [],
                        }
                    ],
                },
            )
        ]
        existing_urls: set[str] = set()
        spawned = build_spawned_news_source_jobs(
            request, runs, "lite", existing_urls,
        )
        spawned_urls = [j.url for j in spawned]
        self.assertIn("https://reuters.com/acme-layoffs", spawned_urls)
        self.assertTrue(all(j.category == "news" for j in spawned))


class LegalRawSourceRefTests(unittest.TestCase):
    def test_raw_legal_source_refs_include_discovered_documents(self) -> None:
        request = ReportRequest(
            company_name="Acme Corp",
            focus_areas=["legal"],
            legal_source_urls=["https://example.com/case-filing.pdf"],
        )
        runs = [
            TinyfishRunRecord(
                run_id="run_1",
                status="COMPLETED",
                goal="Discover legal sources",
                label="Litigation search",
                category="legal",
                url="https://www.bing.com/search?q=acme+lawsuit",
                source_type="legal_search_discovery",
                result={
                    "capture_mode": "legal_search_discovery",
                    "sources": [
                        {
                            "page_title": "Acme enforcement action",
                            "final_url": "https://sec.gov/litigation/acme",
                            "source_kind": "enforcement",
                            "linked_documents": [
                                {
                                    "title": "Order",
                                    "url": "https://sec.gov/litigation/acme-order.pdf",
                                    "document_type": "order",
                                }
                            ],
                        }
                    ],
                },
            )
        ]
        refs = build_raw_legal_source_refs(request, runs)
        ref_urls = [item["url"] for item in refs]
        self.assertIn("https://example.com/case-filing.pdf", ref_urls)
        self.assertIn("https://sec.gov/litigation/acme", ref_urls)
        self.assertIn("https://sec.gov/litigation/acme-order.pdf", ref_urls)


class NewsRawSourceRefTests(unittest.TestCase):
    def test_raw_news_source_refs_include_discovered_articles(self) -> None:
        request = ReportRequest(
            company_name="Acme Corp",
            focus_areas=["news"],
            news_source_urls=["https://reuters.com/acme-direct"],
        )
        runs = [
            TinyfishRunRecord(
                run_id="run_1",
                status="COMPLETED",
                goal="Discover news sources",
                label="Recent news search",
                category="news",
                url="https://www.bing.com/news/search?q=acme",
                source_type="news_search_discovery",
                result={
                    "capture_mode": "news_search_discovery",
                    "sources": [
                        {
                            "page_title": "Acme raises $100M",
                            "final_url": "https://techcrunch.com/acme-funding",
                            "source_kind": "news_article",
                            "linked_documents": [],
                        }
                    ],
                },
            )
        ]
        refs = build_raw_news_source_refs(request, runs)
        ref_urls = [item["url"] for item in refs]
        self.assertIn("https://reuters.com/acme-direct", ref_urls)
        self.assertIn("https://techcrunch.com/acme-funding", ref_urls)


class DiscoveredSourceTypeHeuristicTests(unittest.TestCase):
    def test_financial_domain_heuristics_handle_schemeless_and_ports(self) -> None:
        self.assertEqual(
            "financial_sec_filings",
            classify_financial_source(
                source_kind="filing_index",
                document_type="",
                url="www.sec.gov:443/Archives/edgar/data/1234",
            ),
        )

    def test_legal_domain_heuristics_handle_schemeless_and_ports(self) -> None:
        self.assertEqual(
            "legal_court_records",
            classify_legal_source(
                source_kind="",
                document_type="",
                url="courtlistener.com:443/docket/123/acme/",
            ),
        )

    def test_news_domain_heuristics_handle_ports(self) -> None:
        self.assertEqual(
            "news_aggregator",
            classify_news_source(
                source_kind="",
                url="https://www.reuters.com:443/world/example",
            ),
        )


class PipelineParallelizationTests(unittest.TestCase):
    def _build_pipeline(self, events: list[dict[str, object]]) -> ReportPipeline:
        config = AppConfig(
            openai_api_key="test-openai",
            tinyfish_api_key="test-tinyfish",
            openai_base_url="https://api.openai.com/v1",
            openai_model="gpt-5.4-mini",
            tinyfish_base_url="https://agent.tinyfish.ai/v1",
            report_output_dir=Path("/tmp/test-runs"),
            tinyfish_default_browser_profile="lite",
            tinyfish_poll_interval_seconds=1,
            tinyfish_poll_timeout_seconds=5,
            tinyfish_request_timeout_seconds=5,
            raw_source_timeout_seconds=5,
            openai_request_timeout_seconds=5,
            openai_request_max_retries=1,
            openai_request_retry_backoff_seconds=0.1,
            openai_extraction_max_workers=10,
            openai_verification_max_workers=20,
            tinyfish_batch_size=10,
            tinyfish_max_total_jobs=10,
            tinyfish_max_acquisition_waves=2,
            tinyfish_request_max_retries=0,
            tinyfish_request_retry_backoff_seconds=0.1,
        )
        return ReportPipeline(config=config, progress_callback=events.append)

    def test_parallel_extraction_processes_all_specialists_when_one_fails(self) -> None:
        events: list[dict[str, object]] = []
        pipeline = self._build_pipeline(events)
        request = ReportRequest(company_name="Acme Corp", focus_areas=["financials"])
        evidence = [
            EvidenceItem(
                evidence_id="e1",
                label="Annual report",
                category="financials",
                url="https://example.com/report.pdf",
                retrieved_at="2026-03-28T00:00:00Z",
                content="Revenue was $100.",
                metadata={"tinyfish_source_type": "financial_document"},
            )
        ]

        def fake_structured_response(*, schema_name: str, **_: object) -> dict[str, object]:
            if "capital_structure_agent" in schema_name:
                raise RuntimeError("rate limit")
            return {
                "claims": [
                    {
                        "section": "financials",
                        "statement": f"Claim from {schema_name}",
                        "importance": "medium",
                        "confidence": 0.8,
                        "as_of": "2026-03-28",
                        "source_quote": "Revenue was $100.",
                    }
                ]
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(pipeline.openai, "structured_response", side_effect=fake_structured_response):
                claims = pipeline.extractor.extract(request, evidence, Path(temp_dir))

        self.assertEqual(4, len(claims))
        specialist_updates = [
            event
            for event in events
            if event.get("event") == "specialist_update"
            and event.get("stage") == "extraction"
            and event.get("status") in {"completed", "failed"}
        ]
        self.assertEqual(5, len(specialist_updates))
        self.assertEqual({1, 2, 3, 4, 5}, {event["current"] for event in specialist_updates})
        failed = [
            event
            for event in specialist_updates
            if event.get("status") == "failed"
            and event.get("specialist") == "capital_structure_agent"
        ]
        self.assertEqual(1, len(failed))

    def test_parallel_verification_returns_result_for_each_claim(self) -> None:
        events: list[dict[str, object]] = []
        pipeline = self._build_pipeline(events)
        claims = [
            ExtractedClaim(
                claim_id="c1",
                section="financials",
                statement="Claim 1",
                importance="high",
                confidence=0.9,
                as_of="2026-03-28",
                source_quote="Quote 1",
                evidence_ids=["e1"],
                raw_source_label="specialist",
            ),
            ExtractedClaim(
                claim_id="c2",
                section="financials",
                statement="Claim 2",
                importance="high",
                confidence=0.9,
                as_of="2026-03-28",
                source_quote="Quote 2",
                evidence_ids=["e1"],
                raw_source_label="specialist",
            ),
            ExtractedClaim(
                claim_id="c3",
                section="financials",
                statement="Claim 3",
                importance="high",
                confidence=0.9,
                as_of="2026-03-28",
                source_quote="Quote 3",
                evidence_ids=["e1"],
                raw_source_label="specialist",
            ),
        ]
        evidence = [
            EvidenceItem(
                evidence_id="e1",
                label="Annual report",
                category="financials",
                url="https://example.com/report.pdf",
                retrieved_at="2026-03-28T00:00:00Z",
                content="Revenue was $100.",
                metadata={"tinyfish_source_type": "financial_document"},
            )
        ]

        def fake_verification_response(*, user_content: list[dict[str, object]], **_: object) -> dict[str, object]:
            text = str(user_content[0].get("text", ""))
            if "Claim ID: c2" in text:
                raise RuntimeError("429")
            return {
                "status": "supported",
                "confidence": 0.92,
                "rationale": "Matches source.",
                "supporting_evidence_ids": ["e1"],
                "blocking_issues": [],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(pipeline.openai, "structured_response", side_effect=fake_verification_response):
                verified = pipeline.verifier.verify(claims, evidence, Path(temp_dir))

        self.assertEqual(3, len(verified))
        by_id = {claim.claim_id: claim for claim in verified}
        self.assertEqual("supported", by_id["c1"].status)
        self.assertEqual("insufficient_evidence", by_id["c2"].status)
        self.assertEqual("supported", by_id["c3"].status)
        updates = [
            event
            for event in events
            if event.get("event") == "verification_update" and event.get("stage") == "verification"
        ]
        self.assertEqual(3, len(updates))
        self.assertEqual({1, 2, 3}, {event["current"] for event in updates})

    def test_verified_claims_keep_extraction_order_under_concurrency(self) -> None:
        import time

        pipeline = self._build_pipeline([])
        claims = [
            ExtractedClaim(
                claim_id=f"c{index}",
                section="financials",
                statement=f"Claim {index}",
                importance="low",
                confidence=0.5,
                as_of="",
                source_quote="",
                evidence_ids=[],
                raw_source_label="specialist",
            )
            for index in range(8)
        ]

        def slow_first(*, user_content: list[dict[str, object]], **_: object) -> dict[str, object]:
            text = str(user_content[0].get("text", ""))
            index = int(text.split("Claim ID: c", 1)[1].split("\n", 1)[0])
            time.sleep(0.002 * (8 - index))
            return {
                "status": "supported",
                "confidence": 1.0,
                "rationale": "",
                "supporting_evidence_ids": [],
                "blocking_issues": [],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(pipeline.openai, "structured_response", side_effect=slow_first):
                verified = pipeline.verifier.verify(claims, [], Path(temp_dir))
        self.assertEqual([c.claim_id for c in claims], [c.claim_id for c in verified])


if __name__ == "__main__":
    unittest.main()
