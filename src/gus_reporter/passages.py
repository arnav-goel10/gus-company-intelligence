"""Pick the passages of a source that a claim needs, instead of the whole source.

Verification used to re-send every source a specialist saw with every claim,
truncated to its first 12,000 characters. Now each claim gets:

  - a window around every place its extraction quote occurs, anywhere in the
    source (so a quote past the old 12,000-character cut is now visible), and
  - the chunks that best match the claim's own words, so a contradiction
    stated away from the quote is still in front of the verifier.

When the quote occurs in no source text, the claim falls back to wider
context: more matching chunks, plus the PDFs whose text the pipeline never
extracted, since the quote may come from one of them.

Matching folds case and whitespace, the same rule the grounding check uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gus_reporter.models import EvidenceItem

QUOTE_WINDOW_CHARS = 1200  # kept on each side of a quote occurrence
CHUNK_CHARS = 800
CHUNK_OVERLAP_CHARS = 150
STATEMENT_CHUNKS = 2  # best-matching chunks added for every claim
FALLBACK_CHUNKS = 6  # best-matching chunks when the quote is nowhere in text

_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was "
    "were will with which who than then into over per not no".split()
)


def source_text(item: EvidenceItem) -> str:
    """The text the model would have been shown for this item."""
    return str(item.metadata.get("openai_input_text", "")).strip() or item.content


def is_unextracted_pdf(item: EvidenceItem) -> bool:
    return bool(
        item.metadata.get("openai_input_file_path") or item.metadata.get("openai_input_file_url")
    ) and not str(item.metadata.get("openai_input_text", "")).strip()


def _fold_with_map(text: str) -> tuple[str, list[int]]:
    """Lower-case, collapse whitespace, and remember where each folded char came from."""
    folded: list[str] = []
    origin: list[int] = []
    pending_space = False
    for index, char in enumerate(text):
        if char.isspace():
            pending_space = bool(folded)
            continue
        if pending_space:
            folded.append(" ")
            origin.append(index)
            pending_space = False
        folded.append(char.lower())
        origin.append(index)
    return "".join(folded), origin


def fold(text: str) -> str:
    return _fold_with_map(text)[0]


def quote_spans(text: str, quote: str) -> list[tuple[int, int]]:
    """Character spans in `text` where the folded quote occurs."""
    needle = fold(quote)
    if not needle:
        return []
    folded, origin = _fold_with_map(text)
    spans = []
    start = folded.find(needle)
    while start != -1:
        end = start + len(needle) - 1
        spans.append((origin[start], origin[end] + 1))
        start = folded.find(needle, start + 1)
    return spans


def _terms(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9$%.]+", text.lower())
        if len(token) > 2 and token not in _STOPWORDS
    }


@dataclass
class _Chunk:
    evidence_id: str
    start: int
    end: int
    score: int


def _chunks(item: EvidenceItem, terms: set[str]) -> list[_Chunk]:
    text = source_text(item)
    chunks = []
    step = CHUNK_CHARS - CHUNK_OVERLAP_CHARS
    for start in range(0, max(len(text), 1), step):
        end = min(len(text), start + CHUNK_CHARS)
        score = len(terms & _terms(text[start:end]))
        if score:
            chunks.append(_Chunk(item.evidence_id, start, end, score))
        if end == len(text):
            break
    return chunks


@dataclass
class ClaimContext:
    """What one claim needs from its sources."""

    spans: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    quote_in_text: bool = False
    pdf_ids: list[str] = field(default_factory=list)


def context_for_claim(statement: str, quote: str, items: list[EvidenceItem]) -> ClaimContext:
    context = ClaimContext()
    for item in items:
        text = source_text(item)
        for start, end in quote_spans(text, quote):
            context.quote_in_text = True
            context.spans.setdefault(item.evidence_id, []).append(
                (max(0, start - QUOTE_WINDOW_CHARS), min(len(text), end + QUOTE_WINDOW_CHARS))
            )

    terms = _terms(statement) | (set() if context.quote_in_text else _terms(quote))
    ranked = sorted(
        (chunk for item in items for chunk in _chunks(item, terms)),
        key=lambda chunk: -chunk.score,
    )  # stable sort keeps source order among ties
    for chunk in ranked[: STATEMENT_CHUNKS if context.quote_in_text else FALLBACK_CHUNKS]:
        context.spans.setdefault(chunk.evidence_id, []).append((chunk.start, chunk.end))

    if not context.quote_in_text:
        context.pdf_ids = [item.evidence_id for item in items if is_unextracted_pdf(item)]
    return context


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def render_passages(items: list[EvidenceItem], spans: dict[str, list[tuple[int, int]]]) -> str:
    """Evidence headers plus only the selected passages, in source order."""
    blocks = []
    for item in items:
        if item.evidence_id not in spans and not is_unextracted_pdf(item):
            continue
        text = source_text(item)
        lines = [
            f"Evidence ID: {item.evidence_id}",
            f"Label: {item.label}",
            f"URL: {item.url}",
            f"Source type: {item.metadata.get('source_type', item.metadata.get('tinyfish_source_type', ''))}",
        ]
        if is_unextracted_pdf(item) and item.evidence_id not in spans:
            lines.append("Content: attached PDF.")
        for start, end in merge_spans(spans.get(item.evidence_id, [])):
            lines.append(f"Passage (characters {start} to {end} of {len(text)}):")
            lines.append(
                ("..." if start else "") + text[start:end].strip() + ("..." if end < len(text) else "")
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
