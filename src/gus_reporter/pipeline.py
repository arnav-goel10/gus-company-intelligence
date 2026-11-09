from __future__ import annotations

import base64
import json
import urllib.parse
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from gus_reporter.agents import (
    DomainAgent,
    ExtractionAgent,
    get_active_agents,
    get_extraction_agents,
)
from gus_reporter.config import AppConfig
from gus_reporter.models import (
    EvidenceItem,
    ExtractedClaim,
    FinalReport,
    ReportRequest,
    ReportSection,
    RiskMatrixEntry,
    SourceJob,
    TinyfishRunRecord,
    VerifiedClaim,
    utc_now_iso,
)
from gus_reporter.openai_client import OpenAIClient
from gus_reporter.prompts import (
    EXECUTIVE_AGGREGATION_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT,
    REPORT_SYNTHESIS_SYSTEM_PROMPT,
    SECTION_DESCRIPTIONS,
    VERIFICATION_SYSTEM_PROMPT,
    build_financial_tinyfish_goal,
    build_legal_tinyfish_goal,
    build_news_tinyfish_goal,
)
from gus_reporter.raw_source_client import RawSourceClient
from gus_reporter.schemas import (
    executive_report_schema,
    extraction_schema,
    financial_section_summary_schema,
    legal_section_summary_schema,
    news_section_summary_schema,
    section_summary_schema,
    verification_schema,
)
from gus_reporter.storage import RunStorage
from gus_reporter.tinyfish_client import TinyfishClient

MAX_TEXT_EVIDENCE_CHARS = 12000
MAX_FILE_INPUTS_PER_REQUEST = 4


_DEEP_ACQUISITION_CATEGORIES = {"financials", "legal", "news"}


def _job_defaults(
    request: ReportRequest,
    default_browser_profile: str,
    *,
    source_type: str,
    category: str,
) -> dict[str, Any]:
    is_deep = category in _DEEP_ACQUISITION_CATEGORIES
    browser_profile = "lite" if is_deep else default_browser_profile
    return {
        "source_type": source_type,
        "browser_profile": browser_profile,
        "preferred_run_mode": "async",
        "use_vault": request.tinyfish_use_vault,
        "credential_item_ids": list(request.tinyfish_credential_item_ids),
        "enable_agent_memory": request.tinyfish_enable_agent_memory or is_deep,
    }


def build_financial_goal(
    request: ReportRequest,
    *,
    source_type: str,
    label: str,
    url: str,
) -> str:
    source_type_map = {
        "financial_investor_relations": "investor_relations_hub",
        "financial_investor_candidate": "investor_relations_candidate",
        "financial_document": "financial_document",
        "financial_sec_filings": "official_filing_index",
        "financial_search_discovery": "search_discovery",
    }
    return build_financial_tinyfish_goal(
        company_name=request.company_name,
        source_label=label,
        source_type=source_type_map.get(source_type, "financial_document"),
        ticker=request.ticker,
    )


def build_legal_goal(
    request: ReportRequest,
    *,
    source_type: str,
    label: str,
    url: str,
) -> str:
    source_type_map = {
        "legal_court_records": "court_docket_search",
        "legal_regulatory_filings": "regulatory_enforcement_search",
        "legal_enforcement": "regulatory_enforcement_search",
        "legal_sanctions": "sanctions_screening",
        "legal_corporate_registry": "corporate_registry_lookup",
        "legal_search_discovery": "legal_search_discovery",
        "legal_document": "legal_search_discovery",
    }
    return build_legal_tinyfish_goal(
        company_name=request.company_name,
        source_label=label,
        source_type=source_type_map.get(source_type, "legal_search_discovery"),
        ticker=request.ticker,
        jurisdiction=request.primary_jurisdiction,
    )


def build_news_goal(
    request: ReportRequest,
    *,
    source_type: str,
    label: str,
    url: str,
) -> str:
    source_type_map = {
        "news_aggregator": "news_aggregator_search",
        "news_press_release": "press_release_search",
        "news_industry_publication": "industry_publication_search",
        "news_search_discovery": "news_search_discovery",
        "news_document": "news_search_discovery",
    }
    return build_news_tinyfish_goal(
        company_name=request.company_name,
        source_label=label,
        source_type=source_type_map.get(source_type, "news_search_discovery"),
    )


def _build_search_url(query: str) -> str:
    return f"https://www.bing.com/search?q={urllib.parse.quote_plus(query)}"


def _normalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            parsed.query,
            "",
        )
    )

