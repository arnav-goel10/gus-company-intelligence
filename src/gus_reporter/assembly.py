"""Assembly: section summaries, risk matrix and the publish decision.

Only claims the verifier marked supported reach the report. The publish
decision blocks the whole report when coverage is too thin or when more than
half of all extracted claims failed verification.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from gus_reporter.agents import DomainAgent, get_active_agents
from gus_reporter.events import RunEvents
from gus_reporter.models import (
    EvidenceItem,
    FinalReport,
    ReportRequest,
    ReportSection,
    RiskMatrixEntry,
    VerifiedClaim,
    utc_now_iso,
)
from gus_reporter.prompts import (
    EXECUTIVE_AGGREGATION_SYSTEM_PROMPT,
    REPORT_SYNTHESIS_SYSTEM_PROMPT,
    SECTION_DESCRIPTIONS,
)
from gus_reporter.providers import StructuredModel
from gus_reporter.schemas import (
    executive_report_schema,
    financial_section_summary_schema,
    legal_section_summary_schema,
    news_section_summary_schema,
    section_summary_schema,
)


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


class ReportAssembler:
    def __init__(self, *, llm: StructuredModel, events: RunEvents) -> None:
        self.llm = llm
        self.events = events

    def assemble(
        self,
        request: ReportRequest,
        verified_claims: list[VerifiedClaim],
        evidence: list[EvidenceItem],
    ) -> FinalReport:
        self.events.stage(
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
            aggregation_payload = self.llm.structured_response(
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
        self.events.stage(
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
            self.events.emit(
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
                    payload = self.llm.structured_response(
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
            self.events.emit(
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
        self.events.stage(
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
