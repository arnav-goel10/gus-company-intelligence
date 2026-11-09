"""Verification: every claim is re-judged against the evidence it cites.

The verifier can only keep or suppress. A claim whose verification call fails
is recorded as insufficient_evidence, so an outage suppresses claims rather
than letting them through unchecked.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gus_reporter.events import RunEvents
from gus_reporter.evidence import _build_openai_user_content
from gus_reporter.models import EvidenceItem, ExtractedClaim, VerifiedClaim
from gus_reporter.prompts import VERIFICATION_SYSTEM_PROMPT
from gus_reporter.providers import StructuredModel
from gus_reporter.schemas import verification_schema


class ClaimVerifier:
    def __init__(self, *, llm: StructuredModel, events: RunEvents, max_workers: int) -> None:
        self.llm = llm
        self.events = events
        self.max_workers = max_workers

    def verify(
        self, claims: list[ExtractedClaim], evidence: list[EvidenceItem], run_dir: Path
    ) -> list[VerifiedClaim]:
        evidence_map = {item.evidence_id: item for item in evidence}
        verified: list[VerifiedClaim] = []
        total_claims = len(claims)
        lock = threading.Lock()

        self.events.stage(
            stage="verification",
            status="running",
            message="Verifying extracted claims against evidence.",
            current=0,
            total=total_claims,
        )

        def verify_single(claim: ExtractedClaim) -> VerifiedClaim:
            referenced = [evidence_map[eid] for eid in claim.evidence_ids if eid in evidence_map]
            try:
                payload = self.llm.structured_response(
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
                    blocking_issues=[str(item) for item in payload.get("blocking_issues", [])],
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
                self.events.emit(
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
            max_workers=min(self.max_workers, total_claims or 1)
        ) as executor:
            futures = [executor.submit(verify_single, claim) for claim in claims]
            ordered = [future.result() for future in futures]

        self.events.stage(
            stage="verification",
            status="completed",
            message=f"Verification finished for {len(verified)} claims.",
            current=len(verified),
            total=total_claims,
        )
        return ordered
