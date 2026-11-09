"""Verification: every claim is re-judged against the evidence it came from.

The verifier can only keep or suppress. A claim whose verification call fails,
or that the model's answer leaves out, is recorded as insufficient_evidence,
so an outage suppresses claims rather than letting them through unchecked. A
claim the model calls supported must then pass ground_citation: its quote has
to appear in a source it cites. That check reads the full sources, not the
passages the model was shown.

Cost. Claims from the same specialist share one evidence bundle, so they are
verified together in batches of up to BATCH_SIZE. Each batch carries only the
passages its claims need (gus_reporter.passages). A bundle with PDFs whose
text was never extracted attaches them to every batch, as the old verifier
did per claim, but puts them first so later batches over the same PDFs hit
the prompt cache, and the first such batch per bundle runs before the rest
to warm that cache.
"""

from __future__ import annotations

import dataclasses
import re
import threading
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from gus_reporter.events import RunEvents
from gus_reporter.evidence import file_inputs
from gus_reporter.passages import context_for_claim, is_unextracted_pdf, render_passages
from gus_reporter.models import EvidenceItem, ExtractedClaim, VerifiedClaim
from gus_reporter.prompts import VERIFICATION_SYSTEM_PROMPT
from gus_reporter.providers import StructuredModel
from gus_reporter.schemas import verification_batch_schema


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _searchable_text(item: EvidenceItem) -> str:
    return f"{item.metadata.get('openai_input_text', '')}\n{item.content}"


def ground_citation(
    verified: VerifiedClaim, extracted: ExtractedClaim, evidence_map: dict[str, EvidenceItem]
) -> VerifiedClaim:
    """Deterministic check after the model says supported.

    The cited sources become the items, among those the claim was extracted
    from, that contain the extraction quote. A PDF whose text was never
    extracted cannot be searched, so it stays cited when the model named it.
    If no cited source contains the quote, the claim is suppressed.
    """
    allowed = [eid for eid in extracted.evidence_ids if eid in evidence_map]
    named = [eid for eid in verified.evidence_ids if eid in allowed] or allowed
    quote = _fold(extracted.source_quote)
    containing = [
        eid for eid in allowed if quote and quote in _fold(_searchable_text(evidence_map[eid]))
    ]
    if containing:
        preferred = [eid for eid in named if eid in containing]
        return dataclasses.replace(verified, evidence_ids=preferred or containing)
    pdfs = [eid for eid in named if is_unextracted_pdf(evidence_map[eid])]
    if quote and pdfs:
        return dataclasses.replace(verified, evidence_ids=pdfs)
    reason = (
        "Grounding check: the extraction quote does not appear in any source the claim was "
        "extracted from."
        if quote
        else "Grounding check: the claim has no supporting quote."
    )
    return dataclasses.replace(
        verified,
        status="unsupported",
        rationale=f"{reason} Model rationale: {verified.rationale}",
        blocking_issues=[*verified.blocking_issues, reason],
    )


BATCH_SIZE = 6
FALLBACK_PREFIX = "Verification fallback used after error"

BATCH_INSTRUCTIONS = """The evidence below is a set of passages selected for these claims, not whole documents.
Judge each claim on its own. Return exactly one verdict per claim id.
If the passages do not settle a claim, return insufficient_evidence for it."""


@dataclass
class _Batch:
    claims: list[ExtractedClaim]
    items: list[EvidenceItem]
    spans: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    pdf_ids: list[str] = field(default_factory=list)
    warms_cache: bool = False


def plan_batches(
    claims: list[ExtractedClaim],
    evidence_map: dict[str, EvidenceItem],
    batch_size: int = BATCH_SIZE,
) -> list[_Batch]:
    """Group claims by shared evidence, then split by whether they need PDFs."""
    groups: dict[tuple[str, ...], list[ExtractedClaim]] = {}
    for claim in claims:
        groups.setdefault(tuple(claim.evidence_ids), []).append(claim)

    batches: list[_Batch] = []
    for evidence_ids, group in groups.items():
        items = [evidence_map[eid] for eid in evidence_ids if eid in evidence_map]
        contexts = {
            claim.claim_id: context_for_claim(claim.statement, claim.source_quote, items)
            for claim in group
        }
        text_claims = [c for c in group if not contexts[c.claim_id].pdf_ids]
        pdf_claims = [c for c in group if contexts[c.claim_id].pdf_ids]
        for kind, members in (("text", text_claims), ("pdf", pdf_claims)):
            for offset in range(0, len(members), batch_size):
                chunk = members[offset : offset + batch_size]
                batch = _Batch(claims=chunk, items=items, warms_cache=kind == "pdf" and offset == 0)
                for claim in chunk:
                    context = contexts[claim.claim_id]
                    for eid, spans in context.spans.items():
                        batch.spans.setdefault(eid, []).extend(spans)
                    for eid in context.pdf_ids:
                        if eid not in batch.pdf_ids:
                            batch.pdf_ids.append(eid)
                batches.append(batch)
    return batches