def seed_source_jobs(request: ReportRequest, default_browser_profile: str) -> list[SourceJob]:
    if request.source_jobs:
        return request.source_jobs
    jobs: list[SourceJob] = []
    website = request.company_website.rstrip("/")
    if website:
        jobs.extend(
            [
                SourceJob(
                    label="Official website",
                    category="company_overview",
                    url=website,
                    goal="Extract the company overview, products, geographies, scale signals, and links to investor, leadership, careers, and newsroom pages. Respond in structured JSON if possible.",
                    **_job_defaults(
                        request,
                        default_browser_profile,
                        source_type="company_homepage",
                        category="company_overview",
                    ),
                ),
                SourceJob(
                    label="Leadership page",
                    category="leadership",
                    url=f"{website}/leadership",
                    goal="Extract executives, founders, board members, and recent management changes. Respond in structured JSON if possible.",
                    **_job_defaults(
                        request,
                        default_browser_profile,
                        source_type="leadership_page",
                        category="leadership",
                    ),
                ),
                SourceJob(
                    label="Careers page",
                    category="employees",
                    url=f"{website}/careers",
                    goal="Extract hiring trends, team footprint, open roles, and location signals. Respond in structured JSON if possible.",
                    **_job_defaults(
                        request,
                        default_browser_profile,
                        source_type="careers_page",
                        category="employees",
                    ),
                ),
                SourceJob(
                    label="Newsroom",
                    category="news",
                    url=f"{website}/news",
                    goal="Extract recent company announcements, launches, expansions, funding, layoffs, investigations, or leadership changes. Respond in structured JSON if possible.",
                    **_job_defaults(
                        request,
                        default_browser_profile,
                        source_type="newsroom_page",
                        category="news",
                    ),
                ),
            ]
        )
    if "financials" in request.focus_areas:
        if request.investor_relations_url:
            jobs.append(
                SourceJob(
                    label="Investor relations",
                    category="financials",
                    url=request.investor_relations_url,
                    goal=build_financial_goal(
                        request,
                        source_type="financial_investor_relations",
                        label="Investor relations",
                        url=request.investor_relations_url,
                    ),
                    **_job_defaults(
                        request,
                        default_browser_profile,
                        source_type="financial_investor_relations",
                        category="financials",
                    ),
                )
            )
        elif website:
            jobs.extend(
                [
                    SourceJob(
                        label="Investor relations candidate",
                        category="financials",
                        url=f"{website}/investor-relations",
                        goal=build_financial_goal(
                            request,
                            source_type="financial_investor_candidate",
                            label="Investor relations candidate",
                            url=f"{website}/investor-relations",
                        ),
                        **_job_defaults(
                            request,
                            default_browser_profile,
                            source_type="financial_investor_candidate",
                            category="financials",
                        ),
                    ),
                    SourceJob(
                        label="Investors candidate",
                        category="financials",
                        url=f"{website}/investors",
                        goal=build_financial_goal(
                            request,
                            source_type="financial_investor_candidate",
                            label="Investors candidate",
                            url=f"{website}/investors",
                        ),
                        **_job_defaults(
                            request,
                            default_browser_profile,
                            source_type="financial_investor_candidate",
                            category="financials",
                        ),
                    ),
                ]
            )
        if request.ticker:
            jobs.append(
                SourceJob(
                    label="SEC filings",
                    category="financials",
                    url=f"https://www.sec.gov/edgar/browse/?CIK={request.ticker}&owner=exclude",
                    goal=build_financial_goal(
                        request,
                        source_type="financial_sec_filings",
                        label="SEC filings",
                        url=f"https://www.sec.gov/edgar/browse/?CIK={request.ticker}&owner=exclude",
                    ),
                    **_job_defaults(
                        request,
                        default_browser_profile,
                        source_type="financial_sec_filings",
                        category="financials",
                    ),
                )
            )
        if not any(
            [
                request.company_website,
                request.investor_relations_url,
                request.financial_source_urls,
                request.ticker,
            ]
        ):
            discovery_queries = [
                (
                    "Financial source discovery",
                    f"\"{request.company_name}\" investor relations annual report quarterly results",
                ),
                (
                    "Annual report discovery",
                    f"\"{request.company_name}\" annual report pdf financial statements",
                ),
                (
                    "Earnings and filings discovery",
                    f"\"{request.company_name}\" earnings release quarterly results filing",
                ),
            ]
            for label, query in discovery_queries:
                search_url = _build_search_url(query)
                jobs.append(
                    SourceJob(
                        label=label,
                        category="financials",
                        url=search_url,
                        goal=build_financial_goal(
                            request,
                            source_type="financial_search_discovery",
                            label=label,
                            url=search_url,
                        ),
                        **_job_defaults(
                            request,
                            default_browser_profile,
                            source_type="financial_search_discovery",
                            category="financials",
                        ),
                    )
                )

    if "legal" in request.focus_areas:
        legal_search_queries = [
            (
                "Litigation search",
                f"\"{request.company_name}\" lawsuit litigation court case filed",
            ),
            (
                "SEC enforcement search",
                f"\"{request.company_name}\" SEC enforcement action investigation penalty",
            ),
            (
                "Regulatory action search",
                f"\"{request.company_name}\" FTC DOJ EPA regulatory fine penalty settlement",
            ),
            (
                "Sanctions screening",
                f"\"{request.company_name}\" OFAC sanctions SDN debarment",
            ),
        ]
        for label, query in legal_search_queries:
            search_url = _build_search_url(query)
            jobs.append(
                SourceJob(
                    label=label,
                    category="legal",
                    url=search_url,
                    goal=build_legal_goal(
                        request,
                        source_type="legal_search_discovery",
                        label=label,
                        url=search_url,
                    ),
                    **_job_defaults(
                        request,
                        default_browser_profile,
                        source_type="legal_search_discovery",
                        category="legal",
                    ),
                )
            )
        court_listener_url = (
            f"https://www.courtlistener.com/?q=%22{urllib.parse.quote(request.company_name)}%22&type=o"
        )
        jobs.append(
            SourceJob(
                label="CourtListener search",
                category="legal",
                url=court_listener_url,
                goal=build_legal_goal(
                    request,
                    source_type="legal_court_records",
                    label="CourtListener search",
                    url=court_listener_url,
                ),
                **_job_defaults(
                    request,
                    default_browser_profile,
                    source_type="legal_court_records",
                    category="legal",
                ),
            )
        )
        opencorporates_url = (
            f"https://opencorporates.com/companies?q={urllib.parse.quote(request.company_name)}"
        )
        jobs.append(
            SourceJob(
                label="OpenCorporates lookup",
                category="legal",
                url=opencorporates_url,
                goal=build_legal_goal(
                    request,
                    source_type="legal_corporate_registry",
                    label="OpenCorporates lookup",
                    url=opencorporates_url,
                ),
                **_job_defaults(
                    request,
                    default_browser_profile,
                    source_type="legal_corporate_registry",
                    category="legal",
                ),
            )
        )
        if request.ticker:
            sec_enforcement_url = _build_search_url(
                f"site:sec.gov \"{request.company_name}\" OR \"{request.ticker}\" "
                "enforcement litigation administrative proceedings"
            )
            jobs.append(
                SourceJob(
                    label="SEC enforcement filings",
                    category="legal",
                    url=sec_enforcement_url,
                    goal=build_legal_goal(
                        request,
                        source_type="legal_search_discovery",
                        label="SEC enforcement filings",
                        url=sec_enforcement_url,
                    ),
                    **_job_defaults(
                        request,
                        default_browser_profile,
                        source_type="legal_search_discovery",
                        category="legal",
                    ),
                )
            )
        for index, url in enumerate(request.legal_source_urls, start=1):
            if url.strip():
                jobs.append(
                    SourceJob(
                        label=f"Legal source {index}",
                        category="legal",
                        url=url.strip(),
                        goal=build_legal_goal(
                            request,
                            source_type="legal_document",
                            label=f"Legal source {index}",
                            url=url.strip(),
                        ),
                        **_job_defaults(
                            request,
                            default_browser_profile,
                            source_type="legal_document",
                            category="legal",
                        ),
                    )
                )

    if "news" in request.focus_areas:
        news_search_url = (
            f"https://www.bing.com/news/search?q={urllib.parse.quote_plus(request.company_name)}"
        )
        jobs.append(
            SourceJob(
                label="Recent news search",
                category="news",
                url=news_search_url,
                goal=build_news_goal(
                    request,
                    source_type="news_search_discovery",
                    label="Recent news search",
                    url=news_search_url,
                ),
                **_job_defaults(
                    request,
                    default_browser_profile,
                    source_type="news_search_discovery",
                    category="news",
                ),
            )
        )
        negative_news_url = _build_search_url(
            f"\"{request.company_name}\" lawsuit layoff investigation scandal recall controversy"
        )
        jobs.append(
            SourceJob(
                label="Negative news search",
                category="news",
                url=negative_news_url,
                goal=build_news_goal(
                    request,
                    source_type="news_search_discovery",
                    label="Negative news search",
                    url=negative_news_url,
                ),
                **_job_defaults(
                    request,
                    default_browser_profile,
                    source_type="news_search_discovery",
                    category="news",
                ),
            )
        )
        press_release_url = _build_search_url(
            f"\"{request.company_name}\" press release "
            "site:prnewswire.com OR site:businesswire.com OR site:globenewswire.com"
        )
        jobs.append(
            SourceJob(
                label="Press release search",
                category="news",
                url=press_release_url,
                goal=build_news_goal(
                    request,
                    source_type="news_press_release",
                    label="Press release search",
                    url=press_release_url,
                ),
                **_job_defaults(
                    request,
                    default_browser_profile,
                    source_type="news_press_release",
                    category="news",
                ),
            )
        )
        industry_url = _build_search_url(
            f"\"{request.company_name}\" analysis report review coverage"
        )
        jobs.append(
            SourceJob(
                label="Industry coverage search",
                category="news",
                url=industry_url,
                goal=build_news_goal(
                    request,
                    source_type="news_search_discovery",
                    label="Industry coverage search",
                    url=industry_url,
                ),
                **_job_defaults(
                    request,
                    default_browser_profile,
                    source_type="news_search_discovery",
                    category="news",
                ),
            )
        )
        for index, url in enumerate(request.news_source_urls, start=1):
            if url.strip():
                jobs.append(
                    SourceJob(
                        label=f"News source {index}",
                        category="news",
                        url=url.strip(),
                        goal=build_news_goal(
                            request,
                            source_type="news_document",
                            label=f"News source {index}",
                            url=url.strip(),
                        ),
                        **_job_defaults(
                            request,
                            default_browser_profile,
                            source_type="news_document",
                            category="news",
                        ),
                    )
                )

    return jobs


def _extract_hostname(url: str) -> str:
    stripped = url.strip()
    candidate = stripped if "://" in stripped else f"https://{stripped.lstrip('/')}"
    return (urllib.parse.urlparse(candidate).hostname or "").lower()


def _domain_matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def _guess_discovered_source_type(
    *,
    source_kind: str,
    document_type: str,
    url: str,
) -> str:
    source_kind = source_kind.lower()
    document_type = document_type.lower()
    url_lower = url.lower()
    hostname = _extract_hostname(url_lower)

    if any(marker in document_type for marker in {"annual", "quarter", "earnings", "presentation", "filing"}):
        return "financial_document"
    if any(url_lower.endswith(extension) for extension in {".pdf", ".htm", ".html", ".txt", ".xml", ".json", ".csv", ".xls", ".xlsx"}):
        return "financial_document"
    if "investor" in source_kind:
        return "financial_investor_relations"
    if any(marker in source_kind for marker in {"filing", "sec", "edgar", "regulator"}):
        return "financial_sec_filings"
    if any(marker in url_lower for marker in {"investor", "investors"}):
        return "financial_investor_relations"
    if _domain_matches(hostname, "sec.gov") or "edgar" in url_lower:
        return "financial_sec_filings"

    if "annual-report" in url_lower or "quarterly-report" in url_lower or "10-k" in url_lower or "10-q" in url_lower:
        return "financial_document"

    return "financial_document"


def build_spawned_financial_source_jobs(
    request: ReportRequest,
    runs: list[TinyfishRunRecord],
    default_browser_profile: str,
    existing_urls: set[str],
) -> list[SourceJob]:
    spawned: list[SourceJob] = []

    def maybe_add_job(
        *,
        label: str,
        url: str,
        source_type: str,
    ) -> None:
        normalized = _normalize_url(url)
        if not normalized or normalized in existing_urls:
            return
        existing_urls.add(normalized)
        spawned.append(
            SourceJob(
                label=label,
                category="financials",
                url=url,
                goal=build_financial_goal(
                    request,
                    source_type=source_type,
                    label=label,
                    url=url,
                ),
                **_job_defaults(
                    request,
                    default_browser_profile,
                    source_type=source_type,
                    category="financials",
                ),
            )
        )

    for run in runs:
        if run.category != "financials" or not isinstance(run.result, dict):
            continue
        sources = run.result.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            final_url = str(source.get("final_url", "")).strip()
            page_title = str(source.get("page_title", "")).strip()
            source_kind = str(source.get("source_kind", "")).strip()
            if final_url:
                source_type = _guess_discovered_source_type(
                    source_kind=source_kind,
                    document_type="",
                    url=final_url,
                )
                if source_type in {"financial_investor_relations", "financial_sec_filings"}:
                    maybe_add_job(
                        label=page_title or f"Discovered {source_kind or 'financial source'}",
                        url=final_url,
                        source_type=source_type,
                    )

            linked_documents = source.get("linked_documents")
            if not isinstance(linked_documents, list):
                continue

    return spawned


