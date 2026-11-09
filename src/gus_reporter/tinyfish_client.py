from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from gus_reporter.models import SourceJob, TinyfishRunRecord, utc_now_iso


TERMINAL_RUN_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}
# Phrases that mean the site refused automated access. A run that matches is
# recorded as blocked and is never retried with a different browser, because
# retrying around a bot wall would be evading the site's access policy.
BLOCKED_MARKERS = (
    "access denied",
    "anti-bot",
    "blocked",
    "bot detection",
    "captcha",
    "challenge",
    "cloudflare",
    "forbidden",
    "rate limited",
)


def _clean_optional_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in {"none", "null"}:
        return ""
    return text


def looks_access_blocked(record: TinyfishRunRecord) -> bool:
    """True when the run's status, result, error or steps mention a bot wall."""
    serialized = json.dumps(
        {
            "status": record.status,
            "result": record.result,
            "error": record.error,
            "steps": record.steps,
        },
        sort_keys=True,
        ensure_ascii=True,
    ).lower()
    return any(marker in serialized for marker in BLOCKED_MARKERS)


@dataclass
class _PendingRun:
    index: int
    job: SourceJob
    run_id: str
    browser_profile: str
    attempt_number: int
    streaming_url: str = ""
    progress_log: list[str] = field(default_factory=list)
    prior_attempts: list[dict[str, Any]] = field(default_factory=list)


class TinyfishClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        poll_interval_seconds: int,
        poll_timeout_seconds: int | None,
        batch_size: int = 20,
        request_max_retries: int = 3,
        request_retry_backoff_seconds: float = 2.0,
        request_timeout_seconds: int | None = 120,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_timeout_seconds = (
            poll_timeout_seconds if poll_timeout_seconds and poll_timeout_seconds > 0 else None
        )
        self.batch_size = max(1, min(batch_size, 100))
        self.request_max_retries = max(0, request_max_retries)
        self.request_retry_backoff_seconds = max(0.1, request_retry_backoff_seconds)
        self.request_timeout_seconds = (
            request_timeout_seconds if request_timeout_seconds and request_timeout_seconds > 0 else None
        )
        self.event_callback = event_callback

    def run_job(self, job: SourceJob) -> TinyfishRunRecord:
        return self.run_jobs([job])[0]

    def run_jobs(self, jobs: list[SourceJob]) -> list[TinyfishRunRecord]:
        if not jobs:
            return []

        ordered_records: list[TinyfishRunRecord | None] = [None] * len(jobs)
        async_jobs: list[tuple[int, SourceJob]] = []

        for index, job in enumerate(jobs):
            if job.preferred_run_mode == "sse":
                ordered_records[index] = self._run_job_with_retry(job)
            else:
                async_jobs.append((index, job))

        if async_jobs:
            async_results = self._run_jobs_async(async_jobs)
            for index, record in async_results.items():
                ordered_records[index] = record

        return [record for record in ordered_records if record is not None]

    def _emit_event(self, payload: dict[str, Any]) -> None:
        if self.event_callback is None:
            return
        self.event_callback({"timestamp": utc_now_iso(), **payload})

    def _emit_source_job_event(
        self,
        *,
        job: SourceJob,
        status: str,
        message: str,
        run_id: str = "",
        browser_profile: str = "",
        streaming_url: str = "",
        attempt_number: int | None = None,
    ) -> None:
        self._emit_event(
            {
                "event": "source_job_update",
                "stage": "acquisition",
                "label": job.label,
                "category": job.category,
                "url": job.url,
                "source_type": job.source_type,
                "status": status,
                "message": message,
                "run_id": run_id,
                "browser_profile": browser_profile,
                "streaming_url": streaming_url,
                "attempt_number": attempt_number,
            }
        )

    def _run_job_with_retry(self, job: SourceJob) -> TinyfishRunRecord:
        return self._run_job_once(
            job=job,
            browser_profile=job.browser_profile,
            attempt_number=1,
        )

    def _run_job_once(
        self,
        *,
        job: SourceJob,
        browser_profile: str,
        attempt_number: int,
    ) -> TinyfishRunRecord:
        if job.preferred_run_mode == "sse":
            return self._run_job_via_sse(job, browser_profile, attempt_number)

        run_id = self._start_async_run(job, browser_profile)
        return self._poll_single_run(
            job=job,
            run_id=run_id,
            browser_profile=browser_profile,
            attempt_number=attempt_number,
        )

    def _run_jobs_async(
        self, indexed_jobs: list[tuple[int, SourceJob]]
    ) -> dict[int, TinyfishRunRecord]:
        pending: dict[str, _PendingRun] = {}
        completed: dict[int, TinyfishRunRecord] = {}
        deadline = (
            time.time() + self.poll_timeout_seconds
            if self.poll_timeout_seconds is not None
            else None
        )

        for start in range(0, len(indexed_jobs), self.batch_size):
            batch = indexed_jobs[start : start + self.batch_size]
            for started in self._start_pending_runs(batch):
                pending[started.run_id] = started

        while pending:
            if deadline is not None and time.time() >= deadline:
                labels = ", ".join(sorted(item.job.label for item in pending.values()))
                raise TimeoutError(
                    "Tinyfish batch poll timed out after "
                    f"{self.poll_timeout_seconds} seconds for jobs: {labels}"
                )

            batch_records = self._fetch_runs_batch(list(pending))
            for run_id in list(pending):
                data = batch_records.get(run_id)
                if not data:
                    continue
                status = str(data.get("status", "")).upper()
                if status not in TERMINAL_RUN_STATUSES:
                    streaming_url = _clean_optional_text(data.get("streaming_url"))
                    if streaming_url and streaming_url != pending[run_id].streaming_url:
                        pending[run_id].streaming_url = streaming_url
                        self._emit_source_job_event(
                            job=pending[run_id].job,
                            status="running",
                            message="Tinyfish live preview available.",
                            run_id=run_id,
                            browser_profile=pending[run_id].browser_profile,
                            streaming_url=streaming_url,
                            attempt_number=pending[run_id].attempt_number,
                        )
                    continue

                current = pending.pop(run_id)
                record = self._record_from_run_data(
                    data=data,
                    pending=current,
                )
                self._emit_source_job_event(
                    job=current.job,
                    status=record.status.lower(),
                    message=self._finished_message(record),
                    run_id=record.run_id,
                    browser_profile=current.browser_profile,
                    streaming_url=record.streaming_url,
                    attempt_number=current.attempt_number,
                )
                completed[current.index] = record

            if pending:
                time.sleep(self.poll_interval_seconds)

        return completed

    def _start_pending_runs(
        self, indexed_jobs: list[tuple[int, SourceJob]]
    ) -> list[_PendingRun]:
        if len(indexed_jobs) == 1:
            index, job = indexed_jobs[0]
            return [
                self._start_pending_run(
                    index=index,
                    job=job,
                    browser_profile=job.browser_profile,
                    attempt_number=1,
                )
            ]

        payload = {
            "runs": [
                self._build_payload(job, job.browser_profile)
                for _index, job in indexed_jobs
            ]
        }
        data = self._request("POST", "/automation/run-batch", payload)
        run_ids = [str(item).strip() for item in data.get("run_ids", []) if str(item).strip()]
        if len(run_ids) != len(indexed_jobs):
            raise RuntimeError(
                "Tinyfish did not return the expected number of run_ids for the batch: "
                f"expected {len(indexed_jobs)}, got {len(run_ids)}."
            )

        pending_runs: list[_PendingRun] = []
        for (index, job), run_id in zip(indexed_jobs, run_ids, strict=True):
            self._emit_source_job_event(
                job=job,
                status="running",
                message="Tinyfish run started.",
                run_id=run_id,
                browser_profile=job.browser_profile,
                attempt_number=1,
            )
            pending_runs.append(
                _PendingRun(
                    index=index,
                    job=job,
                    run_id=run_id,
                    browser_profile=job.browser_profile,
                    attempt_number=1,
                )
            )
        return pending_runs

    def _start_pending_run(
        self,
        *,
        index: int,
        job: SourceJob,
        browser_profile: str,
        attempt_number: int,
        prior_attempts: list[dict[str, Any]] | None = None,
    ) -> _PendingRun:
        run_id = self._start_async_run(job, browser_profile)
        self._emit_source_job_event(
            job=job,
            status="running",
            message="Tinyfish run started.",
            run_id=run_id,
            browser_profile=browser_profile,
            attempt_number=attempt_number,
        )
        return _PendingRun(
            index=index,
            job=job,
            run_id=run_id,
            browser_profile=browser_profile,
            attempt_number=attempt_number,
            prior_attempts=list(prior_attempts or []),
        )

    def _start_async_run(self, job: SourceJob, browser_profile: str) -> str:
        data = self._request(
            "POST",
            "/automation/run-async",
            self._build_payload(job, browser_profile),
        )
        run_id = str(data.get("run_id", "")).strip()
        if not run_id:
            raise RuntimeError(f"Tinyfish did not return a run_id for job: {job.label}")
        return run_id

    def _poll_single_run(
        self,
        *,
        job: SourceJob,
        run_id: str,
        browser_profile: str,
        attempt_number: int,
    ) -> TinyfishRunRecord:
        deadline = (
            time.time() + self.poll_timeout_seconds
            if self.poll_timeout_seconds is not None
            else None
        )
        while True:
            if deadline is not None and time.time() >= deadline:
                break
            data = self._request("GET", f"/runs/{urllib.parse.quote(run_id)}")
            status = str(data.get("status", "")).upper()
            if status in TERMINAL_RUN_STATUSES:
                record = self._record_from_run_data(
                    data=data,
                    pending=_PendingRun(
                        index=0,
                        job=job,
                        run_id=run_id,
                        browser_profile=browser_profile,
                        attempt_number=attempt_number,
                    ),
                )
                self._emit_source_job_event(
                    job=job,
                    status=record.status.lower(),
                    message=self._finished_message(record),
                    run_id=record.run_id,
                    browser_profile=browser_profile,
                    streaming_url=record.streaming_url,
                    attempt_number=attempt_number,
                )
                return record
            time.sleep(self.poll_interval_seconds)
        raise TimeoutError(
            f"Tinyfish run {run_id} for job '{job.label}' timed out after "
            f"{self.poll_timeout_seconds} seconds."
        )

    def _run_job_via_sse(
        self,
        job: SourceJob,
        browser_profile: str,
        attempt_number: int,
    ) -> TinyfishRunRecord:
        url = f"{self.base_url}/automation/run-sse"
        payload = json.dumps(self._build_payload(job, browser_profile)).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
                "X-API-Key": self.api_key,
            },
        )
        run_id = ""
        streaming_url = ""
        progress_log: list[str] = []
        fallback_started_at = ""

        try:
            if self.request_timeout_seconds is None:
                response_context = urllib.request.urlopen(request)
            else:
                response_context = urllib.request.urlopen(
                    request,
                    timeout=self.request_timeout_seconds,
                )
            with response_context as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload_text = line[5:].strip()
                    if not payload_text:
                        continue
                    event = json.loads(payload_text)
                    event_type = str(event.get("type", "")).upper()
                    if event_type == "STARTED":
                        run_id = str(event.get("run_id", "")).strip()
                        fallback_started_at = str(event.get("started_at", "")).strip()
                        self._emit_source_job_event(
                            job=job,
                            status="running",
                            message="Tinyfish stream started.",
                            run_id=run_id,
                            browser_profile=browser_profile,
                            attempt_number=attempt_number,
                        )
                        continue
                    if event_type == "STREAMING_URL":
                        streaming_url = str(event.get("streaming_url", "")).strip()
                        self._emit_source_job_event(
                            job=job,
                            status="running",
                            message="Tinyfish live preview available.",
                            run_id=run_id,
                            browser_profile=browser_profile,
                            streaming_url=streaming_url,
                            attempt_number=attempt_number,
                        )
                        continue
                    if event_type == "PROGRESS":
                        summary = (
                            str(event.get("message", "")).strip()
                            or str(event.get("purpose", "")).strip()
                            or str(event.get("description", "")).strip()
                        )
                        if summary:
                            progress_log.append(summary)
                            self._emit_source_job_event(
                                job=job,
                                status="running",
                                message=summary,
                                run_id=run_id,
                                browser_profile=browser_profile,
                                streaming_url=streaming_url,
                                attempt_number=attempt_number,
                            )
                        continue
                    if event_type in {"COMPLETE", "FAILED", "CANCELLED", "ERROR"}:
                        run_payload = (
                            event.get("run")
                            if isinstance(event.get("run"), dict)
                            else event
                        )
                        record = self._record_from_run_data(
                            data=run_payload,
                            pending=_PendingRun(
                                index=0,
                                job=job,
                                run_id=run_id or str(run_payload.get("run_id", "")).strip(),
                                browser_profile=browser_profile,
                                attempt_number=attempt_number,
                                streaming_url=streaming_url,
                                progress_log=progress_log,
                            ),
                            fallback_started_at=fallback_started_at,
                        )
                        self._emit_source_job_event(
                            job=job,
                            status=record.status.lower(),
                            message=self._finished_message(record),
                            run_id=record.run_id,
                            browser_profile=browser_profile,
                            streaming_url=record.streaming_url,
                            attempt_number=attempt_number,
                        )
                        return record
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Tinyfish SSE request failed: POST {url} -> {exc.code} {detail}"
            ) from exc

        if not run_id:
            raise RuntimeError(
                f"Tinyfish SSE stream ended before returning a run_id for job: {job.label}"
            )
        data = self._request("GET", f"/runs/{urllib.parse.quote(run_id)}")
        record = self._record_from_run_data(
            data=data,
            pending=_PendingRun(
                index=0,
                job=job,
                run_id=run_id,
                browser_profile=browser_profile,
                attempt_number=attempt_number,
                streaming_url=streaming_url,
                progress_log=progress_log,
            ),
            fallback_started_at=fallback_started_at,
        )
        self._emit_source_job_event(
            job=job,
            status=record.status.lower(),
            message=self._finished_message(record),
            run_id=record.run_id,
            browser_profile=browser_profile,
            streaming_url=record.streaming_url,
            attempt_number=attempt_number,
        )
        return record

    def _fetch_runs_batch(self, run_ids: list[str]) -> dict[str, dict[str, Any]]:
        if len(run_ids) == 1:
            run_id = run_ids[0]
            item = self._request("GET", f"/runs/{urllib.parse.quote(run_id)}")
            resolved_run_id = str(item.get("run_id", "")).strip() or run_id
            return {resolved_run_id: item}
        records: dict[str, dict[str, Any]] = {}
        for start in range(0, len(run_ids), self.batch_size):
            batch_ids = run_ids[start : start + self.batch_size]
            payload = {"run_ids": batch_ids}
            data = self._request("POST", "/runs/batch", payload)
            for item in data.get("data", []):
                run_id = str(item.get("run_id", "")).strip()
                if run_id:
                    records[run_id] = item
        return records

    def _record_from_run_data(
        self,
        *,
        data: dict[str, Any],
        pending: _PendingRun,
        fallback_started_at: str = "",
    ) -> TinyfishRunRecord:
        attempts = data.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            attempts = [
                {
                    "attempt_number": pending.attempt_number,
                    "browser_profile": pending.browser_profile,
                    "proxy_country_code": pending.job.proxy_country_code,
                    "run_id": pending.run_id,
                }
            ]
        attempts = list(pending.prior_attempts) + attempts
        record = TinyfishRunRecord(
            run_id=pending.run_id,
            status=str(data.get("status", "COMPLETED")).upper(),
            goal=str(data.get("goal", pending.job.goal)),
            label=pending.job.label,
            category=pending.job.category,
            url=pending.job.url,
            source_type=pending.job.source_type,
            created_at=str(data.get("created_at", "")),
            started_at=str(data.get("started_at", "")) or fallback_started_at,
            finished_at=str(data.get("finished_at", "")),
            num_of_steps=self._coerce_int(data.get("num_of_steps")),
            streaming_url=_clean_optional_text(data.get("streaming_url"))
            or pending.streaming_url,
            browser_config=data.get("browser_config")
            if isinstance(data.get("browser_config"), dict)
            else {},
            steps=data.get("steps") if isinstance(data.get("steps"), list) else [],
            attempts=attempts,
            result=data.get("result"),
            error=data.get("error"),
        )
        record.access_blocked = looks_access_blocked(record)
        return record

    def _build_payload(
        self, job: SourceJob, browser_profile: str
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "url": job.url,
            "goal": job.goal,
            "browser_profile": browser_profile,
            "api_integration": "gus-reporter",
        }
        if job.proxy_country_code:
            payload["proxy_config"] = {
                "enabled": True,
                "country_code": job.proxy_country_code,
            }
        if job.enable_agent_memory:
            payload["feature_flags"] = {"enable_agent_memory": True}
        if job.use_vault:
            payload["use_vault"] = True
            if job.credential_item_ids:
                payload["credential_item_ids"] = job.credential_item_ids
        return payload

    def _finished_message(self, record: TinyfishRunRecord) -> str:
        message = f"Tinyfish run finished with status {record.status.lower()}."
        if record.access_blocked:
            message += " The site refused automated access; recorded as blocked, not retried."
        return message

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")

        for attempt in range(self.request_max_retries + 1):
            request = urllib.request.Request(
                url,
                data=body,
                method=method,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": self.api_key,
                },
            )
            try:
                if self.request_timeout_seconds is None:
                    response_context = urllib.request.urlopen(request)
                else:
                    response_context = urllib.request.urlopen(
                        request,
                        timeout=self.request_timeout_seconds,
                    )
                with response_context as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if self._is_retryable_http_status(exc.code) and attempt < self.request_max_retries:
                    time.sleep(self._backoff_seconds(attempt))
                    continue
                raise RuntimeError(
                    f"Tinyfish request failed: {method} {url} -> {exc.code} {detail}"
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < self.request_max_retries:
                    time.sleep(self._backoff_seconds(attempt))
                    continue
                raise RuntimeError(
                    f"Tinyfish request failed: {method} {url} -> {exc.reason}"
                ) from exc

        raise RuntimeError(f"Tinyfish request exhausted retries: {method} {url}")

    def _backoff_seconds(self, attempt: int) -> float:
        return self.request_retry_backoff_seconds * (2**attempt)

    def _is_retryable_http_status(self, status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    def _coerce_int(self, value: Any) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None
