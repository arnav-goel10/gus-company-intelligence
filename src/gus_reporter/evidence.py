"""Evidence: normalise captured sources and package them as model input."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from gus_reporter.agents import DomainAgent, ExtractionAgent
from gus_reporter.models import EvidenceItem, TinyfishRunRecord, utc_now_iso

MAX_TEXT_EVIDENCE_CHARS = 12000
MAX_FILE_INPUTS_PER_REQUEST = 4


def _render_capture_sources(result: dict[str, Any]) -> list[str]:
    rendered: list[str] = []
    sources = result.get("sources")
    if isinstance(sources, list):
        for index, source in enumerate(sources, start=1):
            if not isinstance(source, dict):
                continue
            linked_documents = source.get("linked_documents")
            linked_lines: list[str] = []
            if isinstance(linked_documents, list):
                for item in linked_documents:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title", "")).strip()
                    url = str(item.get("url", "")).strip()
                    document_type = str(item.get("document_type", "")).strip()
                    if title or url:
                        linked_lines.append(
                            f"- {title or 'Document'} | {document_type or 'unknown'} | {url}"
                        )

            rendered.append(
                "\n".join(
                    [
                        f"Captured source {index}",
                        f"Page title: {source.get('page_title', '')}",
                        f"Final URL: {source.get('final_url', '')}",
                        f"Source kind: {source.get('source_kind', '')}",
                        f"Reporting period: {source.get('reporting_period', '')}",
                        (
                            "Captured text:\n"
                            + str(source.get("captured_text", "")).strip()
                            if str(source.get("captured_text", "")).strip()
                            else ""
                        ),
                        "Linked documents:",
                        "\n".join(linked_lines) if linked_lines else "- None",
                        f"Notes: {source.get('notes', '')}",
                    ]
                ).strip()
            )
    return rendered


def _render_tinyfish_evidence(run: TinyfishRunRecord) -> str:
    sections = [
        f"Run status: {run.status}",
        f"Source label: {run.label}",
        f"Source type: {run.source_type}",
        f"Source URL: {run.url}",
    ]
    if run.access_blocked:
        sections.append("Access: the site refused automated access; nothing was captured.")
    if isinstance(run.result, dict):
        capture_mode = str(run.result.get("capture_mode", "")).strip()
        if capture_mode:
            sections.append(f"Capture mode: {capture_mode}")
        sections.extend(_render_capture_sources(run.result))
        coverage_gaps = run.result.get("coverage_gaps")
        if isinstance(coverage_gaps, list) and coverage_gaps:
            sections.append(
                "Coverage gaps:\n" + "\n".join(f"- {str(item).strip()}" for item in coverage_gaps)
            )
    elif run.result is not None:
        sections.append(
            "Result payload:\n"
            + json.dumps(run.result, indent=2, sort_keys=True, ensure_ascii=True)
        )

    if run.error:
        sections.append(
            "Error payload:\n"
            + json.dumps(run.error, indent=2, sort_keys=True, ensure_ascii=True)
        )

    step_trace = [
        {
            "timestamp": step.get("timestamp"),
            "status": step.get("status"),
            "action": step.get("action"),
            "duration": step.get("duration"),
        }
        for step in run.steps[:20]
    ]
    if step_trace:
        sections.append(
            "Tinyfish step trace:\n"
            + json.dumps(step_trace, indent=2, sort_keys=True, ensure_ascii=True)
        )
    if run.attempts:
        sections.append(
            "Tinyfish attempts:\n"
            + json.dumps(run.attempts, indent=2, sort_keys=True, ensure_ascii=True)
        )
    return "\n\n".join(section for section in sections if section).strip()


def normalize_tinyfish_runs(runs: list[TinyfishRunRecord]) -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []
    for index, run in enumerate(runs, start=1):
        evidence.append(
            EvidenceItem(
                evidence_id=f"tinyfish_{run.category or 'general'}_{index:03d}",
                label=run.label,
                category=run.category,
                url=run.url,
                retrieved_at=run.finished_at or run.started_at or run.created_at or utc_now_iso(),
                content=_render_tinyfish_evidence(run),
                metadata={
                    "tinyfish_run_id": run.run_id,
                    "tinyfish_status": run.status,
                    "tinyfish_goal": run.goal,
                    "tinyfish_source_type": run.source_type,
                    "tinyfish_streaming_url": run.streaming_url,
                    "tinyfish_num_of_steps": run.num_of_steps,
                    "tinyfish_browser_config": run.browser_config,
                },
            )
        )
    return evidence


def _build_openai_user_content(
    *,
    preamble: str,
    evidence: list[EvidenceItem],
    run_dir: Path | None = None,
) -> list[dict[str, Any]]:
    text_sections: list[str] = [preamble, "", "Evidence bundle:"]
    file_inputs: list[dict[str, Any]] = []

    for item in evidence:
        file_input = _build_local_file_input(item, run_dir)
        if file_input and len(file_inputs) < MAX_FILE_INPUTS_PER_REQUEST:
            file_inputs.append(file_input)
        elif len(file_inputs) < MAX_FILE_INPUTS_PER_REQUEST:
            file_url = str(item.metadata.get("openai_input_file_url", "")).strip()
            if file_url:
                file_inputs.append({"type": "input_file", "file_url": file_url})

        raw_text = str(item.metadata.get("openai_input_text", "")).strip() or item.content
        if len(raw_text) > MAX_TEXT_EVIDENCE_CHARS:
            raw_text = raw_text[:MAX_TEXT_EVIDENCE_CHARS].rstrip() + "\n...[truncated]"

        text_sections.append(
            "\n".join(
                [
                    f"Evidence ID: {item.evidence_id}",
                    f"Label: {item.label}",
                    f"Category: {item.category}",
                    f"URL: {item.url}",
                    f"Source type: {item.metadata.get('source_type', item.metadata.get('tinyfish_source_type', ''))}",
                    f"Retrieved at: {item.retrieved_at}",
                    "Content:",
                    raw_text,
                ]
            ).strip()
        )

    content: list[dict[str, Any]] = [{"type": "input_text", "text": "\n\n".join(text_sections).strip()}]
    content.extend(file_inputs)
    return content


def _build_local_file_input(
    item: EvidenceItem, run_dir: Path | None
) -> dict[str, Any] | None:
    if run_dir is None:
        return None
    artifact_relpath = str(item.metadata.get("openai_input_file_path", "")).strip()
    if not artifact_relpath:
        return None
    candidate = (run_dir / artifact_relpath).resolve()
    run_root = run_dir.resolve()
    if run_root not in candidate.parents or not candidate.exists() or not candidate.is_file():
        return None
    if candidate.suffix.lower() != ".pdf":
        return None
    encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
    return {
        "type": "input_file",
        "filename": str(item.metadata.get("openai_input_filename", "")).strip()
        or candidate.name,
        "file_data": f"data:application/pdf;base64,{encoded}",
    }


def select_evidence_for_agent(
    agent: DomainAgent, evidence: list[EvidenceItem]
) -> list[EvidenceItem]:
    if agent.use_all_evidence:
        return evidence
    preferred = set(agent.preferred_categories)
    filtered = [item for item in evidence if item.category in preferred]
    return filtered or evidence


def select_evidence_for_extraction_agent(
    agent: ExtractionAgent, evidence: list[EvidenceItem]
) -> list[EvidenceItem]:
    if agent.use_all_evidence:
        return evidence
    preferred_categories = set(agent.preferred_categories)
    filtered = [item for item in evidence if item.category in preferred_categories]
    if agent.preferred_source_types:
        preferred_source_types = set(agent.preferred_source_types)
        filtered_by_source_type = [
            item
            for item in filtered
            if item.metadata.get("tinyfish_source_type", "") in preferred_source_types
        ]
        if filtered_by_source_type:
            return filtered_by_source_type
    return filtered or evidence
