from __future__ import annotations

import unittest

from gus_reporter.models import EvidenceItem, ExtractedClaim, VerifiedClaim
from gus_reporter.verification import ground_citation


def _item(evidence_id: str, text: str, **metadata: str) -> EvidenceItem:
    return EvidenceItem(evidence_id, evidence_id, "financials", f"https://x.example/{evidence_id}",
                        "t", text, dict(metadata))


EVIDENCE = {
    "a": _item("a", "Revenue grew 12%  to $40 million."),
    "b": _item("b", "Unrelated page."),
    "pdf": _item("pdf", "Source label: AR", openai_input_file_path="raw_sources/ar.pdf"),
}


def _pair(quote: str, model_ids: list[str]) -> tuple[VerifiedClaim, ExtractedClaim]:
    extracted = ExtractedClaim("c", "financials", "Revenue grew 12%.", "high", 0.9, "", quote,
                               ["a", "b", "pdf"], "agent")
    verified = VerifiedClaim("c", "financials", extracted.statement, "supported", 0.9, "ok",
                             model_ids, [], quote, "high", "")
    return verified, extracted


class GroundingTests(unittest.TestCase):
    def test_citation_narrows_to_the_source_containing_the_quote(self) -> None:
        verified, extracted = _pair("revenue grew 12% to $40 million", ["a", "b", "pdf"])
        result = ground_citation(verified, extracted, EVIDENCE)
        self.assertEqual("supported", result.status)
        self.assertEqual(["a"], result.evidence_ids)

    def test_ids_the_claim_was_never_shown_are_dropped(self) -> None:
        verified, extracted = _pair("revenue grew 12% to $40 million", ["ghost"])
        self.assertEqual(["a"], ground_citation(verified, extracted, EVIDENCE).evidence_ids)

    def test_quote_missing_everywhere_suppresses_the_claim(self) -> None:
        verified, extracted = _pair("net income doubled", ["a"])
        result = ground_citation(verified, extracted, EVIDENCE)
        self.assertEqual("unsupported", result.status)
        self.assertIn("Grounding check", result.rationale)

    def test_unsearchable_pdf_named_by_the_model_stays_cited(self) -> None:
        verified, extracted = _pair("operating margin was 9%", ["pdf"])
        result = ground_citation(verified, extracted, EVIDENCE)
        self.assertEqual("supported", result.status)
        self.assertEqual(["pdf"], result.evidence_ids)

    def test_empty_quote_is_suppressed(self) -> None:
        verified, extracted = _pair("", ["a"])
        self.assertEqual("unsupported", ground_citation(verified, extracted, EVIDENCE).status)


if __name__ == "__main__":
    unittest.main()
