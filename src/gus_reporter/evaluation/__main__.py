"""GUS evaluation harness.

    # what a full run would cost; spends nothing
    python -m gus_reporter.evaluation estimate

    # the whole loop: estimate, confirm, run every company, judge, score
    python -m gus_reporter.evaluation run --max-usd 150

    # re-score finished runs, optionally with hand labels
    python -m gus_reporter.evaluation score --runs-dir results/eval/<stamp>/runs \\
        --labels eval/labels/<file>.csv

    # export a hand-labelling sheet for finished runs
    python -m gus_reporter.evaluation label-sheet --runs-dir results/eval/<stamp>/runs

Real runs need OPENAI_API_KEY and TINYFISH_API_KEY. Nothing is spent until the
estimate has been printed and confirmed (or --yes is passed).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gus_reporter.config import AppConfig
from gus_reporter.cost import TINYFISH_USD_PER_STEP, estimate_run, run_cost, scenario_table, token_cost
from gus_reporter.evaluation.judge import judge_claim
from gus_reporter.evaluation.labels import agreement, label_rows, read_labels, write_sheet
from gus_reporter.evaluation.metrics import aggregate, load_run, score_run, summary_markdown
from gus_reporter.models import ReportRequest
from gus_reporter.openai_client import OpenAIClient
from gus_reporter.pipeline import ReportPipeline, live_clients
from gus_reporter.providers import StructuredModel
from gus_reporter.render import write_markdown
from gus_reporter.replay import (
    Cassette,
    RecordingBrowser,
    RecordingFetcher,
    RecordingModel,
    UsageMeter,
)

DEFAULT_SUITE = Path("eval/suite.json")
DEFAULT_JUDGE_MODEL = "gpt-5.4"


def load_suite(path: Path, only: str | None = None) -> dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    if only:
        wanted = {item.strip() for item in only.split(",")}
        suite["companies"] = [c for c in suite["companies"] if c["id"] in wanted]
    return suite


# --------------------------------------------------------------------------
# Estimate
# --------------------------------------------------------------------------


def suite_estimate(
    suite: dict[str, Any], *, model: str, judge_model: str | None, max_total_jobs: int, audit: int,
    sample: int = 0,
    verification_mode: str = "batched",
) -> dict[str, Any]:
    rows = []
    totals = {"low": 0.0, "typical": 0.0, "high": 0.0}
    for company in suite["companies"]:
        request = ReportRequest.from_dict(company["request"], "lite")
        estimate = estimate_run(
            request,
            model=model,
            judge_model=judge_model,
            max_total_jobs=max_total_jobs,
            extra_judged=audit,
            judge_sample=sample,
            verification_mode=verification_mode,
        )
        rows.append({"id": company["id"], **estimate})
        for name in totals:
            totals[name] += estimate["estimates"][name]["total_usd"]
    return {"rows": rows, "totals": {k: round(v, 2) for k, v in totals.items()}}


def print_estimate(estimate: dict[str, Any], *, model: str, judge_model: str) -> None:
    mode = estimate["rows"][0]["verification_mode"] if estimate["rows"] else ""
    print(f"Pipeline model {model}, judge model {judge_model}, Tinyfish ${TINYFISH_USD_PER_STEP}/step.")
    print(f"Verification: {mode}.")
    print("Exact per request: seed browse jobs, extraction calls, report sections.")
    print("Assumed per scenario: child jobs, steps per job, page length, PDFs, claims found.\n")
    print(f"{'company':24} {'seed':>4} {'calls':>5}   {'low':>8} {'typical':>8} {'high':>8}")
    for row in estimate["rows"]:
        e = row["estimates"]
        print(
            f"{row['id']:24} {row['shape']['seed_jobs']:>4} {row['shape']['extraction_calls']:>5}   "
            f"${e['low']['total_usd']:>7.2f} ${e['typical']['total_usd']:>7.2f} ${e['high']['total_usd']:>7.2f}"
        )
    t = estimate["totals"]
    print(f"{'TOTAL':24} {'':>4} {'':>5}   ${t['low']:>7.2f} ${t['typical']:>7.2f} ${t['high']:>7.2f}\n")


def confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("Not confirmed (no terminal). Re-run with --yes to spend.")
        return False
    return input(f"{prompt} Type yes to continue: ").strip().lower() == "yes"


# --------------------------------------------------------------------------
# Judge
# --------------------------------------------------------------------------


def judge_run(
    run_dir: Path, model: StructuredModel, *, audit_suppressed: int, sample: int = 0
) -> list[dict[str, Any]]:
    """Judge kept claims (a seeded random sample of `sample` if > 0) plus a few suppressed."""
    data = load_run(run_dir)
    evidence_by_id = {item["evidence_id"]: item for item in data["evidence"]}
    kept = [c for c in data["claims"] if c["status"] == "supported"]
    if sample and len(kept) > sample:
        kept = random.Random(run_dir.name).sample(kept, sample)
    suppressed = [
        c
        for c in data["claims"]
        if c["status"] != "supported" and not str(c["rationale"]).startswith("Verification fallback")
    ][:audit_suppressed]
    results = []
    for claim in kept + suppressed:
        try:
            results.append(judge_claim(model, claim, evidence_by_id, run_dir))
        except Exception as exc:  # a failed judgement is recorded, never guessed
            results.append({"claim_id": claim["claim_id"], "verdict": "cannot_tell", "error": str(exc)})
    with (run_dir / "judgements.jsonl").open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return results


def usage_file_cost(path: Path) -> float:
    if not path.exists():
        return 0.0
    usd = 0.0
    for call in json.loads(path.read_text(encoding="utf-8")):
        if call.get("model"):
            usd += token_cost(
                call["model"],
                input_tokens=int(call.get("input_tokens", 0)),
                cached_input_tokens=int(call.get("cached_input_tokens", 0)),
                output_tokens=int(call.get("output_tokens", 0)),
            )
    return usd


def judge_client(config: AppConfig, judge_model: str) -> OpenAIClient:
    return OpenAIClient(
        api_key=config.openai_api_key,
        base_url=config.openai_base_url,
        model=judge_model,
        request_timeout_seconds=config.openai_request_timeout_seconds,
        request_max_retries=config.openai_request_max_retries,
        request_retry_backoff_seconds=config.openai_request_retry_backoff_seconds,
    )


# --------------------------------------------------------------------------
# Score
# --------------------------------------------------------------------------


def score_dirs(
    runs: list[tuple[str, str, Path]], labels_path: Path | None, suite_name: str
) -> dict[str, Any]:
    labels = read_labels(labels_path) if labels_path else None
    per_run = [score_run(name, kind, path, labels) for name, kind, path in runs]
    result: dict[str, Any] = {
        "suite": suite_name,
        "scored_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "per_run": per_run,
        "aggregate": aggregate(per_run),
    }
    if labels:
        judge_verdicts = {}
        for name, _kind, path in runs:
            for row in load_run(path)["judgements"]:
                judge_verdicts[(name, row["claim_id"])] = row["verdict"]
        result["agreement"] = agreement(labels, judge_verdicts)
    billed = 0.0
    for _name, _kind, path in runs:
        if (path / "usage.json").exists():
            billed += run_cost(path)["total_usd"] + usage_file_cost(path / "judge_usage.json")
    if billed:
        result["cost"] = {"total_usd": round(billed, 4)}
    return result


def runs_from_dir(runs_dir: Path, suite: dict[str, Any]) -> list[tuple[str, str, Path]]:
    kinds = {c["id"]: c["kind"] for c in suite["companies"]}
    return [
        (path.name, kinds.get(path.name, "real"), path)
        for path in sorted(runs_dir.iterdir())
        if (path / "verified_claims.json").exists()
    ]


def write_results(result: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "summary.md").write_text(summary_markdown(result), encoding="utf-8")
    print(summary_markdown(result))
    print(f"written: {out_dir / 'metrics.json'} and summary.md")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_estimate(args: argparse.Namespace) -> int:
    suite = load_suite(Path(args.suite), args.only)
    model = AppConfig.from_env(require_api_keys=False).openai_model
    judge_model = None if args.no_judge else args.judge_model
    estimate = suite_estimate(
        suite, model=model, judge_model=judge_model, max_total_jobs=args.max_total_jobs,
        audit=args.audit_suppressed, sample=args.judge_sample,
        verification_mode=args.verification_mode,
    )
    if args.json:
        print(json.dumps({"estimate": estimate, "scenarios": scenario_table()}, indent=2))
    else:
        print_estimate(estimate, model=model, judge_model=judge_model or "none")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    suite = load_suite(Path(args.suite), args.only)
    config = AppConfig.from_env()  # fails here, before any spend, if a key is missing
    config = dataclasses.replace(config, tinyfish_max_total_jobs=args.max_total_jobs)
    estimate = suite_estimate(
        suite, model=config.openai_model, judge_model=args.judge_model,
        max_total_jobs=args.max_total_jobs, audit=args.audit_suppressed, sample=args.judge_sample,
    )
    print_estimate(estimate, model=config.openai_model, judge_model=args.judge_model)
    budget = f", stopping before any company that would take spend past ${args.max_usd:.2f}"
    if not confirm(f"Typical spend ${estimate['totals']['typical']:.2f}{budget}.", args.yes):
        return 1

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out or f"results/eval/{stamp}")
    runs_dir = out_dir / "runs"
    spent = 0.0
    typical = {row["id"]: row["estimates"]["typical"]["total_usd"] for row in estimate["rows"]}
    failures = {}
    for company in suite["companies"]:
        if spent + typical[company["id"]] > args.max_usd:
            print(f"Stopping before {company['id']}: ${spent:.2f} spent, budget ${args.max_usd:.2f}.")
            break
        cassette = Cassette(out_dir / "cassettes" / company["id"])
        browser, fetcher, model = live_clients(config)
        meter = UsageMeter(RecordingModel(model, cassette))
        company_dir = runs_dir / company["id"]
        pipeline = ReportPipeline(
            dataclasses.replace(config, report_output_dir=runs_dir / "_tmp"),
            browser=RecordingBrowser(browser, cassette),
            fetcher=RecordingFetcher(fetcher, cassette),
            llm=meter,
        )
        print(f"running {company['id']} ...", flush=True)
        try:
            request = ReportRequest.from_dict(company["request"], config.tinyfish_default_browser_profile)
            _report, run_dir = pipeline.run(request)
        except Exception as exc:
            failures[company["id"]] = str(exc)
            print(f"  failed: {exc}")
            continue
        finally:
            cassette.save()
        Path(run_dir).rename(company_dir)
        meter.write(company_dir)
        write_markdown(company_dir)

        judge_meter = UsageMeter(judge_client(config, args.judge_model))
        judge_run(
            company_dir, judge_meter, audit_suppressed=args.audit_suppressed, sample=args.judge_sample
        )
        judge_meter.write(company_dir, "judge_usage.json")
        cost = run_cost(company_dir)["total_usd"] + usage_file_cost(company_dir / "judge_usage.json")
        spent += cost
        print(f"  done: ${cost:.2f} (total ${spent:.2f})")

    if (runs_dir / "_tmp").exists():
        try:
            (runs_dir / "_tmp").rmdir()
        except OSError:
            pass
    result = score_dirs(runs_from_dir(runs_dir, suite), None, suite["name"])
    if failures:
        result["failures"] = failures
    write_results(result, out_dir)
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    suite = load_suite(Path(args.suite))
    config = AppConfig.from_env()
    runs = runs_from_dir(Path(args.runs_dir), suite)
    kept = sum(
        sum(1 for c in load_run(path)["claims"] if c["status"] == "supported") for _, _, path in runs
    )
    print(f"{len(runs)} runs, {kept} kept claims to judge with {args.judge_model}.")
    if not confirm("This calls the judge model once per claim.", args.yes):
        return 1
    for _name, _kind, path in runs:
        meter = UsageMeter(judge_client(config, args.judge_model))
        judge_run(path, meter, audit_suppressed=args.audit_suppressed, sample=args.judge_sample)
        meter.write(path, "judge_usage.json")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    suite = load_suite(Path(args.suite))
    if args.run:
        runs = [(Path(args.run).name, args.kind, Path(args.run))]
    else:
        runs = runs_from_dir(Path(args.runs_dir), suite)
    result = score_dirs(runs, Path(args.labels) if args.labels else None, suite["name"])
    out_dir = Path(args.out) if args.out else (Path(args.runs_dir).parent if args.runs_dir else None)
    if out_dir is None:
        print(summary_markdown(result))
        return 0
    write_results(result, out_dir)
    return 0


def cmd_label_sheet(args: argparse.Namespace) -> int:
    suite = load_suite(Path(args.suite))
    runs = (
        [(Path(args.run).name, "real", Path(args.run))]
        if args.run
        else runs_from_dir(Path(args.runs_dir), suite)
    )
    rows = [
        row
        for name, _kind, path in runs
        for row in label_rows(name, path, include_suppressed=args.include_suppressed)
    ]
    target = write_sheet(rows, Path(args.out))
    print(f"{len(rows)} claims written to {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m gus_reporter.evaluation")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--suite", default=str(DEFAULT_SUITE))
        p.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
        p.add_argument("--audit-suppressed", type=int, default=5,
                       help="Also judge up to N suppressed claims per run (over-suppression).")
        p.add_argument("--max-total-jobs", type=int, default=50)
        p.add_argument("--judge-sample", type=int, default=20,
                       help="Judge a seeded random sample of up to N kept claims per run (0 = all).")

    p = sub.add_parser("estimate", help="Print the cost of a full run. Spends nothing.")
    common(p)
    p.add_argument("--only", help="Comma-separated company ids.")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-judge", action="store_true", help="Pipeline cost only, as for one report.")
    p.add_argument("--verification-mode", choices=["batched", "per_claim"], default="batched",
                   help="per_claim prices the old one-call-per-claim verifier, for comparison.")
    p.set_defaults(func=cmd_estimate)

    p = sub.add_parser("run", help="Estimate, confirm, run, judge and score.")
    common(p)
    p.add_argument("--only", help="Comma-separated company ids.")
    p.add_argument("--max-usd", type=float, required=True,
                   help="Budget. Stops before a company whose typical estimate would pass it.")
    p.add_argument("--out", help="Output directory (default results/eval/<timestamp>).")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("judge", help="Judge kept claims in finished runs.")
    common(p)
    p.add_argument("--runs-dir", required=True)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_judge)

    p = sub.add_parser("score", help="Compute metrics for finished runs. No API calls.")
    p.add_argument("--suite", default=str(DEFAULT_SUITE))
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--runs-dir")
    group.add_argument("--run", help="A single run directory.")
    p.add_argument("--kind", default="real", choices=["real", "fictitious"])
    p.add_argument("--labels", help="Hand-label CSV from label-sheet.")
    p.add_argument("--out")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("label-sheet", help="Export a CSV for hand labelling.")
    p.add_argument("--suite", default=str(DEFAULT_SUITE))
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--runs-dir")
    group.add_argument("--run")
    p.add_argument("--include-suppressed", action="store_true")
    p.add_argument("--out", default="eval/labels/sheet.csv")
    p.set_defaults(func=cmd_label_sheet)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
