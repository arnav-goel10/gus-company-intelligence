"""Run one report.

    # live: needs OPENAI_API_KEY and TINYFISH_API_KEY
    python -m gus_reporter.cli --request examples/company_request.json

    # live, and save every external response so the run can be replayed
    python -m gus_reporter.cli --request req.json --record cassettes/acme

    # offline: no keys, no network, same pipeline code
    python -m gus_reporter.cli --request examples/sample_run/request.json \\
        --replay examples/sample_run/cassette
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

from gus_reporter.config import AppConfig
from gus_reporter.cost import run_cost
from gus_reporter.models import ReportRequest
from gus_reporter.pipeline import ReportPipeline, live_clients
from gus_reporter.render import write_markdown
from gus_reporter.replay import (
    Cassette,
    RecordingBrowser,
    RecordingFetcher,
    RecordingModel,
    ReplayBrowser,
    ReplayFetcher,
    ReplayModel,
    UsageMeter,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the GUS company report pipeline.")
    parser.add_argument("--request", required=True, help="Path to a JSON report request file.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--replay", metavar="CASSETTE", help="Replay a recorded cassette offline.")
    mode.add_argument("--record", metavar="CASSETTE", help="Run live and record a cassette.")
    parser.add_argument("--output-dir", help="Where run directories go (default REPORT_OUTPUT_DIR).")
    parser.add_argument("--json", action="store_true", help="Print the full report JSON.")
    return parser.parse_args(argv)


def build_pipeline(args: argparse.Namespace) -> tuple[ReportPipeline, UsageMeter, Cassette | None]:
    config = AppConfig.from_env(require_api_keys=not args.replay)
    if args.output_dir:
        config = dataclasses.replace(config, report_output_dir=Path(args.output_dir))
    if args.replay:
        cassette = Cassette(Path(args.replay))
        meter = UsageMeter(ReplayModel(cassette))
        pipeline = ReportPipeline(
            config,
            browser=ReplayBrowser(cassette),
            fetcher=ReplayFetcher(cassette),
            llm=meter,
        )
        return pipeline, meter, None

    browser, fetcher, model = live_clients(config)
    cassette = Cassette(Path(args.record)) if args.record else None
    if cassette is not None:
        meter = UsageMeter(RecordingModel(model, cassette))
        pipeline = ReportPipeline(
            config,
            browser=RecordingBrowser(browser, cassette),
            fetcher=RecordingFetcher(fetcher, cassette),
            llm=meter,
        )
    else:
        meter = UsageMeter(model)
        pipeline = ReportPipeline(config, browser=browser, fetcher=fetcher, llm=meter)
    return pipeline, meter, cassette


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    request_payload = json.loads(Path(args.request).read_text(encoding="utf-8"))
    pipeline, meter, cassette = build_pipeline(args)
    request = ReportRequest.from_dict(
        request_payload, pipeline.config.tinyfish_default_browser_profile
    )
    try:
        report, run_dir = pipeline.run(request)
    finally:
        if cassette is not None:
            cassette.save()
    meter.write(Path(run_dir))
    markdown = write_markdown(Path(run_dir))
    cost = run_cost(Path(run_dir))

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    kept = report.total_claims - report.unsupported_claims
    print(f"{report.company_name}: {report.publish_decision} ({report.publish_reason})")
    print(f"claims kept {kept}, suppressed {report.unsupported_claims}, total {report.total_claims}")
    label = "recorded cost" if args.replay else "cost"
    print(
        f"{label}: ${cost['total_usd']:.4f} "
        f"(model ${cost['model_usd']:.4f}, browse ${cost['browse_usd']:.4f})"
    )
    print(f"report: {markdown}")
    print(f"artifacts: {run_dir}")


if __name__ == "__main__":
    main()
