"""Render a finished run as one Markdown file a person can read top to bottom.

The report shows every kept claim next to the source it cites, and every
suppressed claim with the verifier's reason, so a reader can audit what the
pipeline refused to say as easily as what it said.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SUPPORTED = "supported"


def _load(run_dir: Path, name: str) -> Any:
    return json.loads((run_dir / name).read_text(encoding="utf-8"))


def _cell(text: Any) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def render_markdown(run_dir: Path) -> str:
    run_dir = Path(run_dir)
    report = _load(run_dir, "final_report.json")
    claims = _load(run_dir, "verified_claims.json")
    evidence = {item["evidence_id"]: item for item in _load(run_dir, "evidence.json")}

    kept = [claim for claim in claims if claim["status"] == SUPPORTED]
    suppressed = [claim for claim in claims if claim["status"] != SUPPORTED]

    def cite(ids: list[str]) -> str:
        parts = []
        for evidence_id in ids:
            item = evidence.get(evidence_id)
            if item is None:
                parts.append(f"`{evidence_id}` (not in evidence log)")
            else:
                parts.append(f"[`{evidence_id}`]({item['url']})")
        return ", ".join(parts) or "none"

    lines = [
        f"# {report['company_name']}: company intelligence report",
        "",
        f"**Publish decision: {report['publish_decision']}.** {report['publish_reason']}",
        "",
        f"{len(kept)} claims kept, {len(suppressed)} suppressed by verification, "
        f"{len(evidence)} sources captured.",
        "",
        "## Executive summary",
        "",
        report["executive_summary"],
        "",
    ]
    if report.get("key_findings"):
        lines += ["## Key findings", ""]
        lines += [f"- {item}" for item in report["key_findings"]]
        lines.append("")
    if report.get("risk_matrix"):
        lines += ["## Risk matrix", "", "| Severity | Section | Issue | Evidence |", "|---|---|---|---|"]
        for entry in report["risk_matrix"]:
            lines.append(
                f"| {entry['severity']} | {entry['section']} | {_cell(entry['issue'])} | "
                f"{cite(entry['evidence_ids'])} |"
            )
        lines.append("")

    lines += ["## Sections", ""]
    for section in report["sections"]:
        lines += [f"### {section['name']} ({section['status']})", "", section["summary"], ""]
        section_claims = [claim for claim in kept if claim["section"] == section["name"]]
        for claim in section_claims:
            lines.append(f"- {claim['statement']}")
            if claim.get("source_quote"):
                lines.append(f"  > {claim['source_quote']}")
            lines.append(f"  Source: {cite(claim['evidence_ids'])}")
        if section_claims:
            lines.append("")

    lines += [
        "## Suppressed claims",
        "",
        "Extracted by an agent, then rejected by the verifier. None of these reach the "
        "sections above.",
        "",
        "| Claim | Status | Verifier reason |",
        "|---|---|---|",
    ]
    for claim in suppressed:
        lines.append(
            f"| {_cell(claim['statement'])} | {claim['status']} | {_cell(claim['rationale'])} |"
        )
    if not suppressed:
        lines.append("| (none) | | |")
    lines.append("")

    lines += ["## Source log", "", "| Evidence | Category | URL |", "|---|---|---|"]
    for item in report["source_log"]:
        lines.append(f"| `{item['evidence_id']}` | {item['category']} | {item['url']} |")
    lines.append("")
    return "\n".join(lines)


def write_markdown(run_dir: Path) -> Path:
    target = Path(run_dir) / "report.md"
    target.write_text(render_markdown(Path(run_dir)), encoding="utf-8")
    return target
