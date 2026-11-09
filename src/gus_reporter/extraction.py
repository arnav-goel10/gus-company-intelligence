"""Extraction: each specialist agent reads its slice of evidence and emits typed claims."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gus_reporter.agents import (
    DomainAgent,
    ExtractionAgent,
    get_active_agents,
    get_extraction_agents,
)
from gus_reporter.events import RunEvents
from gus_reporter.evidence import (
    _build_openai_user_content,
    select_evidence_for_agent,
    select_evidence_for_extraction_agent,
)
from gus_reporter.models import EvidenceItem, ExtractedClaim, ReportRequest
from gus_reporter.prompts import EXTRACTION_SYSTEM_PROMPT
from gus_reporter.providers import StructuredModel
from gus_reporter.schemas import extraction_schema


class ClaimExtractor:
    """Fans out one model call per specialist; a failed specialist yields no claims."""

    def __init__(self, *, llm: StructuredModel, events: RunEvents, max_workers: int) -> None:
        self.llm = llm
        self.events = events
        self.max_workers = max_workers

    def extract(
        self, request: ReportRequest, evidence: list[EvidenceItem], run_dir: Path
    ) -> list[ExtractedClaim]:
        claims: list[ExtractedClaim] = []
        extraction_units = [
            specialist
            for agent in get_active_agents(request.focus_areas)
            for specialist in get_extraction_agents(agent)
        ]
        total_specialists = len(extraction_units)
        progress = {"started": 0, "completed": 0}
        lock = threading.Lock()

        self.events.stage(
            stage="extraction",
            status="running",
            message="Extracting structured claims from evidence bundles.",
            current=0,
            total=total_specialists,
        )

        def finish(
            agent: DomainAgent,
            specialist: ExtractionAgent,
            *,
            status: str,
            message: str,
            claims_extracted: int,
        ) -> None:
            with lock:
                progress["completed"] += 1
                self.events.emit(
                    {
                        "event": "specialist_update",
                        "stage": "extraction",
                        "status": status,
                        "section": agent.section,
                        "specialist": specialist.name,
                        "message": message,
                        "current": progress["completed"],
                        "total": total_specialists,
                        "claims_extracted": claims_extracted,
                    }
                )
                self.events.stage(
                    stage="extraction",
                    status="running",
                    message="Extracting structured claims from evidence bundles.",
                    current=progress["completed"],
                    total=total_specialists,
                )

        def process_specialist(
            agent: DomainAgent, specialist: ExtractionAgent
        ) -> list[ExtractedClaim]:
            with lock:
                progress["started"] += 1
                current = progress["started"]
            self.events.emit(
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
                payload = self.llm.structured_response(
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
                finish(
                    agent,
                    specialist,
                    status="failed",
                    message=f"{specialist.name} failed and was skipped: {str(exc).strip()}",
                    claims_extracted=0,
                )
                return []

            extracted = [
                ExtractedClaim(
                    claim_id=f"{agent.section}_{specialist.name}_{offset:03d}",
                    section=str(claim["section"]),
                    statement=str(claim["statement"]).strip(),
                    importance=str(claim["importance"]),
                    confidence=float(claim["confidence"]),
                    as_of=str(claim["as_of"]).strip(),
                    source_quote=str(claim["source_quote"]).strip(),
                    evidence_ids=[item.evidence_id for item in specialist_evidence],
                    raw_source_label=specialist.name,
                )
                for offset, claim in enumerate(payload.get("claims", []), start=1)
            ]
            claim_count = len(payload.get("claims", []))
            finish(
                agent,
                specialist,
                status="completed",
                message=f"{specialist.name} extracted {claim_count} claims.",
                claims_extracted=claim_count,
            )
            return extracted

        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, total_specialists or 1)
        ) as executor:
            futures = [
                executor.submit(process_specialist, agent, specialist)
                for agent in get_active_agents(request.focus_areas)
                for specialist in get_extraction_agents(agent)
            ]
            # Collect in submission order so the claim list, and every
            # artifact derived from it, is identical across runs.
            for future in futures:
                claims.extend(future.result())

        self.events.stage(
            stage="extraction",
            status="completed",
            message=f"Extracted {len(claims)} raw claims.",
            current=total_specialists,
            total=total_specialists,
        )
        return claims
