from __future__ import annotations

import argparse
import json
from pathlib import Path

from gus_reporter.config import AppConfig
from gus_reporter.models import ReportRequest
from gus_reporter.pipeline import ReportPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the GUS automated company report pipeline."
    )
    parser.add_argument(
        "--request",
        required=True,
        help="Path to a JSON report request file.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    request_path = Path(args.request)
    request_payload = json.loads(request_path.read_text(encoding="utf-8"))

    config = AppConfig.from_env()
    request = ReportRequest.from_dict(
        request_payload, config.tinyfish_default_browser_profile
    )
    pipeline = ReportPipeline(config)
    report, run_dir = pipeline.run(request)

    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    print(f"\nArtifacts written to: {run_dir}")


if __name__ == "__main__":
    main()
