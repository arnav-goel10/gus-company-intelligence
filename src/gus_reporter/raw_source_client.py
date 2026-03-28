from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from gus_reporter.models import EvidenceItem, utc_now_iso


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
PDF_EXTENSIONS = {".pdf"}
TEXTUAL_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/json",
    "application/xml",
    "text/xml",
    "text/csv",
    "application/csv",
}
SKIP_TAGS = {"script", "style", "noscript", "svg"}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "source"


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag in {"p", "div", "section", "article", "li", "tr", "br", "h1", "h2", "h3", "h4"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in {"p", "div", "section", "article", "li", "tr"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)
            self._chunks.append(" ")

    def text(self) -> str:
        return _normalize_whitespace("".join(self._chunks))


def _decode_body(body: bytes, content_type_header: str) -> str:
    charset = "utf-8"
    if "charset=" in content_type_header.lower():
        charset = content_type_header.split("charset=", 1)[1].split(";", 1)[0].strip()
    for encoding in [charset, "utf-8", "latin-1"]:
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            continue
    return body.decode("utf-8", errors="replace")


def _guess_extension(url: str, content_type_header: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix:
        return suffix
    content_type = content_type_header.split(";", 1)[0].strip().lower()
    if content_type == "application/pdf":
        return ".pdf"
    if content_type in {"text/html", "application/xhtml+xml"}:
        return ".html"
    if content_type in {"application/json"}:
        return ".json"
    if content_type in {"text/plain"}:
        return ".txt"
    if content_type in {"text/csv", "application/csv"}:
        return ".csv"
    return ".txt"


def _is_pdf(url: str, content_type_header: str) -> bool:
    extension = _guess_extension(url, content_type_header)
    return extension in PDF_EXTENSIONS or content_type_header.lower().startswith("application/pdf")


def _is_textual(url: str, content_type_header: str) -> bool:
    if _is_pdf(url, content_type_header):
        return False
    content_type = content_type_header.split(";", 1)[0].strip().lower()
    if content_type in TEXTUAL_CONTENT_TYPES:
        return True
    extension = _guess_extension(url, content_type_header)
    return extension in {".html", ".htm", ".txt", ".json", ".xml", ".csv"}


class RawSourceClient:
    def __init__(
        self,
        *,
        max_text_chars: int = 24000,
        timeout_seconds: int | None = 90,
    ) -> None:
        self.max_text_chars = max(2000, max_text_chars)
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        )

    def collect_evidence(
        self,
        *,
        source_refs: list[dict[str, Any]],
        run_dir: Path,
        category: str = "financials",
    ) -> tuple[list[EvidenceItem], list[dict[str, Any]]]:
        evidence: list[EvidenceItem] = []
        manifest: list[dict[str, Any]] = []

        for index, ref in enumerate(source_refs, start=1):
            manifest_item, evidence_item = self._collect_single(
                index=index,
                ref=ref,
                run_dir=run_dir,
                category=category,
            )
            manifest.append(manifest_item)
            if evidence_item is not None:
                evidence.append(evidence_item)

        return evidence, manifest

    def _collect_single(
        self,
        *,
        index: int,
        ref: dict[str, Any],
        run_dir: Path,
        category: str,
    ) -> tuple[dict[str, Any], EvidenceItem | None]:
        label = str(ref.get("label", "")).strip() or f"Source {index}"
        url = str(ref.get("url", "")).strip()
        source_type = str(ref.get("source_type", "")).strip()
        source_kind = str(ref.get("source_kind", "")).strip()
        reporting_period = str(ref.get("reporting_period", "")).strip()
        notes = str(ref.get("notes", "")).strip()
        discovery_origin = str(ref.get("discovery_origin", "")).strip()
        discovery_run_id = str(ref.get("discovery_run_id", "")).strip()
        if not url:
            return (
                {
                    "label": label,
                    "url": url,
                    "status": "skipped",
                    "reason": "Missing URL",
                },
                None,
            )

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": (
                    "application/pdf,text/html,application/xhtml+xml,"
                    "application/json,text/plain,text/csv,*/*"
                ),
            },
        )
        try:
            if self.timeout_seconds is None:
                response_context = urllib.request.urlopen(request)
            else:
                response_context = urllib.request.urlopen(
                    request,
                    timeout=self.timeout_seconds,
                )
            with response_context as response:
                body = response.read()
                final_url = response.geturl()
                content_type_header = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return (
                {
                    "label": label,
                    "url": url,
                    "status": "error",
                    "error": f"HTTP {exc.code}: {detail.strip()}",
                },
                None,
            )
        except urllib.error.URLError as exc:
            return (
                {
                    "label": label,
                    "url": url,
                    "status": "error",
                    "error": str(exc.reason),
                },
                None,
            )

        final_url = str(final_url).strip() or url
        extension = _guess_extension(final_url, content_type_header)
        raw_dir = run_dir / "raw_sources"
        raw_dir.mkdir(parents=True, exist_ok=True)
        artifact_relpath = ""
        raw_text = ""
        fetch_mode = "binary_reference"

        if _is_pdf(final_url, content_type_header):
            fetch_mode = "pdf_file_url"
            artifact_name = f"{index:03d}-{_slugify(label)}{extension}"
            artifact_path = raw_dir / artifact_name
            artifact_path.write_bytes(body)
            artifact_relpath = str(artifact_path.relative_to(run_dir))
        elif _is_textual(final_url, content_type_header):
            decoded = _decode_body(body, content_type_header)
            if content_type_header.lower().startswith(("text/html", "application/xhtml+xml")):
                parser = _HTMLTextExtractor()
                parser.feed(decoded)
                raw_text = parser.text()
                fetch_mode = "html_text"
            else:
                raw_text = _normalize_whitespace(decoded)
                fetch_mode = "text_fetch"
            raw_text = raw_text[: self.max_text_chars].strip()
            artifact_name = f"{index:03d}-{_slugify(label)}.txt"
            artifact_path = raw_dir / artifact_name
            artifact_path.write_text(raw_text, encoding="utf-8")
            artifact_relpath = str(artifact_path.relative_to(run_dir))
        else:
            artifact_name = f"{index:03d}-{_slugify(label)}{extension}"
            artifact_path = raw_dir / artifact_name
            artifact_path.write_bytes(body)
            artifact_relpath = str(artifact_path.relative_to(run_dir))

        metadata = {
            "source_type": source_type,
            "source_kind": source_kind,
            "reporting_period": reporting_period,
            "discovery_origin": discovery_origin,
            "discovery_run_id": discovery_run_id,
            "direct_fetch_mode": fetch_mode,
            "direct_fetch_content_type": content_type_header,
            "direct_fetch_artifact_path": artifact_relpath,
            "direct_fetch_final_url": final_url,
            "openai_input_text": raw_text,
        }
        if fetch_mode == "pdf_file_url":
            metadata["openai_input_file_url"] = final_url
            metadata["openai_input_file_path"] = artifact_relpath
            metadata["openai_input_filename"] = artifact_name

        evidence = EvidenceItem(
            evidence_id=f"direct_{_slugify(category)}_{index:03d}",
            label=label,
            category=category,
            url=final_url,
            retrieved_at=utc_now_iso(),
            content=self._render_evidence_summary(
                label=label,
                url=final_url,
                source_type=source_type,
                source_kind=source_kind,
                reporting_period=reporting_period,
                notes=notes,
                fetch_mode=fetch_mode,
                raw_text=raw_text,
            ),
            metadata=metadata,
        )

        return (
            {
                "label": label,
                "url": url,
                "final_url": final_url,
                "status": "fetched",
                "source_type": source_type,
                "source_kind": source_kind,
                "reporting_period": reporting_period,
                "discovery_origin": discovery_origin,
                "discovery_run_id": discovery_run_id,
                "fetch_mode": fetch_mode,
                "content_type": content_type_header,
                "artifact_path": artifact_relpath,
                "has_openai_file_input": bool(metadata.get("openai_input_file_url")),
                "has_openai_text_input": bool(raw_text),
            },
            evidence,
        )

    def _render_evidence_summary(
        self,
        *,
        label: str,
        url: str,
        source_type: str,
        source_kind: str,
        reporting_period: str,
        notes: str,
        fetch_mode: str,
        raw_text: str,
    ) -> str:
        lines = [
            f"Source label: {label}",
            f"Source URL: {url}",
            f"Source type: {source_type}",
            f"Source kind: {source_kind}",
            f"Reporting period: {reporting_period}",
            f"Fetch mode: {fetch_mode}",
        ]
        if notes:
            lines.append(f"Notes: {notes}")
        if raw_text:
            lines.extend(
                [
                    "Raw source text:",
                    raw_text,
                ]
            )
        return "\n".join(line for line in lines if line).strip()
