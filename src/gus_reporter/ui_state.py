from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
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
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, RunState] = {}

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
