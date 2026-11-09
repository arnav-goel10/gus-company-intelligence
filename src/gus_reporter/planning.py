"""Source planning: the deterministic first wave of browse jobs for a request.

Everything here is a pure function of the request. Later waves come from
discovery (gus_reporter.discovery), which reads what this wave found.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Callable

from gus_reporter.models import ReportRequest, SourceJob
from gus_reporter.prompts import (
    build_financial_tinyfish_goal,
    build_legal_tinyfish_goal,
    build_news_tinyfish_goal,
)
from gus_reporter.urls import build_search_url

# Categories whose jobs read deep documents (filings, dockets, articles): they
# always use agent memory and the standard browser profile.
_DEEP_ACQUISITION_CATEGORIES = {"financials", "legal", "news"}


def job_defaults(
    request: ReportRequest,
    default_browser_profile: str,
    *,
    source_type: str,
    category: str,
) -> dict[str, Any]:
    is_deep = category in _DEEP_ACQUISITION_CATEGORIES
    return {
        "source_type": source_type,
        "browser_profile": "lite" if is_deep else default_browser_profile,
        "preferred_run_mode": "async",
        "use_vault": request.tinyfish_use_vault,
        "credential_item_ids": list(request.tinyfish_credential_item_ids),
        "enable_agent_memory": request.tinyfish_enable_agent_memory or is_deep,
    }


# --------------------------------------------------------------------------
# Browse goals per category
# --------------------------------------------------------------------------

_FINANCIAL_GOAL_TYPES = {
    "financial_investor_relations": "investor_relations_hub",
    "financial_investor_candidate": "investor_relations_candidate",
    "financial_document": "financial_document",
    "financial_sec_filings": "official_filing_index",
    "financial_search_discovery": "search_discovery",
}
_LEGAL_GOAL_TYPES = {
    "legal_court_records": "court_docket_search",
    "legal_regulatory_filings": "regulatory_enforcement_search",
    "legal_enforcement": "regulatory_enforcement_search",
    "legal_sanctions": "sanctions_screening",
    "legal_corporate_registry": "corporate_registry_lookup",
    "legal_search_discovery": "legal_search_discovery",
    "legal_document": "legal_search_discovery",
}
_NEWS_GOAL_TYPES = {
    "news_aggregator": "news_aggregator_search",
    "news_press_release": "press_release_search",
    "news_industry_publication": "industry_publication_search",
    "news_search_discovery": "news_search_discovery",
    "news_document": "news_search_discovery",
}


def build_financial_goal(
    request: ReportRequest, *, source_type: str, label: str, url: str
) -> str:
    del url
    return build_financial_tinyfish_goal(
        company_name=request.company_name,
        source_label=label,
        source_type=_FINANCIAL_GOAL_TYPES.get(source_type, "financial_document"),
        ticker=request.ticker,
    )


def build_legal_goal(request: ReportRequest, *, source_type: str, label: str, url: str) -> str:
    del url
    return build_legal_tinyfish_goal(
        company_name=request.company_name,
        source_label=label,
        source_type=_LEGAL_GOAL_TYPES.get(source_type, "legal_search_discovery"),
        ticker=request.ticker,
        jurisdiction=request.primary_jurisdiction,
    )


def build_news_goal(request: ReportRequest, *, source_type: str, label: str, url: str) -> str:
    del url
    return build_news_tinyfish_goal(
        company_name=request.company_name,
        source_label=label,
        source_type=_NEWS_GOAL_TYPES.get(source_type, "news_search_discovery"),
    )


_GOAL_BUILDERS: dict[str, Callable[..., str]] = {
    "financials": build_financial_goal,
    "legal": build_legal_goal,
    "news": build_news_goal,
}

_WEBSITE_PAGES = (
    (
        "Official website",
        "company_overview",
        "",
        "company_homepage",
        "Extract the company overview, products, geographies, scale signals, and links to "
        "investor, leadership, careers, and newsroom pages. Respond in structured JSON if possible.",
    ),
    (
        "Leadership page",
        "leadership",
        "/leadership",
        "leadership_page",
        "Extract executives, founders, board members, and recent management changes. "
        "Respond in structured JSON if possible.",
    ),
    (
        "Careers page",
        "employees",
        "/careers",
        "careers_page",
        "Extract hiring trends, team footprint, open roles, and location signals. "
        "Respond in structured JSON if possible.",
    ),
    (
        "Newsroom",
        "news",
        "/news",
        "newsroom_page",
        "Extract recent company announcements, launches, expansions, funding, layoffs, "
        "investigations, or leadership changes. Respond in structured JSON if possible.",
    ),
)


def seed_source_jobs(request: ReportRequest, default_browser_profile: str) -> list[SourceJob]:
    if request.source_jobs:
        return request.source_jobs

    jobs: list[SourceJob] = []
    name = request.company_name
    website = request.company_website.rstrip("/")

    def add(label: str, category: str, url: str, source_type: str, goal: str = "") -> None:
        jobs.append(
            SourceJob(
                label=label,
                category=category,
                url=url,
                goal=goal
                or _GOAL_BUILDERS[category](
                    request, source_type=source_type, label=label, url=url
                ),
                **job_defaults(
                    request,
                    default_browser_profile,
                    source_type=source_type,
                    category=category,
                ),
            )
        )

    if website:
        for label, category, path, source_type, goal in _WEBSITE_PAGES:
            add(label, category, f"{website}{path}", source_type, goal)

    if "financials" in request.focus_areas:
        if request.investor_relations_url:
            add(
                "Investor relations",
                "financials",
                request.investor_relations_url,
                "financial_investor_relations",
            )
        elif website:
            add(
                "Investor relations candidate",
                "financials",
                f"{website}/investor-relations",
                "financial_investor_candidate",
            )
            add(
                "Investors candidate",
                "financials",
                f"{website}/investors",
                "financial_investor_candidate",
            )
        if request.ticker:
            add(
                "SEC filings",
                "financials",
                f"https://www.sec.gov/edgar/browse/?CIK={request.ticker}&owner=exclude",
                "financial_sec_filings",
            )
        has_any_hint = any(
            [
                request.company_website,
                request.investor_relations_url,
                request.financial_source_urls,
                request.ticker,
            ]
        )
        if not has_any_hint:
            for label, query in (
                (
                    "Financial source discovery",
                    f'"{name}" investor relations annual report quarterly results',
                ),
                ("Annual report discovery", f'"{name}" annual report pdf financial statements'),
                (
                    "Earnings and filings discovery",
                    f'"{name}" earnings release quarterly results filing',
                ),
            ):
                add(label, "financials", build_search_url(query), "financial_search_discovery")

    if "legal" in request.focus_areas:
        for label, query in (
            ("Litigation search", f'"{name}" lawsuit litigation court case filed'),
            ("SEC enforcement search", f'"{name}" SEC enforcement action investigation penalty'),
            ("Regulatory action search", f'"{name}" FTC DOJ EPA regulatory fine penalty settlement'),
            ("Sanctions screening", f'"{name}" OFAC sanctions SDN debarment'),
        ):
            add(label, "legal", build_search_url(query), "legal_search_discovery")
        add(
            "CourtListener search",
            "legal",
            f"https://www.courtlistener.com/?q=%22{urllib.parse.quote(name)}%22&type=o",
            "legal_court_records",
        )
        add(
            "OpenCorporates lookup",
            "legal",
            f"https://opencorporates.com/companies?q={urllib.parse.quote(name)}",
            "legal_corporate_registry",
        )
        if request.ticker:
            add(
                "SEC enforcement filings",
                "legal",
                build_search_url(
                    f'site:sec.gov "{name}" OR "{request.ticker}" '
                    "enforcement litigation administrative proceedings"
                ),
                "legal_search_discovery",
            )
        for index, url in enumerate(request.legal_source_urls, start=1):
            if url.strip():
                add(f"Legal source {index}", "legal", url.strip(), "legal_document")

    if "news" in request.focus_areas:
        add(
            "Recent news search",
            "news",
            f"https://www.bing.com/news/search?q={urllib.parse.quote_plus(name)}",
            "news_search_discovery",
        )
        add(
            "Negative news search",
            "news",
            build_search_url(
                f'"{name}" lawsuit layoff investigation scandal recall controversy'
            ),
            "news_search_discovery",
        )
        add(
            "Press release search",
            "news",
            build_search_url(
                f'"{name}" press release '
                "site:prnewswire.com OR site:businesswire.com OR site:globenewswire.com"
            ),
            "news_press_release",
        )
        add(
            "Industry coverage search",
            "news",
            build_search_url(f'"{name}" analysis report review coverage'),
            "news_search_discovery",
        )
        for index, url in enumerate(request.news_source_urls, start=1):
            if url.strip():
                add(f"News source {index}", "news", url.strip(), "news_document")

    return jobs
