"""Acquisition: browse waves with bounded fan-out, then direct raw-source capture."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gus_reporter.discovery import (
    build_raw_financial_source_refs,
    build_raw_legal_source_refs,
    build_raw_news_source_refs,
    build_spawned_financial_source_jobs,
    build_spawned_legal_source_jobs,
    build_spawned_news_source_jobs,
)
from gus_reporter.events import RunEvents
from gus_reporter.evidence import normalize_tinyfish_runs
from gus_reporter.models import EvidenceItem, ReportRequest, SourceJob, TinyfishRunRecord
from gus_reporter.providers import BrowserAgent, SourceFetcher
from gus_reporter.urls import normalize_url


@dataclass
class AcquisitionLimits:
    default_browser_profile: str
    max_total_jobs: int
    max_waves: int


class SourceAcquirer:
    """Runs the planned jobs, spawns child jobs from what they find, then fetches raw sources."""

    def __init__(
        self,
        *,
        browser: BrowserAgent,
        fetcher: SourceFetcher,
        events: RunEvents,
        limits: AcquisitionLimits,
    ) -> None:
        self.browser = browser
        self.fetcher = fetcher
        self.events = events
        self.limits = limits

    def acquire(
        self, request: ReportRequest, source_jobs: list[SourceJob], run_dir: Path
    ) -> list[EvidenceItem]:
        self.events.stage(
            stage="acquisition",
            status="running",
            message="Running Tinyfish across discovered sources.",
            current=0,
            total=len(source_jobs),
        )
        scheduled_jobs, tinyfish_runs = self._run_browse_waves(request, source_jobs, run_dir)
        self.events.write_artifact(
            run_dir,
            "tinyfish_runs.json",
            [record.to_dict() for record in tinyfish_runs],
        )
        raw_evidence = self._fetch_raw_sources(request, tinyfish_runs, scheduled_jobs, run_dir)

        tinyfish_evidence = normalize_tinyfish_runs(tinyfish_runs)
        evidence = raw_evidence + tinyfish_evidence if raw_evidence else tinyfish_evidence
        self.events.write_artifact(
            run_dir, "evidence.json", [item.to_dict() for item in evidence]
        )
        return evidence

    def _run_browse_waves(
        self, request: ReportRequest, source_jobs: list[SourceJob], run_dir: Path
    ) -> tuple[list[SourceJob], list[TinyfishRunRecord]]:
        scheduled_jobs = list(source_jobs)
        pending_jobs = list(source_jobs)
        tinyfish_runs: list[TinyfishRunRecord] = []
        seen_urls = {normalize_url(job.url) for job in scheduled_jobs if job.url.strip()}
        acquisition_wave = 0
        max_total_jobs = max(1, self.limits.max_total_jobs)
        max_acquisition_waves = max(1, self.limits.max_waves)
        profile = self.limits.default_browser_profile

        while (
            pending_jobs
            and acquisition_wave < max_acquisition_waves
            and len(scheduled_jobs) <= max_total_jobs
        ):
            wave_runs = self.browser.run_jobs(pending_jobs)
            tinyfish_runs.extend(wave_runs)
            acquisition_wave += 1
            self.events.stage(
                stage="acquisition",
                status="running",
                message=(
                    f"Completed acquisition wave {acquisition_wave}; "
                    f"{len(tinyfish_runs)} of {len(scheduled_jobs)} source jobs processed."
                ),
                current=len(tinyfish_runs),
                total=len(scheduled_jobs),
            )

            spawned_jobs = (
                build_spawned_financial_source_jobs(request, wave_runs, profile, seen_urls)
                + build_spawned_legal_source_jobs(request, wave_runs, profile, seen_urls)
                + build_spawned_news_source_jobs(request, wave_runs, profile, seen_urls)
            )
            if not spawned_jobs:
                pending_jobs = []
                continue

            remaining_capacity = max(0, max_total_jobs - len(scheduled_jobs))
            if remaining_capacity == 0:
                pending_jobs = []
                continue
            if len(spawned_jobs) > remaining_capacity:
                spawned_jobs = spawned_jobs[:remaining_capacity]

            scheduled_jobs.extend(spawned_jobs)
            for job in spawned_jobs:
                self.events.emit(
                    {
                        "event": "source_job_update",
                        "stage": "acquisition",
                        "label": job.label,
                        "category": job.category,
                        "url": job.url,
                        "source_type": job.source_type,
                        "status": "queued",
                        "message": "Spawned from discovered financial sources.",
                        "browser_profile": job.browser_profile,
                        "run_id": "",
                        "streaming_url": "",
                        "attempt_number": 0,
                    }
                )

            self.events.write_artifact(
                run_dir,
                "source_jobs.json",
                [job.to_dict() for job in scheduled_jobs],
            )
            self.events.stage(
                stage="acquisition",
                status="running",
                message=(
                    f"Spawned {len(spawned_jobs)} child source "
                    f"{'agents' if len(spawned_jobs) != 1 else 'agent'} from discovered links."
                ),
                current=len(tinyfish_runs),
                total=len(scheduled_jobs),
            )
            pending_jobs = spawned_jobs

        return scheduled_jobs, tinyfish_runs

    def _fetch_raw_sources(
        self,
        request: ReportRequest,
        tinyfish_runs: list[TinyfishRunRecord],
        scheduled_jobs: list[SourceJob],
        run_dir: Path,
    ) -> list[EvidenceItem]:
        raw_financial_refs = build_raw_financial_source_refs(request, tinyfish_runs)
        raw_legal_refs = build_raw_legal_source_refs(request, tinyfish_runs)
        raw_news_refs = build_raw_news_source_refs(request, tinyfish_runs)
        all_raw_refs = raw_financial_refs + raw_legal_refs + raw_news_refs
        self.events.write_artifact(run_dir, "raw_source_refs.json", all_raw_refs)
        self.events.stage(
            stage="acquisition",
            status="running",
            message=(
                f"Directly fetching {len(all_raw_refs)} raw source "
                f"{'artifacts' if len(all_raw_refs) != 1 else 'artifact'} "
                f"({len(raw_financial_refs)} financial, {len(raw_legal_refs)} legal, "
                f"{len(raw_news_refs)} news)."
            ),
            current=len(tinyfish_runs),
            total=len(scheduled_jobs),
        )

        all_raw_evidence: list[EvidenceItem] = []
        all_raw_manifests: list[dict[str, Any]] = []
        for category_label, category_refs in [
            ("financials", raw_financial_refs),
            ("legal", raw_legal_refs),
            ("news", raw_news_refs),
        ]:
            if not category_refs:
                continue
            cat_evidence, cat_manifest = self.fetcher.collect_evidence(
                source_refs=category_refs,
                run_dir=run_dir,
                category=category_label,
            )
            all_raw_evidence.extend(cat_evidence)
            all_raw_manifests.extend(cat_manifest)

        self.events.write_artifact(run_dir, "raw_source_manifest.json", all_raw_manifests)
        self.events.stage(
            stage="acquisition",
            status="completed",
            message=(
                f"Completed acquisition: {len(tinyfish_runs)} Tinyfish discovery jobs and "
                f"{len(all_raw_evidence)} directly fetched raw sources."
            ),
            current=len(all_raw_evidence),
            total=max(len(all_raw_refs), 1),
        )
        return all_raw_evidence
