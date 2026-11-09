"""Hand labelling: export a sheet, read it back, measure judge agreement.

The sheet is a CSV, one row per claim, with the claim, its quote and an
excerpt of each cited source. The judge's verdict is left out so it cannot
anchor the labeller. Fill the `label` column with one of

    supported | partially_supported | not_supported | cannot_tell

and optionally `note`. Rows with an empty label are ignored.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from gus_reporter.evaluation.citations import evidence_text
from gus_reporter.evaluation.judge import VERDICTS

SHEET_COLUMNS = (
    "run",
    "claim_id",
    "pipeline_status",
    "statement",
    "source_quote",
    "sources",
    "label",
    "note",
)
_EXCERPT_CHARS = 1500


def _excerpt(item: dict[str, Any], quote: str) -> str:
    text = " ".join(evidence_text(item).split())
    position = text.lower().find(" ".join(quote.split()).lower()) if quote else -1
    start = max(0, position - _EXCERPT_CHARS // 3) if position >= 0 else 0
    return f"[{item['evidence_id']}] {item['url']}\n{text[start:start + _EXCERPT_CHARS]}"


def label_rows(run_name: str, run_dir: Path, *, include_suppressed: bool) -> list[dict[str, str]]:
    claims = json.loads((run_dir / "verified_claims.json").read_text(encoding="utf-8"))
    evidence = {
        item["evidence_id"]: item
        for item in json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
    }
    rows = []
    for claim in claims:
        if claim["status"] != "supported" and not include_suppressed:
            continue
        cited = [evidence[eid] for eid in claim["evidence_ids"] if eid in evidence][:3]
        rows.append(
            {
                "run": run_name,
                "claim_id": claim["claim_id"],
                "pipeline_status": claim["status"],
                "statement": claim["statement"],
                "source_quote": claim["source_quote"],
                "sources": "\n\n".join(_excerpt(item, claim["source_quote"]) for item in cited),
                "label": "",
                "note": "",
            }
        )
    return rows


def write_sheet(rows: Iterable[dict[str, str]], target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return target


def read_labels(path: Path) -> dict[tuple[str, str], str]:
    labels: dict[tuple[str, str], str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            label = (row.get("label") or "").strip()
            if not label:
                continue
            if label not in VERDICTS:
                raise ValueError(f"{path}: unknown label {label!r} for {row['claim_id']}")
            labels[(row["run"], row["claim_id"])] = label
    return labels


def cohens_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Agreement beyond chance between two labellers over the same items."""
    if not pairs:
        return None
    total = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / total
    first = Counter(a for a, _ in pairs)
    second = Counter(b for _, b in pairs)
    expected = sum(first[k] * second[k] for k in set(first) | set(second)) / (total * total)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def agreement(
    human: dict[tuple[str, str], str], judge: dict[tuple[str, str], str]
) -> dict[str, Any]:
    shared = sorted(set(human) & set(judge))
    pairs = [(human[key], judge[key]) for key in shared]
    binary = [
        (h == "supported", j == "supported") for h, j in pairs
    ]
    return {
        "items": len(pairs),
        "exact_agreement": round(sum(h == j for h, j in pairs) / len(pairs), 3) if pairs else None,
        "kappa_4_way": None if not pairs else round(cohens_kappa(pairs) or 0.0, 3),
        "kappa_supported_vs_not": None
        if not pairs
        else round(cohens_kappa([(str(a), str(b)) for a, b in binary]) or 0.0, 3),
        "confusion": dict(Counter(f"human={h} judge={j}" for h, j in pairs)),
    }
