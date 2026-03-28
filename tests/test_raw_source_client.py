from __future__ import annotations

import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from gus_reporter.raw_source_client import RawSourceClient


class _FakeResponse:
    def __init__(self, *, body: bytes, url: str, content_type: str) -> None:
        self._body = body
        self._url = url
        headers = Message()
        headers["Content-Type"] = content_type
        self.headers = headers

    def read(self) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb


class RawSourceClientTests(unittest.TestCase):
    def test_collect_evidence_extracts_text_from_html(self) -> None:
        client = RawSourceClient(max_text_chars=4000, timeout_seconds=30)
        html = (
            "<html><body><h1>Apple Investor Relations</h1>"
            "<p>Revenue was $124.3 billion for the quarter.</p></body></html>"
        ).encode("utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            with patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(
                    body=html,
                    url="https://investor.apple.com/",
                    content_type="text/html; charset=utf-8",
                ),
            ):
                evidence, manifest = client.collect_evidence(
                    source_refs=[
                        {
                            "label": "Apple Investor Relations",
                            "url": "https://investor.apple.com/",
                            "source_type": "financial_investor_relations",
                        }
                    ],
                    run_dir=run_dir,
                )
            artifact_path = run_dir / manifest[0]["artifact_path"]
            self.assertTrue(artifact_path.exists())

        self.assertEqual(1, len(evidence))
        self.assertEqual(1, len(manifest))
        self.assertEqual("html_text", evidence[0].metadata["direct_fetch_mode"])
        self.assertIn("$124.3 billion", evidence[0].metadata["openai_input_text"])

    def test_collect_evidence_marks_pdf_as_openai_file_input(self) -> None:
        client = RawSourceClient(max_text_chars=4000, timeout_seconds=30)
        pdf_bytes = b"%PDF-1.4 test"

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            with patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(
                    body=pdf_bytes,
                    url="https://example.com/report.pdf",
                    content_type="application/pdf",
                ),
            ):
                evidence, manifest = client.collect_evidence(
                    source_refs=[
                        {
                            "label": "Annual report",
                            "url": "https://example.com/report.pdf",
                            "source_type": "financial_document",
                        }
                    ],
                    run_dir=run_dir,
                )

        self.assertEqual(1, len(evidence))
        self.assertEqual(1, len(manifest))
        self.assertEqual("pdf_file_url", evidence[0].metadata["direct_fetch_mode"])
        self.assertEqual(
            "https://example.com/report.pdf",
            evidence[0].metadata["openai_input_file_url"],
        )


if __name__ == "__main__":
    unittest.main()
