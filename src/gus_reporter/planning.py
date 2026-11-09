"""Source planning: the deterministic first wave of source jobs for a request."""

from __future__ import annotations

import urllib.parse
from typing import Any

from gus_reporter.models import ReportRequest, SourceJob
from gus_reporter.prompts import (
    build_financial_tinyfish_goal,
    build_legal_tinyfish_goal,
    build_news_tinyfish_goal,
)
from gus_reporter.urls import _build_search_url


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
