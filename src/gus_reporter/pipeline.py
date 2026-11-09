"""End-to-end orchestration: plan, acquire, extract, verify, assemble.

Each stage lives in its own module and depends only on the provider
protocols in gus_reporter.providers, so the same pipeline runs against the
live APIs, a recording wrapper, or a committed replay cassette.
"""

from __future__ import annotations

from typing import Any, Callable

from gus_reporter.acquisition import AcquisitionLimits, SourceAcquirer
from gus_reporter.assembly import ReportAssembler
from gus_reporter.config import AppConfig
from gus_reporter.events import RunEvents
from gus_reporter.extraction import ClaimExtractor
from gus_reporter.models import FinalReport, ReportRequest
from gus_reporter.openai_client import OpenAIClient
from gus_reporter.planning import seed_source_jobs
from gus_reporter.providers import BrowserAgent, SourceFetcher, StructuredModel
from gus_reporter.raw_source_client import RawSourceClient
from gus_reporter.storage import RunStorage
from gus_reporter.tinyfish_client import TinyfishClient
from gus_reporter.verification import ClaimVerifier


def live_clients(
    config: AppConfig,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[TinyfishClient, RawSourceClient, OpenAIClient]:
    """The real Tinyfish, raw-fetch and OpenAI clients for a config."""
    browser = TinyfishClient(
        api_key=config.tinyfish_api_key,
        base_url=config.tinyfish_base_url,
        poll_interval_seconds=config.tinyfish_poll_interval_seconds,
        poll_timeout_seconds=config.tinyfish_poll_timeout_seconds,
        request_timeout_seconds=config.tinyfish_request_timeout_seconds,
        batch_size=config.tinyfish_batch_size,
        request_max_retries=config.tinyfish_request_max_retries,
        request_retry_backoff_seconds=config.tinyfish_request_retry_backoff_seconds,
        event_callback=progress_callback,
    )
    fetcher = RawSourceClient(timeout_seconds=config.raw_source_timeout_seconds)
    llm = OpenAIClient(
        api_key=config.openai_api_key,
        base_url=config.openai_base_url,
        model=config.openai_model,
        request_timeout_seconds=config.openai_request_timeout_seconds,
        request_max_retries=config.openai_request_max_retries,
        request_retry_backoff_seconds=config.openai_request_retry_backoff_seconds,
    )
    return browser, fetcher, llm


class ReportPipeline:
    def __init__(
        self,
        config: AppConfig,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        *,
        browser: BrowserAgent | None = None,
        fetcher: SourceFetcher | None = None,
        llm: StructuredModel | None = None,
    ) -> None:
        self.config = config
        self.progress_callback = progress_callback
        self.storage = RunStorage(config.report_output_dir)
        self.events = RunEvents(self.storage, progress_callback)
        if browser is None or fetcher is None or llm is None:
            live_browser, live_fetcher, live_llm = live_clients(config, progress_callback)
            browser = browser or live_browser
            fetcher = fetcher or live_fetcher
            llm = llm or live_llm
        self.tinyfish: BrowserAgent = browser
        self.raw_source_client: SourceFetcher = fetcher
        self.openai: StructuredModel = llm
        self.acquirer = SourceAcquirer(
            browser=self.tinyfish,
            fetcher=self.raw_source_client,
            events=self.events,
            limits=AcquisitionLimits(
                default_browser_profile=config.tinyfish_default_browser_profile,
                max_total_jobs=config.tinyfish_max_total_jobs,
                max_waves=config.tinyfish_max_acquisition_waves,
            ),
        )
        self.extractor = ClaimExtractor(
            llm=self.openai,
            events=self.events,
            max_workers=config.openai_extraction_max_workers,
        )
        self.verifier = ClaimVerifier(
            llm=self.openai,
            events=self.events,
            max_workers=config.openai_verification_max_workers,
        )
        self.assembler = ReportAssembler(llm=self.openai, events=self.events)

    def run(self, request: ReportRequest) -> tuple[FinalReport, str]:
        run_dir = self.storage.create_run_dir(request.company_name)
        events = self.events
        events.emit(
            {
                "event": "pipeline_started",
                "company_name": request.company_name,
                "run_dir": str(run_dir),
                "openai_model": self.config.openai_model,
            }
        )
        try:
            events.stage(
                stage="planning",
                status="running",
                message="Building source plan for the requested company.",
            )
            events.write_artifact(run_dir, "request.json", request.to_dict())
            source_jobs = seed_source_jobs(request, self.config.tinyfish_default_browser_profile)
            events.write_artifact(
                run_dir, "source_jobs.json", [job.to_dict() for job in source_jobs]
            )
            events.stage(
                stage="planning",
                status="completed",
                message=f"Planned {len(source_jobs)} source jobs.",
                current=len(source_jobs),
                total=len(source_jobs),
                extra={"jobs": [job.to_dict() for job in source_jobs]},
            )

            evidence = self.acquirer.acquire(request, source_jobs, run_dir)

            extracted_claims = self.extractor.extract(request, evidence, run_dir)
            events.write_artifact(
                run_dir,
                "extracted_claims.json",
                [claim.to_dict() for claim in extracted_claims],
            )

            verified_claims = self.verifier.verify(extracted_claims, evidence, run_dir)
            events.write_artifact(
                run_dir,
                "verified_claims.json",
                [claim.to_dict() for claim in verified_claims],
            )

            final_report = self.assembler.assemble(request, verified_claims, evidence)
            events.write_artifact(run_dir, "final_report.json", final_report.to_dict())
            events.emit(
                {
                    "event": "pipeline_completed",
                    "run_dir": str(run_dir),
                    "final_report": final_report.to_dict(),
                }
            )
            return final_report, str(run_dir)
        except Exception as exc:
            events.emit(
                {
                    "event": "pipeline_failed",
                    "run_dir": str(run_dir),
                    "error": str(exc),
                }
            )
            raise
