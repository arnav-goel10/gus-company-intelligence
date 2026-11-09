"""LLM judge: does the cited source actually support the claim?

The judge sees the claim and only the sources the claim cites, never the
pipeline's own verdict or rationale. It must return the sentence that
supports the claim; a "supported" verdict whose sentence is not found in the
cited text is downgraded to "not_supported", so the judge cannot pass a claim
by paraphrasing from memory.

Use a different, stronger model than the pipeline's verifier, and check the
judge against hand labels (gus_reporter.evaluation.labels) before trusting it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from gus_reporter.evaluation.citations import evidence_text
from gus_reporter.evidence import _build_openai_user_content
from gus_reporter.models import EvidenceItem
from gus_reporter.providers import StructuredModel

VERDICTS = ("supported", "partially_supported", "not_supported", "cannot_tell")
MAX_SOURCES_PER_CLAIM = 6

JUDGE_SYSTEM_PROMPT = """You audit claims in an automated company report.

You get one claim and the source documents it cites. Decide, using only those
documents, whether they support the claim.

- supported: every factual part of the claim (entity, number, date, direction) is stated in a source.
- partially_supported: the core is stated but a detail (number, date, qualifier) is missing or differs.
- not_supported: the sources do not state it, or state something different.
- cannot_tell: the sources are unreadable or empty.

Do not use outside knowledge, even if you know the claim is true.
supporting_sentence must be copied verbatim from a source, or empty.
"""


def judge_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "supporting_sentence": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": ["verdict", "supporting_sentence", "rationale"],
    }


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _sources_for(
    claim: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    cited = [evidence_by_id[eid] for eid in claim.get("evidence_ids", []) if eid in evidence_by_id]
    quote = _fold(str(claim.get("source_quote", "")))
    # Suppressed claims may cite a whole specialist bundle; show the items that
    # contain the quote first and cap the rest.
    cited.sort(key=lambda item: 0 if quote and quote in _fold(evidence_text(item)) else 1)
    return cited[:MAX_SOURCES_PER_CLAIM]


def judge_claim(
    model: StructuredModel,
    claim: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    run_dir: Path,
) -> dict[str, Any]:
    sources = _sources_for(claim, evidence_by_id)
    content = _build_openai_user_content(
        preamble=f"Claim: {claim['statement']}",
        evidence=[EvidenceItem(**item) for item in sources],
        run_dir=run_dir,
    )
    payload = model.structured_response(
        schema_name="claim_judgement",
        schema=judge_schema(),
        system_prompt=JUDGE_SYSTEM_PROMPT,
        user_content=content,
        max_output_tokens=800,
    )
    verdict = str(payload.get("verdict", "cannot_tell"))
    sentence = str(payload.get("supporting_sentence", ""))
    corpus = _fold(" ".join(evidence_text(item) for item in sources))
    sentence_found = bool(sentence.strip()) and _fold(sentence) in corpus
    has_pdf = any(
        (item.get("metadata") or {}).get("openai_input_file_path") for item in sources
    )
    downgraded = verdict == "supported" and not sentence_found and not has_pdf
    return {
        "claim_id": claim["claim_id"],
        "pipeline_status": claim["status"],
        "verdict": "not_supported" if downgraded else verdict,
        "raw_verdict": verdict,
        "supporting_sentence": sentence,
        "sentence_found_in_sources": sentence_found,
        "downgraded": downgraded,
        "rationale": str(payload.get("rationale", "")),
        "sources_shown": [item["evidence_id"] for item in sources],
    }
