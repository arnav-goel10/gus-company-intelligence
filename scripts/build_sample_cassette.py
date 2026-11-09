"""Build the committed offline sample in examples/sample_run/.

The sample is a fictional company, Quillfeather Robotics, on reserved
.example domains. Its web pages and its model responses are authored here by
hand, because recording a real run costs money. Everything between them is
the real pipeline: planning, browse waves, discovery, raw fetch and HTML
parsing, evidence bundling, the publish gate, the risk matrix and the
renderer. The authored verifier makes four scripted rejections and accepts
everything else, so one fabricated claim reaches the pipeline's own
grounding check, which suppresses it.

Run from the repo root:

    PYTHONPATH=src python3 scripts/build_sample_cassette.py

To record a real run instead, use `python -m gus_reporter.cli --record DIR`.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from gus_reporter import cli
from gus_reporter.config import AppConfig
from gus_reporter.models import ReportRequest, SourceJob, TinyfishRunRecord
from gus_reporter.pipeline import ReportPipeline
from gus_reporter.raw_source_client import FetchedSource, RawSourceClient
from gus_reporter.replay import Cassette, RecordingBrowser, RecordingFetcher, RecordingModel
from gus_reporter.tinyfish_client import looks_access_blocked
from gus_reporter.urls import normalize_url

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample_run"
SITE = "https://quillfeather.example"
FIXED_TIME = "2026-11-09T00:00:00+00:00"

# --------------------------------------------------------------------------
# Authored web: what the browse agent "saw" and what the raw fetcher downloads
# --------------------------------------------------------------------------


def _page(title: str, url: str, kind: str, text: str = "", linked: list[dict] | None = None,
          period: str = "") -> dict[str, Any]:
    return {
        "page_title": title,
        "final_url": url,
        "source_kind": kind,
        "reporting_period": period,
        "captured_text": text,
        "linked_documents": linked or [],
        "notes": "",
    }


BROWSE: dict[str, dict[str, Any]] = {
    SITE: {
        "status": "COMPLETED",
        "result": {
            "capture_mode": "page",
            "sources": [
                _page(
                    "Quillfeather Robotics",
                    SITE,
                    "company homepage",
                    "Quillfeather Robotics designs autonomous inspection drones for offshore wind "
                    "farms. Founded in 2017, headquartered in Portland, Oregon, with about 240 "
                    "employees and customers in six countries.",
                )
            ],
        },
    },
    f"{SITE}/leadership": {
        "status": "COMPLETED",
        "result": {
            "sources": [
                _page(
                    "Leadership",
                    f"{SITE}/leadership",
                    "leadership page",
                    "Mara Lindqvist, Chief Executive Officer and co-founder. Daniel Osei, Chief "
                    "Financial Officer since March 2025.",
                )
            ]
        },
    },
    f"{SITE}/careers": {
        "status": "FAILED",
        "error": {"message": "Page showed a CAPTCHA challenge; stopped as instructed."},
        "result": None,
    },
    f"{SITE}/news": {
        "status": "COMPLETED",
        "result": {
            "sources": [
                _page(
                    "Newsroom",
                    f"{SITE}/news",
                    "press release index",
                    linked=[
                        {
                            "title": "Quillfeather closes Series C",
                            "url": f"{SITE}/news/series-c",
                            "document_type": "press release",
                        }
                    ],
                )
            ]
        },
    },
    f"{SITE}/investors": {
        "status": "COMPLETED",
        "result": {
            "sources": [
                _page(
                    "Investor relations",
                    f"{SITE}/investors",
                    "investor relations hub",
                    period="FY2025",
                    linked=[
                        {
                            "title": "Annual Report 2025",
                            "url": f"{SITE}/investors/annual-report-2025.html",
                            "document_type": "annual report",
                        }
                    ],
                )
            ]
        },
    },
    "litigation": {
        "status": "COMPLETED",
        "result": {
            "sources": [
                _page(
                    "Aerodyne Systems v. Quillfeather Robotics",
                    "https://dockets.example/case/3-25-cv-01931",
                    "court docket",
                )
            ]
        },
    },
    "recent news": {
        "status": "COMPLETED",
        "result": {
            "sources": [
                _page(
                    "Quillfeather recalls 120 inspection drones",
                    "https://windpower-news.example/2026/01/quillfeather-recall",
                    "news article",
                )
            ]
        },
    },
}

RAW: dict[str, tuple[str, str]] = {
    f"{SITE}/investors": (
        "text/html; charset=utf-8",
        "<html><body><h1>Investor relations</h1>"
        "<p>FY2025 revenue $48.2 million, up 31% from $36.8 million in FY2024.</p>"
        "<p>Net loss $6.4 million. Cash and equivalents $22.5 million at 31 December 2025.</p>"
        "<p><a href='/investors/annual-report-2025.html'>Annual Report 2025</a></p>"
        "</body></html>",
    ),
    f"{SITE}/investors/annual-report-2025.html": (
        "text/html; charset=utf-8",
        "<html><body><h1>Annual Report 2025</h1>"
        "<p>Revenue for the year was $48.2 million. Gross margin was 41%.</p>"
        "<p>Our largest customer, Brightwater Energy, accounted for 28% of revenue.</p>"
        "<p>In June 2025 we issued a $15 million convertible note due 2027.</p>"
        "<script>trackPageView()</script></body></html>",
    ),
    f"{SITE}/news/series-c": (
        "text/html; charset=utf-8",
        "<html><body><h1>Quillfeather closes Series C</h1>"
        "<p>12 February 2026. Quillfeather Robotics raised $60 million in a Series C round "
        "led by Harbor Point Ventures.</p></body></html>",
    ),
    "https://dockets.example/case/3-25-cv-01931": (
        "text/plain; charset=utf-8",
        "Aerodyne Systems v. Quillfeather Robotics. Patent infringement complaint filed "
        "3 September 2025 in the District of Oregon. Status: pending.",
    ),
    "https://windpower-news.example/2026/01/quillfeather-recall": (
        "text/html; charset=utf-8",
        "<html><body><article><h1>Quillfeather recalls 120 inspection drones</h1>"
        "<p>Quillfeather Robotics recalled 120 drones in January 2026 after reports of "
        "battery faults.</p></article></body></html>",
    ),
}


class AuthoredBrowser:
    def run_jobs(self, jobs: list[SourceJob]) -> list[TinyfishRunRecord]:
        records = []
        for job in jobs:
            key = job.url
            if "bing.com/search" in job.url and "lawsuit+litigation" in job.url:
                key = "litigation"
            if "bing.com/news/search" in job.url:
                key = "recent news"
            data = BROWSE.get(
                key,
                {
                    "status": "COMPLETED",
                    "result": {"sources": [], "coverage_gaps": ["No relevant results found."]},
                },
            )
            record = TinyfishRunRecord(
                run_id=f"sample_{normalize_url(job.url).encode().hex()[-10:]}",
                status=data["status"],
                goal=job.goal,
                label=job.label,
                category=job.category,
                url=job.url,
                source_type=job.source_type,
                created_at=FIXED_TIME,
                started_at=FIXED_TIME,
                finished_at=FIXED_TIME,
                num_of_steps=None,  # authored, so no billed steps
                attempts=[{"attempt_number": 1, "browser_profile": job.browser_profile}],
                result=data.get("result"),
                error=data.get("error"),
            )
            record.access_blocked = looks_access_blocked(record)
            records.append(record)
        return records


class AuthoredFetcher(RawSourceClient):
    def fetch(self, url: str) -> FetchedSource:
        if url not in RAW:
            return FetchedSource(error="HTTP 404: not found")
        content_type, body = RAW[url]
        return FetchedSource(
            final_url=url, content_type=content_type, body=body.encode(), retrieved_at=FIXED_TIME
        )


# --------------------------------------------------------------------------
# Authored model
# --------------------------------------------------------------------------

EXTRACTIONS: dict[str, list[tuple[str, str, str]]] = {
    # specialist: [(statement, quote, importance)]
    "overview_agent": [
        ("Quillfeather designs autonomous inspection drones for offshore wind farms.",
         "designs autonomous inspection drones for offshore wind farms", "high"),
        ("Quillfeather is headquartered in Portland, Oregon, and has about 240 employees.",
         "headquartered in Portland, Oregon, with about 240 employees", "medium"),
    ],
    "leadership_agent": [
        ("Mara Lindqvist is chief executive and a co-founder.",
         "Mara Lindqvist, Chief Executive Officer and co-founder", "high"),
        ("Daniel Osei has been chief financial officer since March 2025.",
         "Daniel Osei, Chief Financial Officer since March 2025", "medium"),
    ],
    "financial_statement_agent": [
        ("FY2025 revenue was $48.2 million, up 31% from $36.8 million.",
         "FY2025 revenue $48.2 million, up 31% from $36.8 million", "high"),
        ("Quillfeather reported a net loss of $6.4 million for FY2025.", "Net loss $6.4 million", "high"),
        ("Quillfeather reported a net profit in FY2025.", "Net loss $6.4 million", "high"),
    ],
    "capital_structure_agent": [
        ("Quillfeather issued a $15 million convertible note due 2027.",
         "issued a $15 million convertible note due 2027", "high"),
    ],
    "funding_guidance_agent": [
        ("Management expects FY2026 revenue above $70 million.",
         "we expect FY2026 revenue above $70 million", "medium"),
    ],
    "financial_risk_agent": [
        ("One customer, Brightwater Energy, accounted for 28% of FY2025 revenue.",
         "Brightwater Energy, accounted for 28% of revenue", "high"),
    ],
    "legal_case_locator": [
        ("Aerodyne Systems sued Quillfeather for patent infringement in September 2025; the case is pending.",
         "Patent infringement complaint filed 3 September 2025 in the District of Oregon. Status: pending.",
         "high"),
    ],
    "legal_sanctions_agent": [
        ("Quillfeather does not appear on any sanctions list.", "", "medium"),
    ],
    "legal_corporate_filings_agent": [
        ("Quillfeather was incorporated in Delaware in 2017.", "Founded in 2017", "low"),
    ],
    "news_event_locator": [
        ("Quillfeather raised a $60 million Series C led by Harbor Point Ventures on 12 February 2026.",
         "raised $60 million in a Series C round led by Harbor Point Ventures", "high"),
    ],
    "news_negative_signal_agent": [
        ("Quillfeather recalled 120 drones in January 2026 after battery fault reports.",
         "recalled 120 drones in January 2026 after reports of battery faults", "high"),
    ],
    "news_sentiment_agent": [
        ("Press coverage of Quillfeather is overwhelmingly positive.", "", "low"),
    ],
}

REJECTIONS: dict[str, tuple[str, str]] = {
    "net profit": ("contradicted", "The investor page reports a net loss of $6.4 million for FY2025."),
    "sanctions list": (
        "insufficient_evidence",
        "No sanctions screening source was captured, so absence from a list was never checked.",
    ),
    "Delaware": ("unsupported", "No captured source states where the company is incorporated."),
    "overwhelmingly positive": (
        "insufficient_evidence",
        "Two articles, one negative, cannot support a claim about overall coverage.",
    ),
}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _evidence_blocks(text: str) -> dict[str, str]:
    blocks = {}
    for chunk in text.split("Evidence ID: ")[1:]:
        evidence_id, _, body = chunk.partition("\n")
        blocks[evidence_id.strip()] = body
    return blocks


class AuthoredModel:
    @staticmethod
    def _verdict(claim: str, quote: str, passages: str) -> dict:
        for marker, (status, rationale) in REJECTIONS.items():
            if marker in claim:
                return {"status": status, "confidence": 0.9, "rationale": rationale,
                        "supporting_evidence_ids": [], "blocking_issues": [rationale]}
        # Otherwise the authored verifier accepts the claim, the way a model can
        # be talked into it. The pipeline's grounding check gets the last word.
        hits = [eid for eid, body in _evidence_blocks(passages).items()
                if quote and _squash(quote) in _squash(body)]
        return {"status": "supported", "confidence": 0.9,
                "rationale": "The cited source states this.",
                "supporting_evidence_ids": hits[:1], "blocking_issues": []}

    def structured_response(self, *, schema_name: str, schema: dict, system_prompt: str,
                            user_prompt: str = "", user_content: list | None = None,
                            max_output_tokens: int = 4000, background: bool = True) -> dict:
        parts = [part["text"] for part in (user_content or [{"type": "input_text", "text": user_prompt}])
                 if part.get("type") == "input_text"]
        text = "\n\n".join(parts)
        if schema_name.endswith("_claim_extraction"):
            specialist = (system_prompt + "\n" + text).split("Specialist: ", 1)[1].split("\n", 1)[0]
            section = schema["properties"]["claims"]["items"]["properties"]["section"]["enum"][0]
            return {
                "document_summary": "",
                "claims": [
                    {"section": section, "statement": statement, "importance": importance,
                     "confidence": 0.8, "as_of": "2026-02", "source_quote": quote}
                    for statement, quote, importance in EXTRACTIONS.get(specialist, [])
                ],
            }
        if schema_name == "claim_verification_batch":
            passages = next(part for part in parts if part.startswith("Evidence passages:"))
            claims_part = parts[-1].split("Claims to verify:", 1)[1]
            verdicts = []
            for block in claims_part.split("Claim ID: ")[1:]:
                claim_id = block.split("\n", 1)[0].strip()
                claim = block.split("Claim: ", 1)[1].split("\n", 1)[0]
                quote = block.split("Quoted support from extraction: ", 1)[1].split("\n", 1)[0].strip()
                verdicts.append({"claim_id": claim_id, **self._verdict(claim, quote, passages)})
            return {"verdicts": verdicts}
        if schema_name.endswith("_section_summary"):
            statements = [line.split("- Statement: ", 1)[1].split(" | ", 1)[0]
                          for line in text.splitlines() if line.startswith("- Statement: ")]
            payload: dict[str, Any] = {
                "summary": " ".join(statements),
                "highlights": statements[:3],
                "recommendations": ["Confirm the cited figures against primary filings."],
            }
            for key, spec in schema["properties"].items():
                if key not in payload:
                    payload[key] = [] if spec["type"] == "array" else spec["enum"][0]
            return payload
        if schema_name == "executive_company_report":
            return {
                "executive_summary": (
                    "Quillfeather Robotics is a venture-backed maker of offshore wind inspection "
                    "drones growing revenue 31% to $48.2 million while still loss-making. A pending "
                    "patent suit, a January 2026 drone recall and 28% revenue concentration in one "
                    "customer are the main risks."
                ),
                "scope_and_limitations": "Fictional sample company; see examples/sample_run/README.md.",
                "methodology": "Browse discovery, direct raw-source capture, per-claim verification.",
                "entity_profile": "Inspection drones for offshore wind, Portland, Oregon, about 240 staff.",
                "diligence_conclusion": "Publishable with the listed risks flagged for follow-up.",
                "key_findings": [
                    "Revenue grew 31% to $48.2 million in FY2025; the company is not profitable.",
                    "A patent infringement case filed by Aerodyne Systems is pending.",
                    "One customer accounts for 28% of revenue.",
                ],
                "recommendations": ["Obtain the convertible note terms and the recall root-cause report."],
                "pdf_section_order": [],
            }
        raise AssertionError(f"unexpected schema {schema_name}")


# --------------------------------------------------------------------------


def main() -> None:
    request_payload = json.loads((SAMPLE / "request.json").read_text(encoding="utf-8"))
    cassette_dir = SAMPLE / "cassette"
    if cassette_dir.exists():
        shutil.rmtree(cassette_dir)
    cassette = Cassette(cassette_dir)
    with tempfile.TemporaryDirectory() as scratch:
        config = AppConfig.from_env(require_api_keys=False)
        config = type(config)(**{**config.__dict__, "report_output_dir": Path(scratch),
                                 "openai_extraction_max_workers": 4,
                                 "openai_verification_max_workers": 4})
        pipeline = ReportPipeline(
            config,
            browser=RecordingBrowser(AuthoredBrowser(), cassette),
            fetcher=RecordingFetcher(AuthoredFetcher(), cassette),
            llm=RecordingModel(AuthoredModel(), cassette),
        )
        report, _ = pipeline.run(ReportRequest.from_dict(request_payload, "lite"))
    cassette.save()
    print(f"cassette written: {len(cassette.browser)} browse records, "
          f"{len(cassette.sources)} raw sources, {len(cassette.model)} model calls")
    print(f"{report.publish_decision}: {report.total_claims - report.unsupported_claims} kept, "
          f"{report.unsupported_claims} suppressed")

    # Replay the cassette through the normal CLI and commit what it produces.
    output = SAMPLE / "output"
    if output.exists():
        shutil.rmtree(output)
    with tempfile.TemporaryDirectory() as scratch:
        cli.main([
            "--request", str(SAMPLE / "request.json"),
            "--replay", str(cassette_dir),
            "--output-dir", scratch,
        ])
        (run_dir,) = Path(scratch).iterdir()
        shutil.copytree(run_dir, output)
    print(f"replayed output written to {output.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