def _guess_discovered_legal_source_type(
    *,
    source_kind: str,
    document_type: str,
    url: str,
) -> str:
    source_kind = source_kind.lower()
    document_type = document_type.lower()
    url_lower = url.lower()
    hostname = _extract_hostname(url_lower)

    if any(marker in source_kind for marker in {"court", "docket", "pacer", "case", "litigation"}):
        return "legal_court_records"
    if any(marker in source_kind for marker in {"enforcement", "penalty", "fine", "settlement", "consent"}):
        return "legal_enforcement"
    if any(marker in source_kind for marker in {"sanction", "ofac", "sdn", "debarment"}):
        return "legal_sanctions"
    if any(marker in source_kind for marker in {"registry", "corporat", "secretary", "incorporation"}):
        return "legal_corporate_registry"

    if any(
        _domain_matches(hostname, marker)
        for marker in {"courtlistener.com", "pacer.gov", "uscourts.gov"}
    ):
        return "legal_court_records"
    if _domain_matches(hostname, "sec.gov") and "litigation" in url_lower:
        return "legal_enforcement"
    if any(_domain_matches(hostname, marker) for marker in {"ftc.gov", "doj.gov"}) or "enforcement" in url_lower:
        return "legal_enforcement"
    if (
        _domain_matches(hostname, "treasury.gov") and ("ofac" in url_lower or "sdn" in url_lower)
    ) or "sanctions" in url_lower:
        return "legal_sanctions"
    if _domain_matches(hostname, "opencorporates.com") or "sos." in hostname or "secretary" in url_lower:
        return "legal_corporate_registry"

    if any(marker in document_type for marker in {"complaint", "order", "judgment", "settlement", "filing"}):
        return "legal_document"
    return "legal_document"


def _guess_discovered_news_source_type(
    *,
    source_kind: str,
    url: str,
) -> str:
    source_kind = source_kind.lower()
    url_lower = url.lower()
    hostname = _extract_hostname(url_lower)

    if any(
        _domain_matches(hostname, marker)
        for marker in {"prnewswire.com", "businesswire.com", "globenewswire.com"}
    ):
        return "news_press_release"
    if any(marker in source_kind for marker in {"press_release", "press release", "wire"}):
        return "news_press_release"

    aggregator_domains = {
        "reuters.com", "bloomberg.com", "wsj.com", "nytimes.com", "ft.com",
        "cnbc.com", "bbc.com", "apnews.com", "news.yahoo.com", "news.google.com"
    }
    if any(_domain_matches(hostname, domain) for domain in aggregator_domains):
        return "news_aggregator"

    industry_domains = {
        "techcrunch.com", "theinformation.com", "semafor.com", "arstechnica.com",
        "theverge.com", "wired.com", "forbes.com", "fortune.com", "inc.com"
    }
    if any(_domain_matches(hostname, domain) for domain in industry_domains):
        return "news_industry_publication"

    if any(marker in source_kind for marker in {"news", "article", "report", "coverage"}):
        return "news_aggregator"

    if "news" in url_lower or "article" in url_lower or "blog" in url_lower:
        return "news_article"

    return "news_article"


def build_spawned_legal_source_jobs(
    request: ReportRequest,
    runs: list[TinyfishRunRecord],
    default_browser_profile: str,
    existing_urls: set[str],
) -> list[SourceJob]:
    spawned: list[SourceJob] = []

    def maybe_add_job(
        *,
        label: str,
        url: str,
        source_type: str,
    ) -> None:
        normalized = _normalize_url(url)
        if not normalized or normalized in existing_urls:
            return
        existing_urls.add(normalized)
        spawned.append(
            SourceJob(
                label=label,
                category="legal",
                url=url,
                goal=build_legal_goal(
                    request,
                    source_type=source_type,
                    label=label,
                    url=url,
                ),
                **_job_defaults(
                    request,
                    default_browser_profile,
                    source_type=source_type,
                    category="legal",
                ),
            )
        )

    for run in runs:
        if run.category != "legal" or not isinstance(run.result, dict):
            continue
        sources = run.result.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            final_url = str(source.get("final_url", "")).strip()
            page_title = str(source.get("page_title", "")).strip()
            source_kind = str(source.get("source_kind", "")).strip()
            document_type = ""
            if final_url:
                source_type = _guess_discovered_legal_source_type(
                    source_kind=source_kind,
                    document_type=document_type,
                    url=final_url,
                )
                if source_type in {"legal_court_records", "legal_enforcement", "legal_sanctions"}:
                    maybe_add_job(
                        label=page_title or f"Discovered {source_kind or 'legal source'}",
                        url=final_url,
                        source_type=source_type,
                    )

            linked_documents = source.get("linked_documents")
            if not isinstance(linked_documents, list):
                continue
            for item in linked_documents:
                if not isinstance(item, dict):
                    continue
                doc_url = str(item.get("url", "")).strip()
                doc_title = str(item.get("title", "")).strip()
                doc_type = str(item.get("document_type", "")).strip()
                if doc_url:
                    maybe_add_job(
                        label=doc_title or page_title or "Linked legal document",
                        url=doc_url,
                        source_type=_guess_discovered_legal_source_type(
                            source_kind=source_kind,
                            document_type=doc_type,
                            url=doc_url,
                        ),
                    )

    return spawned


def build_spawned_news_source_jobs(
    request: ReportRequest,
    runs: list[TinyfishRunRecord],
    default_browser_profile: str,
    existing_urls: set[str],
) -> list[SourceJob]:
    spawned: list[SourceJob] = []

    def maybe_add_job(
        *,
        label: str,
        url: str,
        source_type: str,
    ) -> None:
        normalized = _normalize_url(url)
        if not normalized or normalized in existing_urls:
            return
        existing_urls.add(normalized)
        spawned.append(
            SourceJob(
                label=label,
                category="news",
                url=url,
                goal=build_news_goal(
                    request,
                    source_type=source_type,
                    label=label,
                    url=url,
                ),
                **_job_defaults(
                    request,
                    default_browser_profile,
                    source_type=source_type,
                    category="news",
                ),
            )
        )

    for run in runs:
        if run.category != "news" or not isinstance(run.result, dict):
            continue
        sources = run.result.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            final_url = str(source.get("final_url", "")).strip()
            page_title = str(source.get("page_title", "")).strip()
            source_kind = str(source.get("source_kind", "")).strip()
            if final_url:
                source_type = _guess_discovered_news_source_type(
                    source_kind=source_kind,
                    url=final_url,
                )
                maybe_add_job(
                    label=page_title or f"Discovered {source_kind or 'news source'}",
                    url=final_url,
                    source_type=source_type,
                )

            linked_documents = source.get("linked_documents")
            if not isinstance(linked_documents, list):
                continue
            for item in linked_documents:
                if not isinstance(item, dict):
                    continue
                doc_url = str(item.get("url", "")).strip()
                doc_title = str(item.get("title", "")).strip()
                if doc_url:
                    maybe_add_job(
                        label=doc_title or page_title or "Linked news article",
                        url=doc_url,
                        source_type=_guess_discovered_news_source_type(
                            source_kind=source_kind,
                            url=doc_url,
                        ),
                    )

    return spawned


