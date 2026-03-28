from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from gus_reporter.agents import DomainAgent, get_active_agents
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
)
from gus_reporter.schemas import (
    executive_report_schema,
    extraction_schema,
    financial_section_summary_schema,
    section_summary_schema,
    verification_schema,
)
from gus_reporter.storage import RunStorage
from gus_reporter.tinyfish_client import TinyfishClient


def _job_defaults(
    request: ReportRequest,
    default_browser_profile: str,
    *,
    source_type: str,
    category: str,
    proxy_country_code: str,
) -> dict[str, Any]:
    browser_profile = "lite" if category == "financials" else default_browser_profile
    return {
        "source_type": source_type,
        "browser_profile": browser_profile,
        "proxy_country_code": proxy_country_code,
        "preferred_run_mode": "async",
        "use_vault": request.tinyfish_use_vault,
        "credential_item_ids": list(request.tinyfish_credential_item_ids),
        "enable_agent_memory": request.tinyfish_enable_agent_memory
        or category == "financials",
        "allow_stealth_fallback": category == "financials",
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
    }
    return build_financial_tinyfish_goal(
        company_name=request.company_name,
        source_label=label,
        source_type=source_type_map.get(source_type, "financial_document"),
        ticker=request.ticker,
    )


def seed_source_jobs(request: ReportRequest, default_browser_profile: str) -> list[SourceJob]:
    if request.source_jobs:
        return request.source_jobs
    jobs: list[SourceJob] = []
    website = request.company_website.rstrip("/")
    proxy_country = request.primary_jurisdiction[:2].upper()
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
                        proxy_country_code=proxy_country,
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
                        proxy_country_code=proxy_country,
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
                        proxy_country_code=proxy_country,
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
                        proxy_country_code=proxy_country,
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
                        proxy_country_code=proxy_country,
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
                            proxy_country_code=proxy_country,
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
                            proxy_country_code=proxy_country,
                        ),
                    ),
                ]
            )
        for index, url in enumerate(request.financial_source_urls, start=1):
            jobs.append(
                SourceJob(
                    label=f"Financial source {index}",
                    category="financials",
                    url=url,
                    goal=build_financial_goal(
                        request,
                        source_type="financial_document",
                        label=f"Financial source {index}",
                        url=url,
                    ),
                    **_job_defaults(
                        request,
                        default_browser_profile,
                        source_type="financial_document",
                        category="financials",
                        proxy_country_code=proxy_country,
                    ),
                )
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
                        proxy_country_code="US",
                    ),
                )
            )
    return jobs


