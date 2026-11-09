"""The three external capabilities the pipeline depends on.

Each stage talks to one of these protocols, never to an HTTP client directly.
The live implementations are TinyfishClient, RawSourceClient and OpenAIClient;
gus_reporter.replay provides recording and replaying implementations so a full
run can be reproduced offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from gus_reporter.models import EvidenceItem, SourceJob, TinyfishRunRecord


class BrowserAgent(Protocol):
    """Runs browse jobs and returns one record per job, in job order."""

    def run_jobs(self, jobs: list[SourceJob]) -> list[TinyfishRunRecord]: ...


class SourceFetcher(Protocol):
    """Downloads raw sources and turns them into evidence items."""

    def collect_evidence(
        self,
        *,
        source_refs: list[dict[str, Any]],
        run_dir: Path,
        category: str = "financials",
    ) -> tuple[list[EvidenceItem], list[dict[str, Any]]]: ...


class StructuredModel(Protocol):
    """Returns a JSON object that conforms to the given strict schema."""

    def structured_response(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        system_prompt: str,
        user_prompt: str = "",
        user_content: list[dict[str, Any]] | None = None,
        max_output_tokens: int = 4000,
        background: bool = True,
    ) -> dict[str, Any]: ...
