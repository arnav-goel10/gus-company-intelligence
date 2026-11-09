"""Deterministic citation checks: no model, no judgement, just string facts.

For each claim:
  ids_resolve     every evidence id it cites exists in the run's evidence log
  quote_grounded  its extraction quote appears (whitespace and case folded)
                  in the text of at least one cited item
  pdf_only        its only cited sources are PDFs whose text the pipeline
                  never extracted, so grounding cannot be checked here
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def evidence_text(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    return f"{metadata.get('openai_input_text', '')}\n{item.get('content', '')}"


def is_unextracted_pdf(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") or {}
    return bool(metadata.get("openai_input_file_path") or metadata.get("openai_input_file_url")) and not (
        str(metadata.get("openai_input_text", "")).strip()
    )


@dataclass
class CitationCheck:
    claim_id: str
    cited: int
    ids_resolve: bool
    quote_present: bool
    quote_grounded: bool
    pdf_only: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_claim(claim: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> CitationCheck:
    cited_ids = list(claim.get("evidence_ids") or [])
    cited = [evidence_by_id[eid] for eid in cited_ids if eid in evidence_by_id]
    quote = _fold(str(claim.get("source_quote", "")))
    grounded = bool(quote) and any(quote in _fold(evidence_text(item)) for item in cited)
    return CitationCheck(
        claim_id=str(claim["claim_id"]),
        cited=len(cited_ids),
        ids_resolve=bool(cited_ids) and len(cited) == len(cited_ids),
        quote_present=bool(quote),
        quote_grounded=grounded,
        pdf_only=bool(cited) and all(is_unextracted_pdf(item) for item in cited),
    )
