"""Shared fixtures for the characterisation (golden) tests.

The golden tests pin what the pipeline does today: which source jobs it plans,
which child jobs and raw references it derives from discovery results, what it
sends to the model at every stage, and which artifacts and events it writes.
They exist so the pipeline can be restructured without changing behaviour.

Set GUS_UPDATE_GOLDEN=1 to rewrite the golden files after an intended change.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any

from gus_reporter.models import ReportRequest, SourceJob, TinyfishRunRecord

GOLDEN_DIR = Path(__file__).parent / "golden"
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)?")


def normalise(value: Any, run_dir: str = "") -> Any:
    text = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True)
    if run_dir:
        text = text.replace(run_dir, "<run_dir>")
    return json.loads(_TIMESTAMP.sub("<ts>", text))


def assert_golden(test: Any, name: str, actual: Any) -> None:
    path = GOLDEN_DIR / f"{name}.json"
    rendered = json.dumps(actual, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    if os.getenv("GUS_UPDATE_GOLDEN") == "1" or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        if os.getenv("GUS_UPDATE_GOLDEN") != "1":
            test.fail(f"Golden file {path.name} did not exist and was created; rerun.")
        return
    expected = path.read_text(encoding="utf-8")
    test.maxDiff = None
    test.assertEqual(json.loads(expected), json.loads(rendered), f"golden mismatch: {path.name}")


# --------------------------------------------------------------------------
# Requests used by the planning goldens
# --------------------------------------------------------------------------

def seed_requests() -> dict[str, ReportRequest]:
    return {
        "full": ReportRequest(
            company_name="Example Corp",
            company_website="https://example.com/",
            primary_jurisdiction="us",
            ticker="EXMP",
            investor_relations_url="https://ir.example.com",
            financial_source_urls=["https://example.com/ar-2025.pdf"],
            legal_source_urls=["", " https://court.example.org/case/1 "],
            news_source_urls=["https://news.example.org/story", "  "],
            tinyfish_use_vault=True,
            tinyfish_credential_item_ids=["item_1"],
            report_goal="Diligence memo",
        ),
        "website_default_focus": ReportRequest(
            company_name="Lion City Foods",
            company_website="https://lioncity.example.sg",
            primary_jurisdiction="Singapore",
        ),
        "name_only": ReportRequest(
            company_name="Nameonly Ltd",
            focus_areas=["financials", "legal", "news"],
        ),
        "website_ticker_financials_only": ReportRequest(
            company_name="Ticker Inc",
            company_website="https://ticker.example.com",
            ticker="TICK",
            focus_areas=["financials"],
            tinyfish_enable_agent_memory=True,
        ),
        "explicit_jobs": ReportRequest(
            company_name="Explicit Co",
            source_jobs=[
                SourceJob(
                    label="Given",
                    category="news",
                    url="https://given.example.com",
                    goal="Read it",
                )
            ],
        ),
    }


def discovery_request() -> ReportRequest:
    return ReportRequest(
        company_name="Example Corp",
        company_website="https://example.com",
        primary_jurisdiction="GB",
        ticker="EXMP",
        investor_relations_url="https://ir.example.com",
        financial_source_urls=[" https://example.com/AR.pdf ", "https://example.com/q1.pdf"],
        legal_source_urls=["", " https://court.example.org/case/9 "],
        news_source_urls=["https://news.example.org/a"],
    )


def discovery_runs() -> list[TinyfishRunRecord]:
    def run(run_id: str, category: str, url: str, source_type: str, result: Any) -> TinyfishRunRecord:
        return TinyfishRunRecord(
            run_id=run_id,
            status="COMPLETED",
            goal="goal",
            label=f"Seed {run_id}",
            category=category,
            url=url,
            source_type=source_type,
            result=result,
        )

    return [
        run(
            "fin_1",
            "financials",
            "https://ir.example.com",
            "financial_investor_relations",
            {
                "sources": [
                    {
                        "page_title": "Investors hub",
                        "final_url": "https://example.com/investors/overview",
                        "source_kind": "",
                        "reporting_period": "FY2025",
                        "notes": "hub page",
                        "linked_documents": [
                            {
                                "title": "Annual Report 2025",
                                "url": "https://example.com/files/annual-2025.pdf",
                                "document_type": "Annual report",
                            },
                            {"title": "", "url": "", "document_type": ""},
                            "not a dict",
                            {"title": "", "url": "https://example.com/files/deck", "document_type": ""},
                        ],
                    },
                    {
                        "page_title": "",
                        "final_url": "https://WWW.SEC.GOV/cgi-bin/browse-edgar?CIK=EXMP#frag",
                        "source_kind": "Regulator filing index",
                    },
                    {
                        "page_title": "Duplicate of a hint",
                        "final_url": "https://example.com/AR.pdf",
                        "source_kind": "annual",
                    },
                    {"page_title": "No URL", "final_url": "  ", "linked_documents": "oops"},
                    "skip me",
                ]
            },
        ),
        run("fin_2", "financials", "https://example.com/ar-direct.pdf", "financial_document", None),
        run("fin_3", "financials", "https://example.com/home", "company_homepage", "plain string"),
        run("fin_4", "financials", "https://example.com/x", "financial_document", {"sources": "bad"}),
        run(
            "leg_1",
            "legal",
            "https://www.bing.com/search?q=lawsuit",
            "legal_search_discovery",
            {
                "sources": [
                    {
                        "page_title": "Docket 22-cv-1",
                        "final_url": "https://www.courtlistener.com/docket/1/",
                        "source_kind": "",
                        "notes": "civil",
                        "linked_documents": [
                            {
                                "title": "Complaint",
                                "url": " https://storage.courtlistener.com/complaint.pdf ",
                                "document_type": "complaint",
                            }
                        ],
                    },
                    {
                        "page_title": "FTC press",
                        "final_url": "https://www.ftc.gov/news/press-releases/x",
                        "source_kind": "press",
                    },
                    {
                        "page_title": "Registry",
                        "final_url": "https://opencorporates.com/companies/gb/1",
                        "source_kind": "",
                        "linked_documents": [
                            {"title": "", "url": "https://opencorporates.com/filings/2", "document_type": ""}
                        ],
                    },
                    {
                        "page_title": "OFAC",
                        "final_url": "https://home.treasury.gov/ofac/sdn",
                        "source_kind": "",
                    },
                ]
            },
        ),
        run("leg_2", "legal", "https://court.example.org/seeded", "legal_court_records", None),
        run("leg_3", "legal", "https://example.org/registry", "legal_corporate_registry", None),
        run(
            "news_1",
            "news",
            "https://www.bing.com/news/search?q=Example",
            "news_search_discovery",
            {
                "sources": [
                    {
                        "page_title": "Reuters story",
                        "final_url": "https://www.reuters.com/markets/example",
                        "source_kind": "",
                        "notes": "markets",
                        "linked_documents": [
                            {"title": "Wire copy", "url": "https://www.prnewswire.com/news/x", "document_type": "pr"},
                            {"title": "", "url": "https://blog.example.net/post", "document_type": ""},
                        ],
                    },
                    {
                        "page_title": "",
                        "final_url": "https://techcrunch.com:443/2026/01/01/example",
                        "source_kind": "",
                    },
                    {
                        "page_title": "Coverage",
                        "final_url": "https://unknown.example.io/x",
                        "source_kind": "coverage",
                    },
                ]
            },
        ),
        run("news_2", "news", "https://news.example.org/a", "news_document", None),
    ]


# --------------------------------------------------------------------------
# End-to-end fakes
# --------------------------------------------------------------------------

E2E_REQUEST = {
    "company_name": "Golden Widgets",
    "company_website": "https://golden.example.com",
    "primary_jurisdiction": "US",
    "investor_relations_url": "https://golden.example.com/ir",
    "financial_source_urls": ["https://golden.example.com/ar-2025.pdf"],
    "focus_areas": ["company_overview", "financials", "legal", "news", "risk_signals"],
}

_TINYFISH_RESULTS: dict[str, dict[str, Any]] = {
    "https://golden.example.com": {
        "status": "COMPLETED",
        "result": {
            "capture_mode": "page",
            "sources": [
                {
                    "page_title": "Golden Widgets",
                    "final_url": "https://golden.example.com",
                    "source_kind": "homepage",
                    "captured_text": "Golden Widgets makes industrial widgets in Ohio.",
                }
            ],
        },
    },
    "https://golden.example.com/ir": {
        "status": "COMPLETED",
        "result": {
            "sources": [
                {
                    "page_title": "Investor relations",
                    "final_url": "https://golden.example.com/investors/filings",
                    "source_kind": "investor relations",
                    "reporting_period": "FY2025",
                    "linked_documents": [
                        {
                            "title": "Q3 results",
                            "url": "https://golden.example.com/q3.html",
                            "document_type": "quarterly",
                        }
                    ],
                }
            ],
            "coverage_gaps": ["No 10-K found"],
        },
    },
    "https://golden.example.com/investors/filings": {
        "status": "COMPLETED",
        "result": {"sources": []},
    },
    "https://golden.example.com/news": {
        "status": "FAILED",
        "error": {"message": "Blocked by CAPTCHA challenge"},
        "steps": [{"timestamp": "t", "status": "x", "action": "open", "duration": 1}],
    },
}


def fake_tinyfish_run_jobs(jobs: list[SourceJob]) -> list[TinyfishRunRecord]:
    from gus_reporter.tinyfish_client import looks_access_blocked

    records = []
    for index, job in enumerate(jobs):
        data = _TINYFISH_RESULTS.get(job.url, {"status": "COMPLETED", "result": None})
        record = TinyfishRunRecord(
            run_id=f"tf_{hashlib.sha1(job.url.encode()).hexdigest()[:8]}",
            status=data["status"],
            goal=job.goal,
            label=job.label,
            category=job.category,
            url=job.url,
            source_type=job.source_type,
            created_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:01:00+00:00",
            num_of_steps=3 + index,
            steps=list(data.get("steps", [])),
            attempts=[{"attempt_number": 1, "browser_profile": job.browser_profile}],
            result=data.get("result"),
            error=data.get("error"),
        )
        record.access_blocked = looks_access_blocked(record)
        records.append(record)
    return records


_RAW_PAGES: dict[str, tuple[str, bytes]] = {
    "https://golden.example.com/ir": (
        "text/html; charset=utf-8",
        b"<html><body><h1>IR</h1><p>Revenue grew 12% to $40.1 million in FY2025.</p>"
        b"<script>ignored()</script></body></html>",
    ),
    "https://golden.example.com/ar-2025.pdf": ("application/pdf", b"%PDF-1.4 golden annual report"),
    "https://golden.example.com/investors/filings": (
        "text/plain",
        b"Net loss was $2.3 million. Cash on hand $9.8 million.",
    ),
}


class _FakeResponse:
    def __init__(self, url: str, content_type: str, body: bytes) -> None:
        self._url = url
        self._body = body
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def read(self) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def fake_urlopen(request: Any, timeout: Any = None) -> _FakeResponse:
    url = request.full_url
    if url not in _RAW_PAGES:
        raise urllib.error.HTTPError(url, 404, "Not Found", Message(), io.BytesIO(b"missing"))
    content_type, body = _RAW_PAGES[url]
    return _FakeResponse(url, content_type, body)


class ScriptedModel:
    """Deterministic stand-in for the OpenAI client that logs every call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

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
    ) -> dict[str, Any]:
        content = user_content or [{"type": "input_text", "text": user_prompt}]
        self.calls.append(
            {
                "schema_name": schema_name,
                "schema_sha": hashlib.sha256(
                    json.dumps(schema, sort_keys=True).encode()
                ).hexdigest()[:16],
                "system_prompt": system_prompt,
                "user_text": content[0].get("text", ""),
                "files": [
                    {
                        k: (hashlib.sha256(v.encode()).hexdigest()[:16] if k == "file_data" else v)
                        for k, v in part.items()
                    }
                    for part in content[1:]
                ],
                "max_output_tokens": max_output_tokens,
                "user_prompt_used": user_content is None,
            }
        )
        if schema_name.endswith("_claim_extraction"):
            if "capital_structure_agent" in schema_name:
                raise RuntimeError("simulated rate limit")
            section = schema["properties"]["claims"]["items"]["properties"]["section"]["enum"][0]
            return {
                "document_summary": "",
                "claims": [
                    {
                        "section": section,
                        "statement": f"{schema_name} claim {n}",
                        "importance": ["high", "medium", "low"][n % 3],
                        "confidence": 0.5 + n / 10,
                        "as_of": "2025",
                        "source_quote": "Revenue grew 12%",
                    }
                    for n in range(1 + len(schema_name) % 2)
                ],
            }
        if schema_name == "claim_verification":
            text = content[0].get("text", "")
            claim_id = text.split("Claim ID: ", 1)[1].split("\n", 1)[0]
            bucket = sum(ord(ch) for ch in claim_id) % 5
            if bucket == 4:
                raise RuntimeError("simulated verifier timeout")
            status = ["supported", "supported", "unsupported", "contradicted"][bucket]
            return {
                "status": status,
                "confidence": 0.9,
                "rationale": f"bucket {bucket}",
                "supporting_evidence_ids": ["direct_financials_001"] if bucket == 1 else [],
                "blocking_issues": [] if status == "supported" else ["not in source"],
            }
        if schema_name == "legal_section_summary":
            raise RuntimeError("simulated synthesis failure")
        if schema_name.endswith("_section_summary"):
            payload: dict[str, Any] = {
                "summary": f"Summary for {schema_name}",
                "highlights": ["h1", "h2"],
                "recommendations": ["r1"],
            }
            for key, spec in schema.get("properties", {}).items():
                if key not in payload:
                    payload[key] = [] if spec.get("type") == "array" else "mixed"
            return payload
        if schema_name == "executive_company_report":
            return {
                "executive_summary": "Exec summary",
                "scope_and_limitations": "Scope",
                "methodology": "Method",
                "entity_profile": "Profile",
                "diligence_conclusion": "Conclusion",
                "key_findings": ["k1", " ", "k2"],
                "recommendations": ["rec"],
                "pdf_section_order": [],
            }
        raise AssertionError(f"unexpected schema {schema_name}")
