"""Score finished runs: suppression, citation validity, kept-claim precision.

Precision comes from judge verdicts or, when given, hand labels (labels win
where both exist). Every rate carries a Wilson 95% interval because the
counts per run are small.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from gus_reporter.evaluation.citations import check_claim

FALLBACK_PREFIX = "Verification fallback used after error"


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    if total == 0:
        return None
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3))


def _rate(successes: int, total: int) -> dict[str, Any]:
    return {
        "n": total,
        "k": successes,
        "rate": round(successes / total, 3) if total else None,
        "ci95": wilson(successes, total),
    }


def load_run(run_dir: Path) -> dict[str, Any]:
    def read(name: str) -> Any:
        path = run_dir / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    judgements_path = run_dir / "judgements.jsonl"
    judgements = (
        [json.loads(line) for line in judgements_path.read_text(encoding="utf-8").splitlines() if line]
        if judgements_path.exists()
        else []
    )
    return {
        "claims": read("verified_claims.json") or [],
        "evidence": read("evidence.json") or [],
        "report": read("final_report.json") or {},
        "browse": read("tinyfish_runs.json") or [],
        "judgements": judgements,
    }


def score_run(
    name: str,
    kind: str,
    run_dir: Path,
    labels: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    data = load_run(run_dir)
    claims = data["claims"]
    evidence_by_id = {item["evidence_id"]: item for item in data["evidence"]}
    kept = [claim for claim in claims if claim["status"] == "supported"]
    suppressed = [claim for claim in claims if claim["status"] != "supported"]
    outages = [claim for claim in suppressed if str(claim["rationale"]).startswith(FALLBACK_PREFIX)]
    checks = [check_claim(claim, evidence_by_id) for claim in kept]

    verdicts = {j["claim_id"]: j["verdict"] for j in data["judgements"]}
    for (run, claim_id), label in (labels or {}).items():
        if run == name:
            verdicts[claim_id] = label
    judged_kept = [verdicts[c["claim_id"]] for c in kept if c["claim_id"] in verdicts]
    judged_suppressed = [
        verdicts[c["claim_id"]] for c in suppressed if c["claim_id"] in verdicts and c not in outages
    ]

    return {
        "run": name,
        "kind": kind,
        "publish_decision": data["report"].get("publish_decision", ""),
        "browse_jobs": len(data["browse"]),
        "browse_blocked": sum(1 for run in data["browse"] if run.get("access_blocked")),
        "evidence_items": len(data["evidence"]),
        "claims_extracted": len(claims),
        "claims_kept": len(kept),
        "claims_suppressed": len(suppressed),
        "suppressed_by_outage": len(outages),
        "status_counts": {
            status: sum(1 for c in claims if c["status"] == status)
            for status in sorted({c["status"] for c in claims})
        },
        "citations": {
            "ids_resolve": sum(c.ids_resolve for c in checks),
            "quote_grounded": sum(c.quote_grounded for c in checks),
            "pdf_only": sum(c.pdf_only for c in checks),
            "mean_sources_cited": round(sum(c.cited for c in checks) / len(checks), 2)
            if checks
            else None,
        },
        "kept_judged": len(judged_kept),
        "kept_supported": judged_kept.count("supported"),
        "kept_partial": judged_kept.count("partially_supported"),
        "suppressed_judged": len(judged_suppressed),
        "suppressed_but_supported": judged_suppressed.count("supported"),
    }


def aggregate(per_run: list[dict[str, Any]]) -> dict[str, Any]:
    real = [r for r in per_run if r["kind"] == "real"]
    fictitious = [r for r in per_run if r["kind"] == "fictitious"]

    def total(rows: list[dict[str, Any]], key: str) -> int:
        return sum(int(r[key]) for r in rows)

    kept = total(real, "claims_kept")
    citations_total = {
        key: sum(r["citations"][key] for r in real) for key in ("ids_resolve", "quote_grounded", "pdf_only")
    }
    judged = total(real, "kept_judged")
    return {
        "runs": len(per_run),
        "real_runs": len(real),
        "fictitious_runs": len(fictitious),
        "real": {
            "claims_extracted": total(real, "claims_extracted"),
            "claims_kept": kept,
            "suppression_rate": _rate(total(real, "claims_suppressed"), total(real, "claims_extracted")),
            "suppressed_by_outage": total(real, "suppressed_by_outage"),
            "kept_claim_precision_strict": _rate(total(real, "kept_supported"), judged),
            "kept_claim_precision_lenient": _rate(
                total(real, "kept_supported") + total(real, "kept_partial"), judged
            ),
            "kept_claims_judged": judged,
            "citation_ids_resolve": _rate(citations_total["ids_resolve"], kept),
            "citation_quote_grounded": _rate(citations_total["quote_grounded"], kept),
            "citation_pdf_only": citations_total["pdf_only"],
            "over_suppression": _rate(
                total(real, "suppressed_but_supported"), total(real, "suppressed_judged")
            ),
            "published": sum(1 for r in real if r["publish_decision"] == "publish"),
        },
        "fictitious": {
            "claims_kept": total(fictitious, "claims_kept"),
            "runs_with_any_kept_claim": sum(1 for r in fictitious if r["claims_kept"]),
            "runs_blocked": sum(1 for r in fictitious if r["publish_decision"] == "blocked"),
        },
    }


def summary_markdown(result: dict[str, Any]) -> str:
    agg = result["aggregate"]
    real = agg["real"]

    def fmt(rate: dict[str, Any]) -> str:
        if rate["rate"] is None:
            return "n/a"
        low, high = rate["ci95"]
        return f"{rate['rate']:.1%} ({rate['k']}/{rate['n']}, 95% CI {low:.1%} to {high:.1%})"

    lines = [
        f"# GUS eval: {result.get('suite', '')}",
        "",
        f"Runs: {agg['real_runs']} real companies, {agg['fictitious_runs']} fictitious.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Kept-claim precision, strict (judge or hand label) | {fmt(real['kept_claim_precision_strict'])} |",
        f"| Kept-claim precision, counting partial support | {fmt(real['kept_claim_precision_lenient'])} |",
        f"| Suppression rate | {fmt(real['suppression_rate'])} |",
        f"| Suppressed by API outage, not judgement | {real['suppressed_by_outage']} |",
        f"| Over-suppression (suppressed, but judge finds support) | {fmt(real['over_suppression'])} |",
        f"| Kept claims whose cited ids all resolve | {fmt(real['citation_ids_resolve'])} |",
        f"| Kept claims whose quote is found in a cited source | {fmt(real['citation_quote_grounded'])} |",
        f"| Kept claims citing only unextracted PDFs | {real['citation_pdf_only']} |",
        f"| Real-company reports published | {real['published']} of {agg['real_runs']} |",
        f"| Claims kept about fictitious companies | {agg['fictitious']['claims_kept']} |",
        f"| Fictitious companies blocked | {agg['fictitious']['runs_blocked']} of {agg['fictitious_runs']} |",
        "",
        "| Run | Kind | Decision | Extracted | Kept | Suppressed | Judged | Supported | Quote grounded |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in result["per_run"]:
        lines.append(
            f"| {row['run']} | {row['kind']} | {row['publish_decision']} | {row['claims_extracted']} | "
            f"{row['claims_kept']} | {row['claims_suppressed']} | {row['kept_judged']} | "
            f"{row['kept_supported']} | {row['citations']['quote_grounded']} |"
        )
    if result.get("agreement") and result["agreement"]["items"]:
        a = result["agreement"]
        lines += [
            "",
            f"Judge vs hand labels on {a['items']} claims: exact agreement {a['exact_agreement']}, "
            f"kappa (supported vs not) {a['kappa_supported_vs_not']}.",
        ]
    if result.get("cost"):
        lines += ["", f"Billed cost: ${result['cost']['total_usd']:.2f}."]
    return "\n".join(lines) + "\n"