def build_raw_financial_source_refs(
    request: ReportRequest,
    runs: list[TinyfishRunRecord],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    def maybe_add(
        *,
        label: str,
        url: str,
        source_type: str,
        source_kind: str = "",
        reporting_period: str = "",
        notes: str = "",
        discovery_origin: str = "",
        discovery_run_id: str = "",
    ) -> None:
        normalized = _normalize_url(url)
        if not normalized or normalized in seen_urls:
            return
        seen_urls.add(normalized)
        refs.append(
            {
                "label": label,
                "url": url,
                "source_type": source_type,
                "source_kind": source_kind,
                "reporting_period": reporting_period,
                "notes": notes,
                "discovery_origin": discovery_origin,
                "discovery_run_id": discovery_run_id,
            }
        )

    if request.investor_relations_url:
        maybe_add(
            label="Investor relations",
            url=request.investor_relations_url,
            source_type="financial_investor_relations",
            discovery_origin="request_hint",
        )
    for index, url in enumerate(request.financial_source_urls, start=1):
        maybe_add(
            label=f"Financial source {index}",
            url=url,
            source_type="financial_document",
            discovery_origin="request_hint",
        )

    for run in runs:
        if run.category != "financials":
            continue
        if not isinstance(run.result, dict):
            if run.source_type in {
                "financial_document",
                "financial_investor_relations",
                "financial_sec_filings",
            }:
                maybe_add(
                    label=run.label,
                    url=run.url,
                    source_type=run.source_type,
                    discovery_origin="tinyfish_seeded_url",
                    discovery_run_id=run.run_id,
                )
            continue

        sources = run.result.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            page_title = str(source.get("page_title", "")).strip()
            final_url = str(source.get("final_url", "")).strip()
            source_kind = str(source.get("source_kind", "")).strip()
            reporting_period = str(source.get("reporting_period", "")).strip()
            notes = str(source.get("notes", "")).strip()
            if final_url:
                maybe_add(
                    label=page_title or run.label,
                    url=final_url,
                    source_type=_guess_discovered_source_type(
                        source_kind=source_kind,
                        document_type="",
                        url=final_url,
                    ),
                    source_kind=source_kind,
                    reporting_period=reporting_period,
                    notes=notes,
                    discovery_origin="tinyfish_discovery",
                    discovery_run_id=run.run_id,
                )

            linked_documents = source.get("linked_documents")
            if not isinstance(linked_documents, list):
                continue
            for item in linked_documents:
                if not isinstance(item, dict):
                    continue
                document_url = str(item.get("url", "")).strip()
                document_title = str(item.get("title", "")).strip()
                document_type = str(item.get("document_type", "")).strip()
                if not document_url:
                    continue
                maybe_add(
                    label=document_title or page_title or "Linked financial document",
                    url=document_url,
                    source_type=_guess_discovered_source_type(
                        source_kind=source_kind,
                        document_type=document_type,
                        url=document_url,
                    ),
                    source_kind=document_type or source_kind,
                    reporting_period=reporting_period,
                    notes=notes,
                    discovery_origin="tinyfish_linked_document",
                    discovery_run_id=run.run_id,
                )

    return refs


def build_raw_legal_source_refs(
    request: ReportRequest,
    runs: list[TinyfishRunRecord],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    def maybe_add(
        *,
        label: str,
        url: str,
        source_type: str,
        source_kind: str = "",
        notes: str = "",
        discovery_origin: str = "",
        discovery_run_id: str = "",
    ) -> None:
        normalized = _normalize_url(url)
        if not normalized or normalized in seen_urls:
            return
        seen_urls.add(normalized)
        refs.append(
            {
                "label": label,
                "url": url,
                "source_type": source_type,
                "source_kind": source_kind,
                "reporting_period": "",
                "notes": notes,
                "discovery_origin": discovery_origin,
                "discovery_run_id": discovery_run_id,
            }
        )

    for index, url in enumerate(request.legal_source_urls, start=1):
        if url.strip():
            maybe_add(
                label=f"Legal source {index}",
                url=url.strip(),
                source_type="legal_document",
                discovery_origin="request_hint",
            )

    for run in runs:
        if run.category != "legal":
            continue
        if not isinstance(run.result, dict):
            if run.source_type in {"legal_court_records", "legal_enforcement", "legal_document"}:
                maybe_add(
                    label=run.label,
                    url=run.url,
                    source_type=run.source_type,
                    discovery_origin="tinyfish_seeded_url",
                    discovery_run_id=run.run_id,
                )
            continue

        sources = run.result.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            page_title = str(source.get("page_title", "")).strip()
            final_url = str(source.get("final_url", "")).strip()
            source_kind = str(source.get("source_kind", "")).strip()
            notes = str(source.get("notes", "")).strip()
            if final_url:
                maybe_add(
                    label=page_title or run.label,
                    url=final_url,
                    source_type=_guess_discovered_legal_source_type(
                        source_kind=source_kind,
                        document_type="",
                        url=final_url,
                    ),
                    source_kind=source_kind,
                    notes=notes,
                    discovery_origin="tinyfish_discovery",
                    discovery_run_id=run.run_id,
                )

            linked_documents = source.get("linked_documents")
            if not isinstance(linked_documents, list):
                continue
            for item in linked_documents:
                if not isinstance(item, dict):
                    continue
                document_url = str(item.get("url", "")).strip()
                document_title = str(item.get("title", "")).strip()
                document_type = str(item.get("document_type", "")).strip()
                if document_url:
                    maybe_add(
                        label=document_title or page_title or "Linked legal document",
                        url=document_url,
                        source_type=_guess_discovered_legal_source_type(
                            source_kind=source_kind,
                            document_type=document_type,
                            url=document_url,
                        ),
                        source_kind=document_type or source_kind,
                        notes=notes,
                        discovery_origin="tinyfish_linked_document",
                        discovery_run_id=run.run_id,
                    )

    return refs


def build_raw_news_source_refs(
    request: ReportRequest,
    runs: list[TinyfishRunRecord],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    def maybe_add(
        *,
        label: str,
        url: str,
        source_type: str,
        source_kind: str = "",
        notes: str = "",
        discovery_origin: str = "",
        discovery_run_id: str = "",
    ) -> None:
        normalized = _normalize_url(url)
        if not normalized or normalized in seen_urls:
            return
        seen_urls.add(normalized)
        refs.append(
            {
                "label": label,
                "url": url,
                "source_type": source_type,
                "source_kind": source_kind,
                "reporting_period": "",
                "notes": notes,
                "discovery_origin": discovery_origin,
                "discovery_run_id": discovery_run_id,
            }
        )

    for index, url in enumerate(request.news_source_urls, start=1):
        if url.strip():
            maybe_add(
                label=f"News source {index}",
                url=url.strip(),
                source_type="news_article",
                discovery_origin="request_hint",
            )

    for run in runs:
        if run.category != "news":
            continue
        if not isinstance(run.result, dict):
            continue

        sources = run.result.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            page_title = str(source.get("page_title", "")).strip()
            final_url = str(source.get("final_url", "")).strip()
            source_kind = str(source.get("source_kind", "")).strip()
            notes = str(source.get("notes", "")).strip()
            if final_url:
                maybe_add(
                    label=page_title or run.label,
                    url=final_url,
                    source_type=_guess_discovered_news_source_type(
                        source_kind=source_kind,
                        url=final_url,
                    ),
                    source_kind=source_kind,
                    notes=notes,
                    discovery_origin="tinyfish_discovery",
                    discovery_run_id=run.run_id,
                )

            linked_documents = source.get("linked_documents")
            if not isinstance(linked_documents, list):
                continue
            for item in linked_documents:
                if not isinstance(item, dict):
                    continue
                document_url = str(item.get("url", "")).strip()
                document_title = str(item.get("title", "")).strip()
                if document_url:
                    maybe_add(
                        label=document_title or page_title or "Linked news article",
                        url=document_url,
                        source_type=_guess_discovered_news_source_type(
                            source_kind=source_kind,
                            url=document_url,
                        ),
                        source_kind=source_kind,
                        notes=notes,
                        discovery_origin="tinyfish_linked_document",
                        discovery_run_id=run.run_id,
                    )

    return refs


def _render_capture_sources(result: dict[str, Any]) -> list[str]:
    rendered: list[str] = []
    sources = result.get("sources")
    if isinstance(sources, list):
        for index, source in enumerate(sources, start=1):
            if not isinstance(source, dict):
                continue
            linked_documents = source.get("linked_documents")
            linked_lines: list[str] = []
            if isinstance(linked_documents, list):
                for item in linked_documents:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title", "")).strip()
                    url = str(item.get("url", "")).strip()
                    document_type = str(item.get("document_type", "")).strip()
                    if title or url:
                        linked_lines.append(
                            f"- {title or 'Document'} | {document_type or 'unknown'} | {url}"
                        )

            rendered.append(
                "\n".join(
                    [
                        f"Captured source {index}",
                        f"Page title: {source.get('page_title', '')}",
                        f"Final URL: {source.get('final_url', '')}",
                        f"Source kind: {source.get('source_kind', '')}",
                        f"Reporting period: {source.get('reporting_period', '')}",
                        (
                            "Captured text:\n"
                            + str(source.get("captured_text", "")).strip()
                            if str(source.get("captured_text", "")).strip()
                            else ""
                        ),
                        "Linked documents:",
                        "\n".join(linked_lines) if linked_lines else "- None",
                        f"Notes: {source.get('notes', '')}",
                    ]
                ).strip()
            )
    return rendered


def _render_tinyfish_evidence(run: TinyfishRunRecord) -> str:
    sections = [
        f"Run status: {run.status}",
        f"Source label: {run.label}",
        f"Source type: {run.source_type}",
        f"Source URL: {run.url}",
    ]
    if run.access_blocked:
        sections.append("Access: the site refused automated access; nothing was captured.")
    if isinstance(run.result, dict):
        capture_mode = str(run.result.get("capture_mode", "")).strip()
        if capture_mode:
            sections.append(f"Capture mode: {capture_mode}")
        sections.extend(_render_capture_sources(run.result))
        coverage_gaps = run.result.get("coverage_gaps")
        if isinstance(coverage_gaps, list) and coverage_gaps:
            sections.append(
                "Coverage gaps:\n" + "\n".join(f"- {str(item).strip()}" for item in coverage_gaps)
            )
    elif run.result is not None:
        sections.append(
            "Result payload:\n"
            + json.dumps(run.result, indent=2, sort_keys=True, ensure_ascii=True)
        )

    if run.error:
        sections.append(
            "Error payload:\n"
            + json.dumps(run.error, indent=2, sort_keys=True, ensure_ascii=True)
        )

    step_trace = [
        {
            "timestamp": step.get("timestamp"),
            "status": step.get("status"),
            "action": step.get("action"),
            "duration": step.get("duration"),
        }
        for step in run.steps[:20]
    ]
    if step_trace:
        sections.append(
            "Tinyfish step trace:\n"
            + json.dumps(step_trace, indent=2, sort_keys=True, ensure_ascii=True)
        )
    if run.attempts:
        sections.append(
            "Tinyfish attempts:\n"
            + json.dumps(run.attempts, indent=2, sort_keys=True, ensure_ascii=True)
        )
    return "\n\n".join(section for section in sections if section).strip()


def normalize_tinyfish_runs(runs: list[TinyfishRunRecord]) -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []
    for index, run in enumerate(runs, start=1):
        evidence.append(
            EvidenceItem(
                evidence_id=f"tinyfish_{run.category or 'general'}_{index:03d}",
                label=run.label,
                category=run.category,
                url=run.url,
                retrieved_at=run.finished_at or run.started_at or run.created_at or utc_now_iso(),
                content=_render_tinyfish_evidence(run),
                metadata={
                    "tinyfish_run_id": run.run_id,
                    "tinyfish_status": run.status,
                    "tinyfish_goal": run.goal,
                    "tinyfish_source_type": run.source_type,
                    "tinyfish_streaming_url": run.streaming_url,
                    "tinyfish_num_of_steps": run.num_of_steps,
                    "tinyfish_browser_config": run.browser_config,
                },
            )
        )
    return evidence


def _build_openai_user_content(
    *,
    preamble: str,
    evidence: list[EvidenceItem],
    run_dir: Path | None = None,
) -> list[dict[str, Any]]:
    text_sections: list[str] = [preamble, "", "Evidence bundle:"]
    file_inputs: list[dict[str, Any]] = []

    for item in evidence:
        file_input = _build_local_file_input(item, run_dir)
        if file_input and len(file_inputs) < MAX_FILE_INPUTS_PER_REQUEST:
            file_inputs.append(file_input)
        elif len(file_inputs) < MAX_FILE_INPUTS_PER_REQUEST:
            file_url = str(item.metadata.get("openai_input_file_url", "")).strip()
            if file_url:
                file_inputs.append({"type": "input_file", "file_url": file_url})

        raw_text = str(item.metadata.get("openai_input_text", "")).strip() or item.content
        if len(raw_text) > MAX_TEXT_EVIDENCE_CHARS:
            raw_text = raw_text[:MAX_TEXT_EVIDENCE_CHARS].rstrip() + "\n...[truncated]"

        text_sections.append(
            "\n".join(
                [
                    f"Evidence ID: {item.evidence_id}",
                    f"Label: {item.label}",
                    f"Category: {item.category}",
                    f"URL: {item.url}",
                    f"Source type: {item.metadata.get('source_type', item.metadata.get('tinyfish_source_type', ''))}",
                    f"Retrieved at: {item.retrieved_at}",
                    "Content:",
                    raw_text,
                ]
            ).strip()
        )

    content: list[dict[str, Any]] = [{"type": "input_text", "text": "\n\n".join(text_sections).strip()}]
    content.extend(file_inputs)
    return content


def _build_local_file_input(
    item: EvidenceItem, run_dir: Path | None
) -> dict[str, Any] | None:
    if run_dir is None:
        return None
    artifact_relpath = str(item.metadata.get("openai_input_file_path", "")).strip()
    if not artifact_relpath:
        return None
    candidate = (run_dir / artifact_relpath).resolve()
    run_root = run_dir.resolve()
    if run_root not in candidate.parents or not candidate.exists() or not candidate.is_file():
        return None
    if candidate.suffix.lower() != ".pdf":
        return None
    encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
    return {
        "type": "input_file",
        "filename": str(item.metadata.get("openai_input_filename", "")).strip()
        or candidate.name,
        "file_data": f"data:application/pdf;base64,{encoded}",
    }


def compute_publish_decision(
    focus_areas: list[str], verified_claims: list[VerifiedClaim]
) -> tuple[str, str]:
    supported_by_section: dict[str, int] = defaultdict(int)
    unsupported_count = 0
    for claim in verified_claims:
        if claim.status == "supported":
            supported_by_section[claim.section] += 1
        else:
            unsupported_count += 1
    covered_sections = sum(1 for area in focus_areas if supported_by_section.get(area, 0) > 0)
    minimum_required = min(4, len(focus_areas))
    if covered_sections < minimum_required:
        return (
            "blocked",
            f"Only {covered_sections} focus areas have supported evidence; minimum required is {minimum_required}.",
        )
    if unsupported_count > len(verified_claims) // 2:
        return (
            "blocked",
            "More than half of extracted claims failed verification.",
        )
    return ("publish", "Coverage and verification thresholds were met.")


def select_evidence_for_agent(
    agent: DomainAgent, evidence: list[EvidenceItem]
) -> list[EvidenceItem]:
    if agent.use_all_evidence:
        return evidence
    preferred = set(agent.preferred_categories)
    filtered = [item for item in evidence if item.category in preferred]
    return filtered or evidence


def select_evidence_for_extraction_agent(
    agent: ExtractionAgent, evidence: list[EvidenceItem]
) -> list[EvidenceItem]:
    if agent.use_all_evidence:
        return evidence
    preferred_categories = set(agent.preferred_categories)
    filtered = [item for item in evidence if item.category in preferred_categories]
    if agent.preferred_source_types:
        preferred_source_types = set(agent.preferred_source_types)
        filtered_by_source_type = [
            item
            for item in filtered
            if item.metadata.get("tinyfish_source_type", "") in preferred_source_types
        ]
        if filtered_by_source_type:
            return filtered_by_source_type
    return filtered or evidence


def build_risk_matrix(verified_claims: list[VerifiedClaim]) -> list[RiskMatrixEntry]:
    entries: list[RiskMatrixEntry] = []
    recommendations = {
        "legal": "Escalate to counsel for independent verification. Confirm case status, jurisdiction exposure, and potential liability before proceeding with any transaction.",
        "financials": "Request primary financial statements and reconcile any quantitative exposure before relying on the claim.",
        "news": "Verify whether the event remains current, assess reputational impact, and determine whether it materially changes the investment thesis or company narrative.",
        "risk_signals": "Treat this as a decision blocker until the issue is independently cleared or contextualized.",
    }
    for claim in verified_claims:
        if claim.status != "supported":
            continue
        if claim.section not in {"legal", "financials", "news", "risk_signals"}:
            continue
        if claim.importance not in {"high", "medium"}:
            continue
        entries.append(
            RiskMatrixEntry(
                section=claim.section,
                severity="high" if claim.importance == "high" else "medium",
                issue=claim.statement,
                evidence_ids=claim.evidence_ids,
                recommendation=recommendations.get(
                    claim.section,
                    "Review the source evidence and determine whether this changes the diligence conclusion.",
                ),
            )
        )
    return entries[:12]


def build_pdf_section_order(active_sections: list[ReportSection]) -> list[str]:
    ordered = [
        "Executive Summary",
        "Scope and Methodology",
        "Entity Profile",
    ]
    ordered.extend(section.name for section in active_sections)
    ordered.extend(
        [
            "Key Findings",
            "Risk Matrix",
            "Recommendations and Decision Path",
            "Appendix / Source Log",
        ]
    )
    return ordered


def _fallback_section_payload(
    section: str, claims: list[VerifiedClaim], error: Exception | None = None
) -> dict[str, Any]:
    claim_statements = [claim.statement.strip() for claim in claims if claim.statement.strip()]
    summary = (
        " ".join(claim_statements[:2]).strip()
        or "Insufficient verified evidence for this section."
    )
    if error is not None:
        summary = (
            f"{summary} Summary model fallback was used after an error: {str(error).strip()}."
        ).strip()
    payload: dict[str, Any] = {
        "summary": summary,
        "highlights": claim_statements[:5],
        "recommendations": [
            "Review the verified claims and underlying evidence directly before relying on this section."
        ],
    }
    if section == "financials":
        payload["key_metrics"] = []
        payload["red_flags"] = claim_statements[:3]
        payload["data_gaps"] = []
    elif section == "legal":
        payload["active_matters"] = []
        payload["sanctions_flags"] = []
        payload["regulatory_exposure"] = claim_statements[:3]
        payload["red_flags"] = claim_statements[:3]
        payload["data_gaps"] = []
    elif section == "news":
        payload["material_events"] = []
        payload["coverage_sentiment"] = "mixed"
        payload["coverage_density"] = "medium"
        payload["negative_signals"] = claim_statements[:3]
        payload["data_gaps"] = []
    return payload


def _fallback_aggregation_payload(
    *,
    request: ReportRequest,
    sections: list[ReportSection],
    risk_matrix: list[RiskMatrixEntry],
    publish_decision: str,
    publish_reason: str,
    error: Exception | None = None,
) -> dict[str, Any]:
    supported_sections = [section for section in sections if section.status == "supported"]
    findings = [
        highlight
        for section in supported_sections
        for highlight in section.highlights[:2]
    ][:8]
    if not findings:
        findings = [publish_reason]
    methodology = (
        "Tinyfish discovered and fetched raw sources, then OpenAI extracted, verified, "
        "and synthesized only supported claims."
    )
    if error is not None:
        methodology = (
            f"{methodology} Executive aggregation fallback was used after an error: {str(error).strip()}."
        )
    entity_profile = next(
        (
            section.summary
            for section in supported_sections
            if section.name == "company_overview"
        ),
        f"{request.company_name} was reviewed across financial, legal, and news evidence.",
    )
    return {
        "executive_summary": (
            f"Publish decision: {publish_decision}. {publish_reason} "
            f"{len(supported_sections)} section(s) have supported evidence."
        ).strip(),
        "scope_and_limitations": (
            "This report is fully automated and only includes claims supported by the collected evidence bundle."
        ),
        "methodology": methodology,
        "entity_profile": entity_profile,
        "diligence_conclusion": (
            f"{publish_decision.capitalize()} decision based on verified claims, "
            f"{len(risk_matrix)} material risk item(s), and evidence-backed section summaries."
        ),
        "key_findings": findings,
        "recommendations": [
            recommendation
            for section in sections
            for recommendation in section.recommendations[:1]
        ][:6]
        or ["Collect stronger primary-source evidence before relying on this report."],
        "pdf_section_order": build_pdf_section_order(sections),
    }


class ReportPipeline:
    def __init__(
        self,
        config: AppConfig,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.progress_callback = progress_callback
        self._event_lock = threading.Lock()
        self.storage = RunStorage(config.report_output_dir)
        self.tinyfish = TinyfishClient(
            api_key=config.tinyfish_api_key,
            base_url=config.tinyfish_base_url,
            poll_interval_seconds=config.tinyfish_poll_interval_seconds,
            poll_timeout_seconds=config.tinyfish_poll_timeout_seconds,
            request_timeout_seconds=config.tinyfish_request_timeout_seconds,
            batch_size=config.tinyfish_batch_size,
            request_max_retries=config.tinyfish_request_max_retries,
            request_retry_backoff_seconds=config.tinyfish_request_retry_backoff_seconds,
            event_callback=progress_callback,
        )
        self.raw_source_client = RawSourceClient(
            timeout_seconds=config.raw_source_timeout_seconds
        )
        self.openai = OpenAIClient(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
            model=config.openai_model,
            request_timeout_seconds=config.openai_request_timeout_seconds,
            request_max_retries=config.openai_request_max_retries,
            request_retry_backoff_seconds=config.openai_request_retry_backoff_seconds,
        )

    def _emit_event(self, payload: dict[str, Any]) -> None:
        if self.progress_callback is None:
            return
        with self._event_lock:
            self.progress_callback({"timestamp": utc_now_iso(), **payload})

    def _emit_stage_event(
        self,
        *,
        stage: str,
        status: str,
        message: str,
        current: int | None = None,
        total: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "event": "stage_update",
            "stage": stage,
            "status": status,
            "message": message,
        }
        if current is not None:
            payload["current"] = current
        if total is not None:
            payload["total"] = total
        if extra:
            payload.update(extra)
        self._emit_event(payload)

    def _write_artifact(self, run_dir: Any, relative_path: str, payload: Any) -> None:
        self.storage.write_json(run_dir, relative_path, payload)
        self._emit_event(
            {
                "event": "artifact_written",
                "relative_path": relative_path,
                "run_dir": str(run_dir),
            }
        )

    def run(self, request: ReportRequest) -> tuple[FinalReport, str]:
        run_dir = self.storage.create_run_dir(request.company_name)
        self._emit_event(
            {
                "event": "pipeline_started",
                "company_name": request.company_name,
                "run_dir": str(run_dir),
                "openai_model": self.config.openai_model,
            }
        )
        try:
            self._emit_stage_event(
                stage="planning",
                status="running",
                message="Building source plan for the requested company.",
            )
            self._write_artifact(run_dir, "request.json", request.to_dict())

            source_jobs = seed_source_jobs(
                request, self.config.tinyfish_default_browser_profile
            )
            self._write_artifact(
                run_dir, "source_jobs.json", [job.to_dict() for job in source_jobs]
            )
            self._emit_stage_event(
                stage="planning",
                status="completed",
                message=f"Planned {len(source_jobs)} source jobs.",
                current=len(source_jobs),
                total=len(source_jobs),
                extra={
                    "jobs": [job.to_dict() for job in source_jobs],
                },
            )

            self._emit_stage_event(
                stage="acquisition",
                status="running",
                message="Running Tinyfish across discovered sources.",
                current=0,
                total=len(source_jobs),
            )
            scheduled_jobs = list(source_jobs)
            pending_jobs = list(source_jobs)
            tinyfish_runs: list[TinyfishRunRecord] = []
            seen_urls = {_normalize_url(job.url) for job in scheduled_jobs if job.url.strip()}
            acquisition_wave = 0
            max_total_jobs = max(1, self.config.tinyfish_max_total_jobs)
            max_acquisition_waves = max(1, self.config.tinyfish_max_acquisition_waves)

            while (
                pending_jobs
                and acquisition_wave < max_acquisition_waves
                and len(scheduled_jobs) <= max_total_jobs
            ):
                wave_runs = self.tinyfish.run_jobs(pending_jobs)
                tinyfish_runs.extend(wave_runs)
                acquisition_wave += 1
                self._emit_stage_event(
                    stage="acquisition",
                    status="running",
                    message=(
                        f"Completed acquisition wave {acquisition_wave}; "
                        f"{len(tinyfish_runs)} of {len(scheduled_jobs)} source jobs processed."
                    ),
                    current=len(tinyfish_runs),
                    total=len(scheduled_jobs),
                )

                spawned_jobs = (
                    build_spawned_financial_source_jobs(
                        request,
                        wave_runs,
                        self.config.tinyfish_default_browser_profile,
                        seen_urls,
                    )
                    + build_spawned_legal_source_jobs(
                        request,
                        wave_runs,
                        self.config.tinyfish_default_browser_profile,
                        seen_urls,
                    )
                    + build_spawned_news_source_jobs(
                        request,
                        wave_runs,
                        self.config.tinyfish_default_browser_profile,
                        seen_urls,
                    )
                )
                if not spawned_jobs:
                    pending_jobs = []
                    continue

                remaining_capacity = max(0, max_total_jobs - len(scheduled_jobs))
                if remaining_capacity == 0:
                    pending_jobs = []
                    continue
                if len(spawned_jobs) > remaining_capacity:
                    spawned_jobs = spawned_jobs[:remaining_capacity]

                scheduled_jobs.extend(spawned_jobs)
                for job in spawned_jobs:
                    self._emit_event(
                        {
                            "event": "source_job_update",
                            "stage": "acquisition",
                            "label": job.label,
                            "category": job.category,
                            "url": job.url,
                            "source_type": job.source_type,
                            "status": "queued",
                            "message": "Spawned from discovered financial sources.",
                            "browser_profile": job.browser_profile,
                            "run_id": "",
                            "streaming_url": "",
                            "attempt_number": 0,
                        }
                    )

                self._write_artifact(
                    run_dir,
                    "source_jobs.json",
                    [job.to_dict() for job in scheduled_jobs],
                )
                self._emit_stage_event(
                    stage="acquisition",
                    status="running",
                    message=(
                        f"Spawned {len(spawned_jobs)} child source "
                        f"{'agents' if len(spawned_jobs) != 1 else 'agent'} from discovered links."
                    ),
                    current=len(tinyfish_runs),
                    total=len(scheduled_jobs),
                )
                pending_jobs = spawned_jobs

            self._write_artifact(
                run_dir,
                "tinyfish_runs.json",
                [record.to_dict() for record in tinyfish_runs],
            )
            raw_financial_refs = build_raw_financial_source_refs(request, tinyfish_runs)
            raw_legal_refs = build_raw_legal_source_refs(request, tinyfish_runs)
            raw_news_refs = build_raw_news_source_refs(request, tinyfish_runs)
            all_raw_refs = raw_financial_refs + raw_legal_refs + raw_news_refs
            self._write_artifact(
                run_dir,
                "raw_source_refs.json",
                all_raw_refs,
            )
            self._emit_stage_event(
                stage="acquisition",
                status="running",
                message=(
                    f"Directly fetching {len(all_raw_refs)} raw source "
                    f"{'artifacts' if len(all_raw_refs) != 1 else 'artifact'} "
                    f"({len(raw_financial_refs)} financial, {len(raw_legal_refs)} legal, "
                    f"{len(raw_news_refs)} news)."
                ),
                current=len(tinyfish_runs),
                total=len(scheduled_jobs),
            )

            all_raw_evidence: list[EvidenceItem] = []
            all_raw_manifests: list[dict[str, Any]] = []
            for category_label, category_refs in [
                ("financials", raw_financial_refs),
                ("legal", raw_legal_refs),
                ("news", raw_news_refs),
            ]:
                if not category_refs:
                    continue
                cat_evidence, cat_manifest = self.raw_source_client.collect_evidence(
                    source_refs=category_refs,
                    run_dir=run_dir,
                    category=category_label,
                )
                all_raw_evidence.extend(cat_evidence)
                all_raw_manifests.extend(cat_manifest)

            self._write_artifact(
                run_dir,
                "raw_source_manifest.json",
                all_raw_manifests,
            )
            self._emit_stage_event(
                stage="acquisition",
                status="completed",
                message=(
                    f"Completed acquisition: {len(tinyfish_runs)} Tinyfish discovery jobs and "
                    f"{len(all_raw_evidence)} directly fetched raw sources."
                ),
                current=len(all_raw_evidence),
                total=max(len(all_raw_refs), 1),
            )

            tinyfish_evidence = normalize_tinyfish_runs(tinyfish_runs)
            evidence = all_raw_evidence + tinyfish_evidence if all_raw_evidence else tinyfish_evidence
            self._write_artifact(
                run_dir, "evidence.json", [item.to_dict() for item in evidence]
            )

            extracted_claims = self._extract_claims(request, evidence, run_dir)
            self._write_artifact(
                run_dir,
                "extracted_claims.json",
                [claim.to_dict() for claim in extracted_claims],
            )

            verified_claims = self._verify_claims(extracted_claims, evidence, run_dir)
            self._write_artifact(
                run_dir,
                "verified_claims.json",
                [claim.to_dict() for claim in verified_claims],
            )

            final_report = self._build_report(request, verified_claims, evidence)
            self._write_artifact(run_dir, "final_report.json", final_report.to_dict())
            self._emit_event(
                {
                    "event": "pipeline_completed",
                    "run_dir": str(run_dir),
                    "final_report": final_report.to_dict(),
                }
            )
            return final_report, str(run_dir)
        except Exception as exc:
            self._emit_event(
                {
                    "event": "pipeline_failed",
                    "run_dir": str(run_dir),
                    "error": str(exc),
                }
            )
            raise

    def _extract_claims(
        self, request: ReportRequest, evidence: list[EvidenceItem], run_dir: Path
    ) -> list[ExtractedClaim]:
        claims: list[ExtractedClaim] = []
        extraction_units = [
            specialist
            for agent in get_active_agents(request.focus_areas)
            for specialist in get_extraction_agents(agent)
        ]
        total_specialists = len(extraction_units)
        completed_specialists = 0
        started_specialists = 0
        lock = threading.Lock()

        self._emit_stage_event(
            stage="extraction",
            status="running",
            message="Extracting structured claims from evidence bundles.",
            current=0,
            total=total_specialists,
        )

        def process_specialist(agent: DomainAgent, specialist: ExtractionAgent) -> list[ExtractedClaim]:
            nonlocal completed_specialists
            nonlocal started_specialists
            with lock:
                started_specialists += 1
                current = started_specialists
            self._emit_event(
                {
                    "event": "specialist_update",
                    "stage": "extraction",
                    "status": "running",
                    "section": agent.section,
                    "specialist": specialist.name,
                    "message": f"Running {specialist.name}.",
                    "current": current,
                    "total": total_specialists,
                }
            )
            specialist_evidence = select_evidence_for_extraction_agent(
                specialist,
                select_evidence_for_agent(agent, evidence),
            )
            try:
                payload = self.openai.structured_response(
                    schema_name=f"{agent.section}_{specialist.name}_claim_extraction",
                    schema=extraction_schema([agent.section]),
                    system_prompt=(
                        f"{EXTRACTION_SYSTEM_PROMPT}\n\n"
                        f"Coordinator section: {agent.section}\n"
                        f"Coordinator: {agent.name}\n"
                        f"Specialist: {specialist.name}\n"
                        f"Specialist instruction: {specialist.extraction_instruction}"
                    ),
                    user_content=_build_openai_user_content(
                        preamble=(
                        f"Company: {request.company_name}\n"
                        f"Jurisdiction: {request.primary_jurisdiction}\n"
                        f"Target section: {agent.section}\n"
                        f"Goal: {request.report_goal or 'Generate a source-backed diligence report.'}\n\n"
                        "Use attached PDFs and raw source text to extract only directly supported claims."
                        ),
                        evidence=specialist_evidence,
                        run_dir=run_dir,
                    ),
                )
            except Exception as exc:
                with lock:
                    completed_specialists += 1
                    self._emit_event(
                        {
                            "event": "specialist_update",
                            "stage": "extraction",
                            "status": "failed",
                            "section": agent.section,
                            "specialist": specialist.name,
                            "message": f"{specialist.name} failed and was skipped: {str(exc).strip()}",
                            "current": completed_specialists,
                            "total": total_specialists,
                            "claims_extracted": 0,
                        }
                    )
                    self._emit_stage_event(
                        stage="extraction",
                        status="running",
                        message="Extracting structured claims from evidence bundles.",
                        current=completed_specialists,
                        total=total_specialists,
                    )
                return []

            extracted = []
            for offset, claim in enumerate(payload.get("claims", []), start=1):
                extracted.append(
                    ExtractedClaim(
                        claim_id=f"{agent.section}_{specialist.name}_{offset:03d}",
                        section=str(claim["section"]),
                        statement=str(claim["statement"]).strip(),
                        importance=str(claim["importance"]),
                        confidence=float(claim["confidence"]),
                        as_of=str(claim["as_of"]).strip(),
                        source_quote=str(claim["source_quote"]).strip(),
                        evidence_ids=[
                            item.evidence_id for item in specialist_evidence
                        ],
                        raw_source_label=specialist.name,
                    )
                )

            with lock:
                completed_specialists += 1
                self._emit_event(
                    {
                        "event": "specialist_update",
                        "stage": "extraction",
                        "status": "completed",
                        "section": agent.section,
                        "specialist": specialist.name,
                        "message": f"{specialist.name} extracted {len(payload.get('claims', []))} claims.",
                        "current": completed_specialists,
                        "total": total_specialists,
                        "claims_extracted": len(payload.get("claims", [])),
                    }
                )
                self._emit_stage_event(
                    stage="extraction",
                    status="running",
                    message="Extracting structured claims from evidence bundles.",
                    current=completed_specialists,
                    total=total_specialists,
                )
            return extracted

        with ThreadPoolExecutor(
            max_workers=min(self.config.openai_extraction_max_workers, total_specialists or 1)
        ) as executor:
            futures = []
            for agent in get_active_agents(request.focus_areas):
                for specialist in get_extraction_agents(agent):
                    futures.append(executor.submit(process_specialist, agent, specialist))

            # Collect in submission order so the claim list, and every
            # artifact derived from it, is identical across runs.
            for future in futures:
                claims.extend(future.result())

        self._emit_stage_event(
            stage="extraction",
            status="completed",
            message=f"Extracted {len(claims)} raw claims.",
            current=total_specialists,
            total=total_specialists,
        )
        return claims

    def _verify_claims(
        self, claims: list[ExtractedClaim], evidence: list[EvidenceItem], run_dir: Path
    ) -> list[VerifiedClaim]:
        evidence_map = {item.evidence_id: item for item in evidence}
        verified: list[VerifiedClaim] = []
        total_claims = len(claims)
        lock = threading.Lock()

        self._emit_stage_event(
            stage="verification",
            status="running",
            message="Verifying extracted claims against evidence.",
            current=0,
            total=total_claims,
        )

        def verify_single(claim: ExtractedClaim) -> VerifiedClaim:
            referenced = [evidence_map[eid] for eid in claim.evidence_ids if eid in evidence_map]
            try:
                payload = self.openai.structured_response(
                    schema_name="claim_verification",
                    schema=verification_schema(),
                    system_prompt=VERIFICATION_SYSTEM_PROMPT,
                    user_content=_build_openai_user_content(
                        preamble=(
                        f"Claim ID: {claim.claim_id}\n"
                        f"Section: {claim.section}\n"
                        f"Claim: {claim.statement}\n"
                        f"Quoted support from extraction: {claim.source_quote}\n\n"
                        "Verify this claim only against the attached raw sources and text evidence."
                        ),
                        evidence=referenced,
                        run_dir=run_dir,
                    ),
                    max_output_tokens=1200,
                )
                verified_claim = VerifiedClaim(
                    claim_id=claim.claim_id,
                    section=claim.section,
                    statement=claim.statement,
                    status=str(payload["status"]),
                    confidence=float(payload["confidence"]),
                    rationale=str(payload["rationale"]).strip(),
                    evidence_ids=[
                        str(item) for item in payload.get("supporting_evidence_ids", [])
                    ]
                    or claim.evidence_ids,
                    blocking_issues=[
                        str(item) for item in payload.get("blocking_issues", [])
                    ],
                    source_quote=claim.source_quote,
                    importance=claim.importance,
                    as_of=claim.as_of,
                )
            except Exception as exc:
                verified_claim = VerifiedClaim(
                    claim_id=claim.claim_id,
                    section=claim.section,
                    statement=claim.statement,
                    status="insufficient_evidence",
                    confidence=0.0,
                    rationale=f"Verification fallback used after error: {str(exc).strip()}",
                    evidence_ids=claim.evidence_ids,
                    blocking_issues=[str(exc).strip()],
                    source_quote=claim.source_quote,
                    importance=claim.importance,
                    as_of=claim.as_of,
                )

            with lock:
                verified.append(verified_claim)
                self._emit_event(
                    {
                        "event": "verification_update",
                        "stage": "verification",
                        "status": "running",
                        "message": f"Verified {claim.claim_id}.",
                        "current": len(verified),
                        "total": total_claims,
                        "claim_id": claim.claim_id,
                        "claim_status": verified_claim.status,
                    }
                )
            return verified_claim

        with ThreadPoolExecutor(
            max_workers=min(self.config.openai_verification_max_workers, total_claims or 1)
        ) as executor:
            futures = [executor.submit(verify_single, claim) for claim in claims]
            ordered = [future.result() for future in futures]

        self._emit_stage_event(
            stage="verification",
            status="completed",
            message=f"Verification finished for {len(verified)} claims.",
            current=len(verified),
            total=total_claims,
        )
        return ordered

    def _build_report(
        self,
        request: ReportRequest,
        verified_claims: list[VerifiedClaim],
        evidence: list[EvidenceItem],
    ) -> FinalReport:
        self._emit_stage_event(
            stage="reporting",
            status="running",
            message="Synthesizing final report and section summaries.",
        )
        supported_claims = [claim for claim in verified_claims if claim.status == "supported"]
        grouped: dict[str, list[VerifiedClaim]] = defaultdict(list)
        for claim in supported_claims:
            grouped[claim.section].append(claim)

        publish_decision, publish_reason = compute_publish_decision(
            request.focus_areas, verified_claims
        )
        sections = self._build_section_reports(request, grouped)
        risk_matrix = build_risk_matrix(verified_claims)
        try:
            aggregation_payload = self.openai.structured_response(
                schema_name="executive_company_report",
                schema=executive_report_schema(),
                system_prompt=EXECUTIVE_AGGREGATION_SYSTEM_PROMPT,
                user_prompt=self._executive_aggregation_prompt(
                    request=request,
                    sections=sections,
                    risk_matrix=risk_matrix,
                    publish_decision=publish_decision,
                    publish_reason=publish_reason,
                ),
                max_output_tokens=2200,
            )
        except Exception as exc:
            aggregation_payload = _fallback_aggregation_payload(
                request=request,
                sections=sections,
                risk_matrix=risk_matrix,
                publish_decision=publish_decision,
                publish_reason=publish_reason,
                error=exc,
            )

        source_log = [
            {
                "evidence_id": item.evidence_id,
                "label": item.label,
                "category": item.category,
                "source_type": item.metadata.get(
                    "source_type", item.metadata.get("tinyfish_source_type", "")
                ),
                "url": item.url,
                "retrieved_at": item.retrieved_at,
                "direct_fetch_mode": item.metadata.get("direct_fetch_mode", ""),
                "direct_fetch_artifact_path": item.metadata.get(
                    "direct_fetch_artifact_path", ""
                ),
                "openai_input_file_path": item.metadata.get("openai_input_file_path", ""),
                "openai_input_file_url": item.metadata.get("openai_input_file_url", ""),
                "tinyfish_status": item.metadata.get("tinyfish_status", ""),
                "tinyfish_streaming_url": item.metadata.get(
                    "tinyfish_streaming_url", ""
                ),
                "tinyfish_num_of_steps": item.metadata.get("tinyfish_num_of_steps"),
            }
            for item in evidence
        ]

        final_report = FinalReport(
            company_name=request.company_name,
            generated_at=utc_now_iso(),
            publish_decision=publish_decision,
            publish_reason=publish_reason,
            executive_summary=str(aggregation_payload.get("executive_summary", "")).strip(),
            scope_and_limitations=str(
                aggregation_payload.get("scope_and_limitations", "")
            ).strip(),
            methodology=str(aggregation_payload.get("methodology", "")).strip(),
            entity_profile=str(aggregation_payload.get("entity_profile", "")).strip(),
            diligence_conclusion=str(
                aggregation_payload.get("diligence_conclusion", "")
            ).strip(),
            key_findings=[
                str(item).strip()
                for item in aggregation_payload.get("key_findings", [])
                if str(item).strip()
            ],
            recommendations=[
                str(item).strip()
                for item in aggregation_payload.get("recommendations", [])
                if str(item).strip()
            ],
            pdf_section_order=[
                str(item).strip()
                for item in aggregation_payload.get("pdf_section_order", [])
                if str(item).strip()
            ]
            or build_pdf_section_order(sections),
            risk_matrix=risk_matrix,
            sections=sections,
            source_log=source_log,
            unsupported_claims=sum(
                1 for claim in verified_claims if claim.status != "supported"
            ),
            total_claims=len(verified_claims),
        )
        self._emit_stage_event(
            stage="reporting",
            status="completed",
            message="Final report assembled.",
            current=len(sections),
            total=len(sections),
        )
        return final_report

    def _build_section_reports(
        self,
        request: ReportRequest,
        grouped_claims: dict[str, list[VerifiedClaim]],
    ) -> list[ReportSection]:
        sections: list[ReportSection] = []
        active_agents = get_active_agents(request.focus_areas)
        total_sections = len(active_agents)
        for index, agent in enumerate(active_agents, start=1):
            self._emit_event(
                {
                    "event": "report_section_update",
                    "stage": "reporting",
                    "status": "running",
                    "section": agent.section,
                    "current": index - 1,
                    "total": total_sections,
                    "message": f"Synthesizing section {agent.section}.",
                }
            )
            claims = grouped_claims.get(agent.section, [])
            if claims:
                schema = self._section_schema_for(agent.section)
                try:
                    payload = self.openai.structured_response(
                        schema_name=f"{agent.section}_section_summary",
                        schema=schema,
                        system_prompt=(
                            f"{REPORT_SYNTHESIS_SYSTEM_PROMPT}\n\n"
                            f"Agent: {agent.name}\n"
                            f"Section: {agent.section}\n"
                            f"Instruction: {agent.synthesis_instruction}"
                        ),
                        user_prompt=self._section_synthesis_prompt(
                            request.company_name, agent, claims
                        ),
                        max_output_tokens=1400,
                    )
                except Exception as exc:
                    payload = _fallback_section_payload(
                        agent.section,
                        claims,
                        error=exc,
                    )
                summary = str(payload.get("summary", "")).strip()
                highlights = [
                    str(item).strip()
                    for item in payload.get("highlights", [])
                    if str(item).strip()
                ]
                recommendations = [
                    str(item).strip()
                    for item in payload.get("recommendations", [])
                    if str(item).strip()
                ]
                structured_data = self._extract_structured_data(agent.section, payload)
            else:
                summary = "Insufficient verified evidence for this section."
                highlights = []
                recommendations = [
                    "Add stronger primary sources for this section before relying on it in a decision."
                ]
                structured_data = self._empty_structured_data(agent.section)

            sections.append(
                ReportSection(
                    name=agent.section,
                    status="supported" if claims else "insufficient_evidence",
                    summary=summary,
                    highlights=highlights or [claim.statement for claim in claims[:3]],
                    evidence_ids=sorted(
                        {
                            evidence_id
                            for claim in claims
                            for evidence_id in claim.evidence_ids
                        }
                    ),
                    recommendations=recommendations,
                    structured_data=structured_data,
                )
            )
            self._emit_event(
                {
                    "event": "report_section_update",
                    "stage": "reporting",
                    "status": "completed",
                    "section": agent.section,
                    "current": index,
                    "total": total_sections,
                    "message": f"Section {agent.section} ready.",
                }
            )
        self._emit_stage_event(
            stage="reporting",
            status="running",
            message="Section synthesis complete. Building final report shell.",
            current=len(sections),
            total=total_sections,
        )
        return sections

    @staticmethod
    def _section_schema_for(section: str) -> dict:
        if section == "financials":
            return financial_section_summary_schema()
        if section == "legal":
            return legal_section_summary_schema()
        if section == "news":
            return news_section_summary_schema()
        return section_summary_schema()

    @staticmethod
    def _extract_structured_data(section: str, payload: dict[str, Any]) -> dict[str, Any]:
        if section == "financials":
            return {
                "key_metrics": payload.get("key_metrics", []),
                "red_flags": payload.get("red_flags", []),
                "data_gaps": payload.get("data_gaps", []),
            }
        if section == "legal":
            return {
                "active_matters": payload.get("active_matters", []),
                "sanctions_flags": payload.get("sanctions_flags", []),
                "regulatory_exposure": payload.get("regulatory_exposure", []),
                "red_flags": payload.get("red_flags", []),
                "data_gaps": payload.get("data_gaps", []),
            }
        if section == "news":
            return {
                "material_events": payload.get("material_events", []),
                "coverage_sentiment": payload.get("coverage_sentiment", ""),
                "coverage_density": payload.get("coverage_density", ""),
                "negative_signals": payload.get("negative_signals", []),
                "data_gaps": payload.get("data_gaps", []),
            }
        return {}

    @staticmethod
    def _empty_structured_data(section: str) -> dict[str, Any]:
        if section == "financials":
            return {"key_metrics": [], "red_flags": [], "data_gaps": []}
        if section == "legal":
            return {
                "active_matters": [],
                "sanctions_flags": [],
                "regulatory_exposure": [],
                "red_flags": [],
                "data_gaps": [],
            }
        if section == "news":
            return {
                "material_events": [],
                "coverage_sentiment": "",
                "coverage_density": "",
                "negative_signals": [],
                "data_gaps": [],
            }
        return {}

    def _section_synthesis_prompt(
        self,
        company_name: str,
        agent: DomainAgent,
        claims: list[VerifiedClaim],
    ) -> str:
        lines = [
            f"Company: {company_name}",
            f"Section: {agent.section}",
            f"Section meaning: {SECTION_DESCRIPTIONS.get(agent.section, agent.section)}",
            "",
            "Verified claims:",
        ]
        for claim in claims:
            lines.append(
                f"- Statement: {claim.statement} | Importance: {claim.importance} | "
                f"Confidence: {claim.confidence:.2f} | As of: {claim.as_of or 'unknown'} | "
                f"Evidence IDs: {', '.join(claim.evidence_ids)}"
            )
        return "\n".join(lines)

    def _executive_aggregation_prompt(
        self,
        *,
        request: ReportRequest,
        sections: list[ReportSection],
        risk_matrix: list[RiskMatrixEntry],
        publish_decision: str,
        publish_reason: str,
    ) -> str:
        lines = [
            f"Company: {request.company_name}",
            f"Jurisdiction: {request.primary_jurisdiction}",
            f"Goal: {request.report_goal or 'Generate an evidence-backed company report.'}",
            f"Publish decision: {publish_decision}",
            f"Publish reason: {publish_reason}",
            f"Requested PDF section order: {', '.join(build_pdf_section_order(sections))}",
            "",
            "Scope guidance:",
            "- The report should read like an in-depth diligence memo for investors, legal, compliance, or risk teams.",
            "- Keep section ordering consistent with the requested PDF structure.",
            "- State limitations clearly when evidence is weak or missing.",
            "- The appendix should be represented by the source log rather than narrative repetition.",
            "",
            "Section summaries:",
        ]
        for section in sections:
            lines.append(f"Section {section.name} ({section.status}): {section.summary}")
            for item in section.highlights:
                lines.append(f"- Highlight: {item}")
            for item in section.recommendations:
                lines.append(f"- Recommendation: {item}")
        lines.append("")
        lines.append("Risk matrix:")
        if not risk_matrix:
            lines.append("- No material risk entries were generated from verified claims.")
        for item in risk_matrix:
            lines.append(
                f"- Severity: {item.severity} | Section: {item.section} | Issue: {item.issue} | "
                f"Evidence IDs: {', '.join(item.evidence_ids)} | Recommendation: {item.recommendation}"
            )
        return "\n".join(lines)
