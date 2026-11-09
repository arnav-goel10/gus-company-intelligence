"""Record a run's external traffic to a cassette, or replay it with no network.

A cassette is a directory:

    browser.json        browse records keyed by normalised job URL
    sources.json        raw fetches keyed by URL (status, headers, body file)
    sources/<sha>.bin   raw bodies
    model.json          model responses keyed by a hash of the exact request

Model calls are keyed on schema name, system prompt, user content (including
attached file bytes) and the output-token cap, so replay fails loudly if the
pipeline would send the model anything different from what was recorded.
That makes the committed sample a regression test, not only a demo.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from gus_reporter.models import SourceJob, TinyfishRunRecord
from gus_reporter.providers import BrowserAgent, StructuredModel
from gus_reporter.raw_source_client import FetchedSource, RawSourceClient
from gus_reporter.urls import normalize_url


class ReplayMiss(KeyError):
    """The pipeline asked for something the cassette does not contain."""


def model_request_key(
    *,
    schema_name: str,
    system_prompt: str,
    user_content: list[dict[str, Any]],
    max_output_tokens: int,
) -> str:
    canonical = json.dumps(
        {
            "schema_name": schema_name,
            "system_prompt": system_prompt,
            "user_content": user_content,
            "max_output_tokens": max_output_tokens,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    return f"{schema_name}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


class Cassette:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()
        self.browser: dict[str, dict[str, Any]] = self._load("browser.json")
        self.sources: dict[str, dict[str, Any]] = self._load("sources.json")
        self.model: dict[str, dict[str, Any]] = self._load("model.json")

    def _load(self, name: str) -> dict[str, Any]:
        path = self.root / name
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            for name, data in (
                ("browser.json", self.browser),
                ("sources.json", self.sources),
                ("model.json", self.model),
            ):
                (self.root / name).write_text(
                    json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )

    # -- raw source bodies -------------------------------------------------

    def put_body(self, body: bytes) -> str:
        digest = hashlib.sha256(body).hexdigest()[:24]
        target = self.root / "sources" / f"{digest}.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(body)
        return f"sources/{digest}.bin"

    def get_body(self, relpath: str) -> bytes:
        return (self.root / relpath).read_bytes()


def _usage_or_empty(usage: dict[str, Any] | None) -> dict[str, Any]:
    return dict(usage or {})


def _call_model(
    model: StructuredModel, kwargs: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    with_usage = getattr(model, "structured_response_with_usage", None)
    if callable(with_usage):
        return with_usage(**kwargs)
    return model.structured_response(**kwargs), {}


def _normalised_model_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    if kwargs.get("user_content") is None:
        kwargs = {
            **kwargs,
            "user_content": [{"type": "input_text", "text": kwargs.get("user_prompt", "")}],
        }
    return kwargs


# --------------------------------------------------------------------------
# Recording wrappers: pass through to the live client and remember the answer
# --------------------------------------------------------------------------


class RecordingBrowser:
    def __init__(self, inner: BrowserAgent, cassette: Cassette) -> None:
        self.inner = inner
        self.cassette = cassette

    def run_jobs(self, jobs: list[SourceJob]) -> list[TinyfishRunRecord]:
        records = self.inner.run_jobs(jobs)
        with self.cassette._lock:
            for job, record in zip(jobs, records):
                self.cassette.browser[normalize_url(job.url)] = record.to_dict()
        return records


class RecordingFetcher(RawSourceClient):
    def __init__(self, inner: RawSourceClient, cassette: Cassette) -> None:
        super().__init__(max_text_chars=inner.max_text_chars, timeout_seconds=inner.timeout_seconds)
        self.inner = inner
        self.cassette = cassette

    def fetch(self, url: str) -> FetchedSource:
        fetched = self.inner.fetch(url)
        entry: dict[str, Any] = {
            "final_url": fetched.final_url,
            "content_type": fetched.content_type,
            "retrieved_at": fetched.retrieved_at,
            "error": fetched.error,
            "body": self.cassette.put_body(fetched.body) if fetched.error is None else "",
        }
        with self.cassette._lock:
            self.cassette.sources[url] = entry
        return fetched


class RecordingModel:
    def __init__(self, inner: StructuredModel, cassette: Cassette) -> None:
        self.inner = inner
        self.cassette = cassette

    def structured_response(self, **kwargs: Any) -> dict[str, Any]:
        payload, _usage = self.structured_response_with_usage(**kwargs)
        return payload

    def structured_response_with_usage(
        self, **kwargs: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        normalised = _normalised_model_kwargs(kwargs)
        key = model_request_key(
            schema_name=normalised["schema_name"],
            system_prompt=normalised["system_prompt"],
            user_content=normalised["user_content"],
            max_output_tokens=normalised.get("max_output_tokens", 4000),
        )
        try:
            payload, usage = _call_model(self.inner, kwargs)
        except Exception as exc:
            with self.cassette._lock:
                self.cassette.model[key] = {"error": str(exc)}
            raise
        with self.cassette._lock:
            self.cassette.model[key] = {"response": payload, "usage": _usage_or_empty(usage)}
        return payload, usage


# --------------------------------------------------------------------------
# Replay: answer from the cassette, never touch the network
# --------------------------------------------------------------------------


class ReplayBrowser:
    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette

    def run_jobs(self, jobs: list[SourceJob]) -> list[TinyfishRunRecord]:
        records = []
        for job in jobs:
            data = self.cassette.browser.get(normalize_url(job.url))
            if data is None:
                raise ReplayMiss(f"browse job not in cassette: {job.url}")
            records.append(TinyfishRunRecord(**data))
        return records


class ReplayFetcher(RawSourceClient):
    def __init__(self, cassette: Cassette, *, max_text_chars: int = 24000) -> None:
        super().__init__(max_text_chars=max_text_chars, timeout_seconds=None)
        self.cassette = cassette

    def fetch(self, url: str) -> FetchedSource:
        entry = self.cassette.sources.get(url)
        if entry is None:
            raise ReplayMiss(f"raw source not in cassette: {url}")
        if entry.get("error") is not None:
            return FetchedSource(error=str(entry["error"]))
        return FetchedSource(
            final_url=str(entry["final_url"]),
            content_type=str(entry["content_type"]),
            body=self.cassette.get_body(str(entry["body"])),
            retrieved_at=str(entry["retrieved_at"]),
        )


class ReplayModel:
    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette

    def structured_response(self, **kwargs: Any) -> dict[str, Any]:
        payload, _usage = self.structured_response_with_usage(**kwargs)
        return payload

    def structured_response_with_usage(
        self, **kwargs: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        normalised = _normalised_model_kwargs(kwargs)
        key = model_request_key(
            schema_name=normalised["schema_name"],
            system_prompt=normalised["system_prompt"],
            user_content=normalised["user_content"],
            max_output_tokens=normalised.get("max_output_tokens", 4000),
        )
        entry = self.cassette.model.get(key)
        if entry is None:
            raise ReplayMiss(
                f"model call not in cassette: {key}. The pipeline sent a request that "
                "differs from the recording (prompt, evidence or schema changed)."
            )
        if "error" in entry:
            raise RuntimeError(entry["error"])
        return entry["response"], _usage_or_empty(entry.get("usage"))


# --------------------------------------------------------------------------
# Usage metering for cost accounting
# --------------------------------------------------------------------------


class UsageMeter:
    """Wraps a model client and keeps every call's billed token counts."""

    def __init__(self, inner: StructuredModel) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def structured_response(self, **kwargs: Any) -> dict[str, Any]:
        payload, _usage = self.structured_response_with_usage(**kwargs)
        return payload

    def structured_response_with_usage(
        self, **kwargs: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            payload, usage = _call_model(self.inner, kwargs)
        except Exception:
            with self._lock:
                self.calls.append({"schema_name": kwargs.get("schema_name", ""), "failed": True})
            raise
        with self._lock:
            self.calls.append({"schema_name": kwargs.get("schema_name", ""), **usage})
        return payload, usage

    def write(self, run_dir: Path, name: str = "usage.json") -> Path:
        target = Path(run_dir) / name
        calls = sorted(self.calls, key=lambda call: json.dumps(call, sort_keys=True))
        target.write_text(json.dumps(calls, indent=2, sort_keys=True), encoding="utf-8")
        return target
