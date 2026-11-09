from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gus_reporter.models import utc_now_iso


STAGE_ORDER = ["planning", "acquisition", "extraction", "verification", "reporting"]
STAGE_LABELS = {
    "planning": "Source Plan",
    "acquisition": "Tinyfish Fetch",
    "extraction": "Claim Extraction",
    "verification": "Claim Verification",
    "reporting": "Report Assembly",
}


def _default_stages() -> dict[str, dict[str, Any]]:
    return {
        stage: {
            "key": stage,
            "label": STAGE_LABELS[stage],
            "status": "pending",
            "message": "",
            "current": 0,
            "total": 0,
        }
        for stage in STAGE_ORDER
    }


def _path_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(
        microsecond=0
    ).isoformat()


def _read_json_if_exists(path: Path) -> Any:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@dataclass
class RunState:
    run_id: str
    company_name: str
    request: dict[str, Any]
    created_at: str
    updated_at: str
    status: str = "queued"
    current_stage: str = "planning"
    run_dir: str = ""
    openai_model: str = ""
    error: str = ""
    final_report: dict[str, Any] | None = None
    source_jobs: list[dict[str, Any]] = field(default_factory=list)
    stages: dict[str, dict[str, Any]] = field(default_factory=_default_stages)
    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "company_name": self.company_name,
            "request": deepcopy(self.request),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "current_stage": self.current_stage,
            "run_dir": self.run_dir,
            "openai_model": self.openai_model,
            "error": self.error,
            "final_report": deepcopy(self.final_report),
            "source_jobs": deepcopy(self.source_jobs),
            "stages": deepcopy(self.stages),
            "events": deepcopy(self.events[-200:]),
            "artifacts": deepcopy(self.artifacts),
        }


class RunStateStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, RunState] = {}
        self._base_dir = base_dir.expanduser().resolve() if base_dir else None
        self._hydrate_runs_from_disk()

    def create_run(self, request: dict[str, Any]) -> str:
        run_id = uuid.uuid4().hex[:12]
        now = utc_now_iso()
        company_name = str(request.get("company_name", "")).strip() or "Unnamed company"
        with self._lock:
            self._runs[run_id] = RunState(
                run_id=run_id,
                company_name=company_name,
                request=deepcopy(request),
                created_at=now,
                updated_at=now,
            )
        return run_id

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            runs = sorted(
                self._runs.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )
            return [
                {
                    "run_id": run.run_id,
                    "company_name": run.company_name,
                    "created_at": run.created_at,
                    "updated_at": run.updated_at,
                    "status": run.status,
                    "current_stage": run.current_stage,
                    "run_dir": run.run_dir,
                    "error": run.error,
                }
                for run in runs
            ]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return None if run is None else run.to_dict()

    def resolve_run_dir(self, run_id: str) -> str:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None and run.run_dir:
                return run.run_dir
            if self._base_dir is None:
                return ""
            candidate = (self._base_dir / run_id).resolve()
            if candidate.exists() and candidate.is_dir():
                return str(candidate)
            return ""

    def record_event(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            run = self._runs[run_id]
            normalized = deepcopy(event)
            normalized.setdefault("timestamp", utc_now_iso())
            run.updated_at = normalized["timestamp"]
            run.events.append(normalized)

            event_name = str(normalized.get("event", "")).strip()
            stage = str(normalized.get("stage", "")).strip()

            if event_name == "pipeline_started":
                run.status = "running"
                run.run_dir = str(normalized.get("run_dir", "")).strip()
                run.openai_model = str(normalized.get("openai_model", "")).strip()

            if event_name == "pipeline_completed":
                run.status = "completed"
                run.run_dir = str(normalized.get("run_dir", run.run_dir)).strip()
                final_report = normalized.get("final_report")
                run.final_report = deepcopy(final_report) if isinstance(final_report, dict) else None

            if event_name == "pipeline_failed":
                run.status = "failed"
                run.run_dir = str(normalized.get("run_dir", run.run_dir)).strip()
                run.error = str(normalized.get("error", "Unknown error")).strip()
                failed_stage = str(normalized.get("stage", run.current_stage)).strip()
                if failed_stage in run.stages:
                    stage_state = run.stages[failed_stage]
                    stage_state["status"] = "failed"
                    if run.error:
                        stage_state["message"] = run.error
                    run.current_stage = failed_stage

                for item in run.source_jobs:
                    if item["status"] in {"queued", "running", "retrying"}:
                        item["status"] = "failed"
                        if not item["message"]:
                            item["message"] = "Run aborted before completion."

            if event_name == "artifact_written":
                relative_path = str(normalized.get("relative_path", "")).strip()
                if relative_path:
                    artifact = {
                        "relative_path": relative_path,
                        "run_dir": str(normalized.get("run_dir", run.run_dir)).strip(),
                    }
                    if artifact not in run.artifacts:
                        run.artifacts.append(artifact)

            if event_name == "stage_update" and stage in run.stages:
                stage_state = run.stages[stage]
                stage_state["status"] = str(normalized.get("status", stage_state["status"]))
                stage_state["message"] = str(normalized.get("message", "")).strip()
                if "current" in normalized:
                    stage_state["current"] = int(normalized["current"])
                if "total" in normalized:
                    stage_state["total"] = int(normalized["total"])
                run.current_stage = stage
                if run.status not in {"completed", "failed"}:
                    run.status = "running"
                if event_name == "stage_update" and "jobs" in normalized:
                    self._set_source_jobs(run, normalized.get("jobs", []))

            if event_name == "source_job_update":
                self._update_source_job(run, normalized)

    def _set_source_jobs(self, run: RunState, jobs: list[dict[str, Any]]) -> None:
        run.source_jobs = []
        for item in jobs:
            if not isinstance(item, dict):
                continue
            run.source_jobs.append(
                {
                    "label": str(item.get("label", "")).strip(),
                    "category": str(item.get("category", "")).strip(),
                    "url": str(item.get("url", "")).strip(),
                    "source_type": str(item.get("source_type", "")).strip(),
                    "browser_profile": str(item.get("browser_profile", "")).strip(),
                    "status": "queued",
                    "message": "",
                    "run_id": "",
                    "streaming_url": "",
                    "attempt_number": 0,
                }
            )

    def _update_source_job(self, run: RunState, event: dict[str, Any]) -> None:
        label = str(event.get("label", "")).strip()
        url = str(event.get("url", "")).strip()
        source_type = str(event.get("source_type", "")).strip()

        for item in run.source_jobs:
            if item["label"] == label and item["url"] == url:
                target = item
                break
        else:
            target = {
                "label": label,
                "category": str(event.get("category", "")).strip(),
                "url": url,
                "source_type": source_type,
                "browser_profile": str(event.get("browser_profile", "")).strip(),
                "status": "queued",
                "message": "",
                "run_id": "",
                "streaming_url": "",
                "attempt_number": 0,
            }
            run.source_jobs.append(target)

        target["status"] = str(event.get("status", target["status"])).strip()
        target["message"] = str(event.get("message", target["message"])).strip()
        target["run_id"] = str(event.get("run_id", target["run_id"])).strip()
        target["browser_profile"] = str(
            event.get("browser_profile", target["browser_profile"])
        ).strip()
        target["streaming_url"] = str(
            event.get("streaming_url", target["streaming_url"])
        ).strip()
        if event.get("attempt_number") is not None:
            target["attempt_number"] = int(event["attempt_number"])

    def _hydrate_runs_from_disk(self) -> None:
        if self._base_dir is None or not self._base_dir.exists():
            return
        for run_dir in sorted(self._base_dir.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            hydrated = self._hydrate_single_run(run_dir)
            if hydrated is None:
                continue
            self._runs[hydrated.run_id] = hydrated

    def _hydrate_single_run(self, run_dir: Path) -> RunState | None:
        request = _read_json_if_exists(run_dir / "request.json")
        final_report = _read_json_if_exists(run_dir / "final_report.json")
        source_jobs = _read_json_if_exists(run_dir / "source_jobs.json")
        tinyfish_runs = _read_json_if_exists(run_dir / "tinyfish_runs.json")

        if not isinstance(request, dict) and not isinstance(final_report, dict):
            return None

        company_name = (
            str((request or {}).get("company_name", "")).strip()
            or str((final_report or {}).get("company_name", "")).strip()
            or run_dir.name
        )
        created_at = _path_mtime_iso(run_dir / "request.json") if (run_dir / "request.json").exists() else _path_mtime_iso(run_dir)
        updated_at = _path_mtime_iso(run_dir)
        run = RunState(
            run_id=run_dir.name,
            company_name=company_name,
            request=deepcopy(request) if isinstance(request, dict) else {},
            created_at=created_at,
            updated_at=updated_at,
            run_dir=str(run_dir),
            final_report=deepcopy(final_report) if isinstance(final_report, dict) else None,
            artifacts=self._hydrate_artifacts(run_dir),
        )

        if isinstance(source_jobs, list):
            self._set_source_jobs(run, source_jobs)
        if isinstance(tinyfish_runs, list):
            self._hydrate_source_job_states(run, tinyfish_runs)

        self._hydrate_stage_projection(run, run_dir)
        return run

    def _hydrate_artifacts(self, run_dir: Path) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for path in sorted(run_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".json", ".txt", ".md"}:
                continue
            artifacts.append(
                {
                    "relative_path": str(path.relative_to(run_dir)),
                    "run_dir": str(run_dir),
                }
            )
        return artifacts

    def _hydrate_source_job_states(
        self, run: RunState, tinyfish_runs: list[dict[str, Any]]
    ) -> None:
        for record in tinyfish_runs:
            if not isinstance(record, dict):
                continue
            attempts = record.get("attempts")
            latest_attempt = attempts[-1] if isinstance(attempts, list) and attempts else {}
            status = str(record.get("status", "")).strip().lower() or "completed"
            self._update_source_job(
                run,
                {
                    "label": str(record.get("label", "")).strip(),
                    "category": str(record.get("category", "")).strip(),
                    "url": str(record.get("url", "")).strip(),
                    "source_type": str(record.get("source_type", "")).strip(),
                    "status": status,
                    "message": f"Recovered Tinyfish run with status {status}.",
                    "run_id": str(record.get("run_id", "")).strip(),
                    "browser_profile": str(
                        latest_attempt.get("browser_profile", "")
                    ).strip(),
                    "streaming_url": str(record.get("streaming_url", "")).strip(),
                    "attempt_number": latest_attempt.get("attempt_number") or 0,
                },
            )

    def _hydrate_stage_projection(self, run: RunState, run_dir: Path) -> None:
        artifact_names = {
            path.name
            for path in run_dir.rglob("*")
            if path.is_file()
        }
        stage_artifacts = {
            "planning": {"source_jobs.json"},
            "acquisition": {"tinyfish_runs.json", "raw_source_manifest.json", "evidence.json"},
            "extraction": {"extracted_claims.json"},
            "verification": {"verified_claims.json"},
            "reporting": {"final_report.json"},
        }

        completed_stages: list[str] = []
        for stage in STAGE_ORDER:
            if artifact_names.intersection(stage_artifacts[stage]):
                completed_stages.append(stage)
                run.stages[stage]["status"] = "completed"
            else:
                break

        if "final_report.json" in artifact_names and isinstance(run.final_report, dict):
            for stage in STAGE_ORDER:
                run.stages[stage]["status"] = "completed"
            run.status = "completed"
            run.current_stage = "reporting"
            return

        if completed_stages:
            next_index = min(len(completed_stages), len(STAGE_ORDER) - 1)
            current_stage = STAGE_ORDER[next_index]
            run.status = "failed"
            run.current_stage = current_stage
            run.error = (
                "Recovered historical run without a final report. "
                "The original server session likely stopped before completion."
            )
            run.stages[current_stage]["status"] = "failed"
            run.stages[current_stage]["message"] = run.error
            return

        run.status = "failed"
        run.current_stage = "planning"
        run.error = "Recovered historical run without usable artifacts."
        run.stages["planning"]["status"] = "failed"
        run.stages["planning"]["message"] = run.error
