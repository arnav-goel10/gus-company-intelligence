"""Discovery: turn browse results into child source jobs and raw-fetch references.

A browse run returns the pages it visited and the documents those pages link
to. Discovery decides, per category, which of those URLs become new browse
jobs (the next acquisition wave) and which are fetched directly as raw
evidence. The three categories differ only in a handful of rules, captured in
one CategoryPolicy each.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterator

from gus_reporter.models import ReportRequest, SourceJob, TinyfishRunRecord
from gus_reporter.planning import (
    build_financial_goal,
    build_legal_goal,
    build_news_goal,
    job_defaults,
)
from gus_reporter.urls import domain_matches, hostname_of, normalize_url

# --------------------------------------------------------------------------
# Source-type heuristics
# --------------------------------------------------------------------------

_FINANCIAL_DOCUMENT_EXTENSIONS = (
    ".pdf", ".htm", ".html", ".txt", ".xml", ".json", ".csv", ".xls", ".xlsx",
)
_NEWS_WIRE_DOMAINS = ("prnewswire.com", "businesswire.com", "globenewswire.com")
_NEWS_AGGREGATOR_DOMAINS = (
    "reuters.com", "bloomberg.com", "wsj.com", "nytimes.com", "ft.com",
    "cnbc.com", "bbc.com", "apnews.com", "news.yahoo.com", "news.google.com",
)
_NEWS_INDUSTRY_DOMAINS = (
    "techcrunch.com", "theinformation.com", "semafor.com", "arstechnica.com",
    "theverge.com", "wired.com", "forbes.com", "fortune.com", "inc.com",
)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def classify_financial_source(*, source_kind: str, document_type: str, url: str) -> str:
    source_kind = source_kind.lower()
    document_type = document_type.lower()
    url_lower = url.lower()
    hostname = hostname_of(url_lower)

    if _contains_any(document_type, ("annual", "quarter", "earnings", "presentation", "filing")):
        return "financial_document"
    if url_lower.endswith(_FINANCIAL_DOCUMENT_EXTENSIONS):
        return "financial_document"
    if "investor" in source_kind:
        return "financial_investor_relations"
    if _contains_any(source_kind, ("filing", "sec", "edgar", "regulator")):
        return "financial_sec_filings"
    if "investor" in url_lower:
        return "financial_investor_relations"
    if domain_matches(hostname, "sec.gov") or "edgar" in url_lower:
        return "financial_sec_filings"
    return "financial_document"


def classify_legal_source(*, source_kind: str, document_type: str, url: str) -> str:
    del document_type  # every legal document type maps to legal_document
    source_kind = source_kind.lower()
    url_lower = url.lower()
    hostname = hostname_of(url_lower)

    if _contains_any(source_kind, ("court", "docket", "pacer", "case", "litigation")):
        return "legal_court_records"
    if _contains_any(source_kind, ("enforcement", "penalty", "fine", "settlement", "consent")):
        return "legal_enforcement"
    if _contains_any(source_kind, ("sanction", "ofac", "sdn", "debarment")):
        return "legal_sanctions"
    if _contains_any(source_kind, ("registry", "corporat", "secretary", "incorporation")):
        return "legal_corporate_registry"

    if any(
        domain_matches(hostname, domain)
        for domain in ("courtlistener.com", "pacer.gov", "uscourts.gov")
    ):
        return "legal_court_records"
    if domain_matches(hostname, "sec.gov") and "litigation" in url_lower:
        return "legal_enforcement"
    if any(domain_matches(hostname, d) for d in ("ftc.gov", "doj.gov")) or "enforcement" in url_lower:
        return "legal_enforcement"
    if (
        domain_matches(hostname, "treasury.gov") and ("ofac" in url_lower or "sdn" in url_lower)
    ) or "sanctions" in url_lower:
        return "legal_sanctions"
    if domain_matches(hostname, "opencorporates.com") or "sos." in hostname or "secretary" in url_lower:
        return "legal_corporate_registry"
    return "legal_document"


def classify_news_source(*, source_kind: str, document_type: str = "", url: str) -> str:
    del document_type  # news classification only looks at the source kind and host
    source_kind = source_kind.lower()
    hostname = hostname_of(url.lower())

    if any(domain_matches(hostname, domain) for domain in _NEWS_WIRE_DOMAINS):
        return "news_press_release"
    if _contains_any(source_kind, ("press_release", "press release", "wire")):
        return "news_press_release"
    if any(domain_matches(hostname, domain) for domain in _NEWS_AGGREGATOR_DOMAINS):
        return "news_aggregator"
    if any(domain_matches(hostname, domain) for domain in _NEWS_INDUSTRY_DOMAINS):
        return "news_industry_publication"
    if _contains_any(source_kind, ("news", "article", "report", "coverage")):
        return "news_aggregator"
    return "news_article"


# --------------------------------------------------------------------------
# Per-category policy
# --------------------------------------------------------------------------


def _financial_hints(request: ReportRequest) -> list[tuple[str, str, str]]:
    hints = []
    if request.investor_relations_url:
        hints.append(
            ("Investor relations", request.investor_relations_url, "financial_investor_relations")
        )
    hints.extend(
        (f"Financial source {index}", url, "financial_document")
        for index, url in enumerate(request.financial_source_urls, start=1)
    )
    return hints


def _stripped_hints(urls: list[str], label: str, source_type: str) -> list[tuple[str, str, str]]:
    return [
        (f"{label} {index}", url.strip(), source_type)
        for index, url in enumerate(urls, start=1)
        if url.strip()
    ]


@dataclass(frozen=True)
class CategoryPolicy:
    category: str
    classify: Callable[..., str]
    build_goal: Callable[..., str]
    # Label noun used for a discovered page that has no title.
    noun: str
    # Label used for a linked document that has no title.
    linked_label: str
    # Source types of a discovered page that are worth a new browse job;
    # None means every discovered page is.
    spawn_page_types: frozenset[str] | None
    # Whether documents linked from a page also become browse jobs.
    spawn_linked_documents: bool
    # Whether a linked document's own type replaces the page's source kind.
    linked_kind_from_document_type: bool
    # Whether the page's reporting period is carried onto raw refs.
    carries_reporting_period: bool
    # A run that returned no structured result is still fetched directly
    # when its seeded source type is one of these.
    raw_fetch_seeded_types: frozenset[str]
    request_hints: Callable[[ReportRequest], list[tuple[str, str, str]]]


FINANCIAL_POLICY = CategoryPolicy(
    category="financials",
    classify=classify_financial_source,
    build_goal=build_financial_goal,
    noun="financial source",
    linked_label="Linked financial document",
    spawn_page_types=frozenset({"financial_investor_relations", "financial_sec_filings"}),
    spawn_linked_documents=False,
    linked_kind_from_document_type=True,
    carries_reporting_period=True,
    raw_fetch_seeded_types=frozenset(
        {"financial_document", "financial_investor_relations", "financial_sec_filings"}
    ),
    request_hints=_financial_hints,
)

LEGAL_POLICY = CategoryPolicy(
    category="legal",
    classify=classify_legal_source,
    build_goal=build_legal_goal,
    noun="legal source",
    linked_label="Linked legal document",
    spawn_page_types=frozenset({"legal_court_records", "legal_enforcement", "legal_sanctions"}),
    spawn_linked_documents=True,
    linked_kind_from_document_type=True,
    carries_reporting_period=False,
    raw_fetch_seeded_types=frozenset({"legal_court_records", "legal_enforcement", "legal_document"}),
    request_hints=lambda request: _stripped_hints(
        request.legal_source_urls, "Legal source", "legal_document"
    ),
)

NEWS_POLICY = CategoryPolicy(
    category="news",
    classify=classify_news_source,
    build_goal=build_news_goal,
    noun="news source",
    linked_label="Linked news article",
    spawn_page_types=None,
    spawn_linked_documents=True,
    linked_kind_from_document_type=False,
    carries_reporting_period=False,
    raw_fetch_seeded_types=frozenset(),
    request_hints=lambda request: _stripped_hints(
        request.news_source_urls, "News source", "news_article"
    ),
)

POLICIES = (FINANCIAL_POLICY, LEGAL_POLICY, NEWS_POLICY)


# --------------------------------------------------------------------------
# Walking browse results
# --------------------------------------------------------------------------


@dataclass
class _Page:
    title: str
    final_url: str
    source_kind: str
    reporting_period: str
    notes: str
    linked: list[dict[str, Any]] | None


def _pages(run: TinyfishRunRecord) -> Iterator[_Page]:
    sources = run.result.get("sources") if isinstance(run.result, dict) else None
    if not isinstance(sources, list):
        return
    for source in sources:
        if not isinstance(source, dict):
            continue
        linked = source.get("linked_documents")
        yield _Page(
            title=str(source.get("page_title", "")).strip(),
            final_url=str(source.get("final_url", "")).strip(),
            source_kind=str(source.get("source_kind", "")).strip(),
            reporting_period=str(source.get("reporting_period", "")).strip(),
            notes=str(source.get("notes", "")).strip(),
            linked=[item for item in linked if isinstance(item, dict)]
            if isinstance(linked, list)
            else None,
        )


def _linked_fields(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("url", "")).strip(),
        str(item.get("title", "")).strip(),
        str(item.get("document_type", "")).strip(),
    )


def spawn_source_jobs(
    policy: CategoryPolicy,
    request: ReportRequest,
    runs: list[TinyfishRunRecord],
    default_browser_profile: str,
    existing_urls: set[str],
) -> list[SourceJob]:
    """New browse jobs for URLs this wave discovered. Mutates existing_urls."""
    spawned: list[SourceJob] = []

    def add(label: str, url: str, source_type: str) -> None:
        normalized = normalize_url(url)
        if not normalized or normalized in existing_urls:
            return
        existing_urls.add(normalized)
        spawned.append(
            SourceJob(
                label=label,
                category=policy.category,
                url=url,
                goal=policy.build_goal(request, source_type=source_type, label=label, url=url),
                **job_defaults(
                    request,
                    default_browser_profile,
                    source_type=source_type,
                    category=policy.category,
                ),
            )
        )

    for run in runs:
        if run.category != policy.category:
            continue
        for page in _pages(run):
            if page.final_url:
                source_type = policy.classify(
                    source_kind=page.source_kind, document_type="", url=page.final_url
                )
                if policy.spawn_page_types is None or source_type in policy.spawn_page_types:
                    add(
                        page.title or f"Discovered {page.source_kind or policy.noun}",
                        page.final_url,
                        source_type,
                    )
            if not policy.spawn_linked_documents or page.linked is None:
                continue
            for item in page.linked:
                doc_url, doc_title, doc_type = _linked_fields(item)
                if doc_url:
                    add(
                        doc_title or page.title or policy.linked_label,
                        doc_url,
                        policy.classify(
                            source_kind=page.source_kind, document_type=doc_type, url=doc_url
                        ),
                    )
    return spawned


def raw_source_refs(
    policy: CategoryPolicy,
    request: ReportRequest,
    runs: list[TinyfishRunRecord],
) -> list[dict[str, Any]]:
    """URLs to download directly: request hints, then everything browse runs found."""
    refs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    def add(
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
        normalized = normalize_url(url)
        if not normalized or normalized in seen_urls:
            return
        seen_urls.add(normalized)
        refs.append(
            {
                "label": label,
                "url": url,
                "source_type": source_type,
                "source_kind": source_kind,
                "reporting_period": reporting_period if policy.carries_reporting_period else "",
                "notes": notes,
                "discovery_origin": discovery_origin,
                "discovery_run_id": discovery_run_id,
            }
        )

    for label, url, source_type in policy.request_hints(request):
        add(label=label, url=url, source_type=source_type, discovery_origin="request_hint")

    for run in runs:
        if run.category != policy.category:
            continue
        if not isinstance(run.result, dict):
            if run.source_type in policy.raw_fetch_seeded_types:
                add(
                    label=run.label,
                    url=run.url,
                    source_type=run.source_type,
                    discovery_origin="tinyfish_seeded_url",
                    discovery_run_id=run.run_id,
                )
            continue
        for page in _pages(run):
            if page.final_url:
                add(
                    label=page.title or run.label,
                    url=page.final_url,
                    source_type=policy.classify(
                        source_kind=page.source_kind, document_type="", url=page.final_url
                    ),
                    source_kind=page.source_kind,
                    reporting_period=page.reporting_period,
                    notes=page.notes,
                    discovery_origin="tinyfish_discovery",
                    discovery_run_id=run.run_id,
                )
            for item in page.linked or []:
                doc_url, doc_title, doc_type = _linked_fields(item)
                if not doc_url:
                    continue
                add(
                    label=doc_title or page.title or policy.linked_label,
                    url=doc_url,
                    source_type=policy.classify(
                        source_kind=page.source_kind, document_type=doc_type, url=doc_url
                    ),
                    source_kind=(
                        (doc_type or page.source_kind)
                        if policy.linked_kind_from_document_type
                        else page.source_kind
                    ),
                    reporting_period=page.reporting_period,
                    notes=page.notes,
                    discovery_origin="tinyfish_linked_document",
                    discovery_run_id=run.run_id,
                )
    return refs


# Named entry points kept for readability at call sites and in tests.


def build_spawned_financial_source_jobs(
    request: ReportRequest, runs: list[TinyfishRunRecord], profile: str, existing: set[str]
) -> list[SourceJob]:
    return spawn_source_jobs(FINANCIAL_POLICY, request, runs, profile, existing)


def build_spawned_legal_source_jobs(
    request: ReportRequest, runs: list[TinyfishRunRecord], profile: str, existing: set[str]
) -> list[SourceJob]:
    return spawn_source_jobs(LEGAL_POLICY, request, runs, profile, existing)


def build_spawned_news_source_jobs(
    request: ReportRequest, runs: list[TinyfishRunRecord], profile: str, existing: set[str]
) -> list[SourceJob]:
    return spawn_source_jobs(NEWS_POLICY, request, runs, profile, existing)


def build_raw_financial_source_refs(
    request: ReportRequest, runs: list[TinyfishRunRecord]
) -> list[dict[str, Any]]:
    return raw_source_refs(FINANCIAL_POLICY, request, runs)


def build_raw_legal_source_refs(
    request: ReportRequest, runs: list[TinyfishRunRecord]
) -> list[dict[str, Any]]:
    return raw_source_refs(LEGAL_POLICY, request, runs)


def build_raw_news_source_refs(
    request: ReportRequest, runs: list[TinyfishRunRecord]
) -> list[dict[str, Any]]:
    return raw_source_refs(NEWS_POLICY, request, runs)
