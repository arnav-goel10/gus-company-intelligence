"""Progress events and artifact writes, shared by every pipeline stage."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from gus_reporter.models import utc_now_iso
from gus_reporter.storage import RunStorage


class RunEvents:
    """Thread-safe progress emitter plus the run's artifact writer."""

    def __init__(
        self,
        storage: RunStorage,
        callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.storage = storage
        self.callback = callback
        self._lock = threading.Lock()

    def emit(self, payload: dict[str, Any]) -> None:
        if self.callback is None:
            return
        with self._lock:
            self.callback({"timestamp": utc_now_iso(), **payload})

    def stage(
        self,
        *,
        stage: str,
        status: str,
        message: str,
        current: int | None = None,
        total: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "event": "stage_update",
            "stage": stage,
            "status": status,
            "message": message,
        }
        if current is not None:
            payload["current"] = current
        if total is not None:
            payload["total"] = total
        if extra:
            payload.update(extra)
        self.emit(payload)

    def write_artifact(self, run_dir: Path, relative_path: str, payload: Any) -> None:
        self.storage.write_json(run_dir, relative_path, payload)
        self.emit(
            {
                "event": "artifact_written",
                "relative_path": relative_path,
                "run_dir": str(run_dir),
            }
        )