def normalize_tinyfish_runs(runs: list[TinyfishRunRecord]) -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []
    for index, run in enumerate(runs, start=1):
        normalized_payload = {
            "status": run.status,
            "source_type": run.source_type,
            "result": run.result,
            "error": run.error,
            "num_of_steps": run.num_of_steps,
            "browser_config": run.browser_config,
            "step_trace": [
                {
                    "timestamp": step.get("timestamp"),
                    "status": step.get("status"),
                    "action": step.get("action"),
                    "duration": step.get("duration"),
                }
                for step in run.steps[:20]
            ],
            "attempts": run.attempts,
        }
        serialized = json.dumps(
            normalized_payload, indent=2, sort_keys=True, ensure_ascii=True
        )
        evidence.append(
            EvidenceItem(
                evidence_id=f"evidence_{index:03d}",
                label=run.label,
                category=run.category,
                url=run.url,
                retrieved_at=run.finished_at or run.started_at or run.created_at or utc_now_iso(),
                content=serialized,
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


def build_risk_matrix(verified_claims: list[VerifiedClaim]) -> list[RiskMatrixEntry]:
    entries: list[RiskMatrixEntry] = []
    recommendations = {
        "legal": "Escalate to counsel and validate current case or enforcement status before using this report in a transaction.",
        "financials": "Request primary financial statements and reconcile any quantitative exposure before relying on the claim.",
        "news": "Check whether the event remains current and whether it materially changes the company narrative.",
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


class ReportPipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.storage = RunStorage(config.report_output_dir)
        self.tinyfish = TinyfishClient(
            api_key=config.tinyfish_api_key,
            base_url=config.tinyfish_base_url,
            poll_interval_seconds=config.tinyfish_poll_interval_seconds,
            poll_timeout_seconds=config.tinyfish_poll_timeout_seconds,
            batch_size=config.tinyfish_batch_size,
            retry_with_stealth=config.tinyfish_retry_with_stealth,
            request_max_retries=config.tinyfish_request_max_retries,
            request_retry_backoff_seconds=config.tinyfish_request_retry_backoff_seconds,
        )
        self.openai = OpenAIClient(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
            model=config.openai_model,
        )

    def run(self, request: ReportRequest) -> tuple[FinalReport, str]:
        run_dir = self.storage.create_run_dir(request.company_name)
        self.storage.write_json(run_dir, "request.json", request.to_dict())

        source_jobs = seed_source_jobs(
            request, self.config.tinyfish_default_browser_profile
        )
        self.storage.write_json(
            run_dir, "source_jobs.json", [job.to_dict() for job in source_jobs]
        )

        tinyfish_runs = self.tinyfish.run_jobs(source_jobs)
        self.storage.write_json(
            run_dir, "tinyfish_runs.json", [record.to_dict() for record in tinyfish_runs]
        )

        evidence = normalize_tinyfish_runs(tinyfish_runs)
        self.storage.write_json(
            run_dir, "evidence.json", [item.to_dict() for item in evidence]
        )

        extracted_claims = self._extract_claims(request, evidence)
        self.storage.write_json(
            run_dir,
            "extracted_claims.json",
            [claim.to_dict() for claim in extracted_claims],
        )

        verified_claims = self._verify_claims(extracted_claims, evidence)
        self.storage.write_json(
            run_dir,
            "verified_claims.json",
            [claim.to_dict() for claim in verified_claims],
        )

        final_report = self._build_report(request, verified_claims, evidence)
        self.storage.write_json(run_dir, "final_report.json", final_report.to_dict())
        return final_report, str(run_dir)

    def _extract_claims(
        self, request: ReportRequest, evidence: list[EvidenceItem]
    ) -> list[ExtractedClaim]:
        claims: list[ExtractedClaim] = []
        for agent in get_active_agents(request.focus_areas):
            agent_evidence = select_evidence_for_agent(agent, evidence)
            evidence_text = "\n\n".join(
                [
                    f"Evidence ID: {item.evidence_id}\n"
                    f"Label: {item.label}\n"
                    f"Category: {item.category}\n"
                    f"URL: {item.url}\n"
                    f"Retrieved at: {item.retrieved_at}\n"
                    f"Content:\n{item.content}"
                    for item in agent_evidence
                ]
            )
            payload = self.openai.structured_response(
                schema_name=f"{agent.section}_claim_extraction",
                schema=extraction_schema([agent.section]),
                system_prompt=(
                    f"{EXTRACTION_SYSTEM_PROMPT}\n\n"
                    f"Agent: {agent.name}\n"
                    f"Section: {agent.section}\n"
                    f"Instruction: {agent.extraction_instruction}"
                ),
                user_prompt=(
                    f"Company: {request.company_name}\n"
                    f"Jurisdiction: {request.primary_jurisdiction}\n"
                    f"Target section: {agent.section}\n"
                    f"Goal: {request.report_goal or 'Generate a source-backed diligence report.'}\n\n"
                    f"Evidence bundle:\n{evidence_text}"
                ),
            )
            for offset, claim in enumerate(payload.get("claims", []), start=1):
                claims.append(
                    ExtractedClaim(
                        claim_id=f"{agent.section}_{offset:03d}",
                        section=str(claim["section"]),
                        statement=str(claim["statement"]).strip(),
                        importance=str(claim["importance"]),
                        confidence=float(claim["confidence"]),
                        as_of=str(claim["as_of"]).strip(),
                        source_quote=str(claim["source_quote"]).strip(),
                        evidence_ids=[item.evidence_id for item in agent_evidence],
                        raw_source_label=agent.name,
                    )
                )
        return claims

    def _verify_claims(
        self, claims: list[ExtractedClaim], evidence: list[EvidenceItem]
    ) -> list[VerifiedClaim]:
        evidence_map = {item.evidence_id: item for item in evidence}
        verified: list[VerifiedClaim] = []
        for claim in claims:
            referenced = [evidence_map[eid] for eid in claim.evidence_ids if eid in evidence_map]
            evidence_text = "\n\n".join(
                [
                    f"Evidence ID: {item.evidence_id}\n"
                    f"Label: {item.label}\n"
                    f"URL: {item.url}\n"
                    f"Content:\n{item.content}"
                    for item in referenced
                ]
            )
            payload = self.openai.structured_response(
                schema_name="claim_verification",
                schema=verification_schema(),
                system_prompt=VERIFICATION_SYSTEM_PROMPT,
                user_prompt=(
                    f"Claim ID: {claim.claim_id}\n"
                    f"Section: {claim.section}\n"
                    f"Claim: {claim.statement}\n"
                    f"Quoted support from extraction: {claim.source_quote}\n\n"
                    f"Evidence bundle:\n{evidence_text}"
                ),
                max_output_tokens=1200,
            )
            verified.append(
                VerifiedClaim(
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
            )
        return verified

    def _build_report(
        self,
        request: ReportRequest,
        verified_claims: list[VerifiedClaim],
        evidence: list[EvidenceItem],
    ) -> FinalReport:
        supported_claims = [claim for claim in verified_claims if claim.status == "supported"]
        grouped: dict[str, list[VerifiedClaim]] = defaultdict(list)
        for claim in supported_claims:
            grouped[claim.section].append(claim)

        publish_decision, publish_reason = compute_publish_decision(
            request.focus_areas, verified_claims
        )
        sections = self._build_section_reports(request, grouped)
        risk_matrix = build_risk_matrix(verified_claims)
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

        source_log = [
            {
                "evidence_id": item.evidence_id,
                "label": item.label,
                "category": item.category,
                "source_type": item.metadata.get("tinyfish_source_type", ""),
                "url": item.url,
                "retrieved_at": item.retrieved_at,
                "tinyfish_status": item.metadata.get("tinyfish_status", ""),
                "tinyfish_streaming_url": item.metadata.get(
                    "tinyfish_streaming_url", ""
                ),
                "tinyfish_num_of_steps": item.metadata.get("tinyfish_num_of_steps"),
            }
            for item in evidence
        ]

        return FinalReport(
            company_name=request.company_name,
            generated_at=utc_now_iso(),
            publish_decision=publish_decision,
            publish_reason=publish_reason,
            executive_summary=str(aggregation_payload.get("executive_summary", "")).strip(),
            methodology=str(aggregation_payload.get("methodology", "")).strip(),
            entity_profile=str(aggregation_payload.get("entity_profile", "")).strip(),
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
            risk_matrix=risk_matrix,
            sections=sections,
            source_log=source_log,
            unsupported_claims=sum(
                1 for claim in verified_claims if claim.status != "supported"
            ),
            total_claims=len(verified_claims),
        )

    def _build_section_reports(
        self,
        request: ReportRequest,
        grouped_claims: dict[str, list[VerifiedClaim]],
    ) -> list[ReportSection]:
        sections: list[ReportSection] = []
        for agent in get_active_agents(request.focus_areas):
            claims = grouped_claims.get(agent.section, [])
            if claims:
                schema = (
                    financial_section_summary_schema()
                    if agent.section == "financials"
                    else section_summary_schema()
                )
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
                structured_data = {}
                if agent.section == "financials":
                    structured_data = {
                        "key_metrics": payload.get("key_metrics", []),
                        "red_flags": payload.get("red_flags", []),
                        "data_gaps": payload.get("data_gaps", []),
                    }
            else:
                summary = "Insufficient verified evidence for this section."
                highlights = []
                recommendations = [
                    "Add stronger primary sources for this section before relying on it in a decision."
                ]
                structured_data = (
                    {"key_metrics": [], "red_flags": [], "data_gaps": []}
                    if agent.section == "financials"
                    else {}
                )

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
        return sections

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
