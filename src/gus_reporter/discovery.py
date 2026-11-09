"""Discovery: turn browse results into child source jobs and raw-fetch references."""

from __future__ import annotations

from typing import Any

from gus_reporter.models import ReportRequest, SourceJob, TinyfishRunRecord
from gus_reporter.planning import (
    _job_defaults,
    build_financial_goal,
    build_legal_goal,
    build_news_goal,
)
from gus_reporter.urls import _domain_matches, _extract_hostname, _normalize_url


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