def batch_user_content(batch: _Batch, run_dir: Path | None) -> list[dict[str, Any]]:
    """Stable parts first (attached PDFs), then passages, then the claims."""
    pdfs = [item for item in batch.items if item.evidence_id in batch.pdf_ids]
    shown = [
        item for item in batch.items if item.evidence_id in batch.spans or item.evidence_id in batch.pdf_ids
    ]
    claims_text = "\n\n".join(
        "\n".join(
            [
                f"Claim ID: {claim.claim_id}",
                f"Section: {claim.section}",
                f"Claim: {claim.statement}",
                f"Quoted support from extraction: {claim.source_quote}",
            ]
        )
        for claim in batch.claims
    )
    return [
        *file_inputs(pdfs, run_dir),
        {"type": "input_text", "text": "Evidence passages:\n\n" + render_passages(shown, batch.spans)},
        {"type": "input_text", "text": f"{BATCH_INSTRUCTIONS}\n\nClaims to verify:\n\n{claims_text}"},
    ]


def _verified_from(claim: ExtractedClaim, verdict: dict[str, Any]) -> VerifiedClaim:
    return VerifiedClaim(
        claim_id=claim.claim_id,
        section=claim.section,
        statement=claim.statement,
        status=str(verdict["status"]),
        confidence=float(verdict["confidence"]),
        rationale=str(verdict["rationale"]).strip(),
        evidence_ids=[str(item) for item in verdict.get("supporting_evidence_ids", [])]
        or claim.evidence_ids,
        blocking_issues=[str(item) for item in verdict.get("blocking_issues", [])],
        source_quote=claim.source_quote,
        importance=claim.importance,
        as_of=claim.as_of,
    )


def _fallback(claim: ExtractedClaim, reason: str) -> VerifiedClaim:
    return VerifiedClaim(
        claim_id=claim.claim_id,
        section=claim.section,
        statement=claim.statement,
        status="insufficient_evidence",
        confidence=0.0,
        rationale=f"{FALLBACK_PREFIX}: {reason}",
        evidence_ids=claim.evidence_ids,
        blocking_issues=[reason],
        source_quote=claim.source_quote,
        importance=claim.importance,
        as_of=claim.as_of,
    )


class ClaimVerifier:
    def __init__(
        self,
        *,
        llm: StructuredModel,
        events: RunEvents,
        max_workers: int,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self.llm = llm
        self.events = events
        self.max_workers = max_workers
        self.batch_size = max(1, batch_size)

    def verify(
        self, claims: list[ExtractedClaim], evidence: list[EvidenceItem], run_dir: Path
    ) -> list[VerifiedClaim]:
        evidence_map = {item.evidence_id: item for item in evidence}
        results: dict[str, VerifiedClaim] = {}
        total_claims = len(claims)
        lock = threading.Lock()

        self.events.stage(
            stage="verification",
            status="running",
            message="Verifying extracted claims against evidence.",
            current=0,
            total=total_claims,
        )

        def verify_batch(batch: _Batch) -> None:
            try:
                payload = self.llm.structured_response(
                    schema_name="claim_verification_batch",
                    schema=verification_batch_schema([c.claim_id for c in batch.claims]),
                    system_prompt=VERIFICATION_SYSTEM_PROMPT,
                    user_content=batch_user_content(batch, run_dir),
                    # The old verifier allowed 1,200 tokens per claim; keep that.
                    max_output_tokens=1200 * len(batch.claims),
                )
                verdicts: dict[str, dict[str, Any]] = {}
                for verdict in payload.get("verdicts", []):
                    verdicts.setdefault(str(verdict.get("claim_id", "")), verdict)
                error = ""
            except Exception as exc:
                verdicts, error = {}, str(exc).strip()

            for claim in batch.claims:
                verdict = verdicts.get(claim.claim_id)
                if verdict is None:
                    verified_claim = _fallback(claim, error or "claim missing from the batch answer")
                else:
                    try:
                        verified_claim = _verified_from(claim, verdict)
                    except (KeyError, TypeError, ValueError) as exc:
                        verified_claim = _fallback(claim, f"malformed verdict: {exc}")
                if verified_claim.status == "supported":
                    verified_claim = ground_citation(verified_claim, claim, evidence_map)
                with lock:
                    results[claim.claim_id] = verified_claim
                    self.events.emit(
                        {
                            "event": "verification_update",
                            "stage": "verification",
                            "status": "running",
                            "message": f"Verified {claim.claim_id}.",
                            "current": len(results),
                            "total": total_claims,
                            "claim_id": claim.claim_id,
                            "claim_status": verified_claim.status,
                        }
                    )

        batches = plan_batches(claims, evidence_map, self.batch_size)
        warm = [batch for batch in batches if batch.warms_cache]
        rest = [batch for batch in batches if not batch.warms_cache]
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(batches) or 1)) as executor:
            # The first PDF batch of each bundle runs first so its prefix is
            # cached before the bundle's other PDF batches are sent.
            for future in [executor.submit(verify_batch, batch) for batch in warm]:
                future.result()
            for future in [executor.submit(verify_batch, batch) for batch in rest]:
                future.result()

        self.events.stage(
            stage="verification",
            status="completed",
            message=f"Verification finished for {len(results)} claims.",
            current=len(results),
            total=total_claims,
        )
        return [results[claim.claim_id] for claim in claims]
