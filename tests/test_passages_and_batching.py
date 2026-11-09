from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gus_reporter.events import RunEvents
from gus_reporter.models import EvidenceItem, ExtractedClaim
from gus_reporter.passages import (
    QUOTE_WINDOW_CHARS,
    context_for_claim,
    quote_spans,
    render_passages,
)
from gus_reporter.storage import RunStorage
from gus_reporter.verification import ClaimVerifier, batch_user_content, plan_batches

FILLER = "Lorem ipsum dolor sit amet. " * 800  # about 22,000 characters


def _text(evidence_id: str, text: str) -> EvidenceItem:
    return EvidenceItem(evidence_id, evidence_id, "financials", f"https://x.example/{evidence_id}",
                        "t", f"Source label: {evidence_id}", {"openai_input_text": text})


def _pdf(evidence_id: str) -> EvidenceItem:
    return EvidenceItem(evidence_id, evidence_id, "financials", f"https://x.example/{evidence_id}.pdf",
                        "t", "Source label: annual report",
                        {"openai_input_file_url": f"https://x.example/{evidence_id}.pdf",
                         "openai_input_text": ""})


def _claim(claim_id: str, statement: str, quote: str, ids: list[str]) -> ExtractedClaim:
    return ExtractedClaim(claim_id, "financials", statement, "high", 0.9, "", quote, ids, "agent")


class PassageTests(unittest.TestCase):
    def test_quote_is_found_across_case_and_whitespace(self) -> None:
        text = "Header.\n\nNet   LOSS was\n$6.4 million in FY2025."
        (start, end), = quote_spans(text, "net loss was $6.4 million")
        self.assertEqual("Net   LOSS was\n$6.4 million", text[start:end])

    def test_quote_past_the_old_12000_character_cut_is_shown(self) -> None:
        item = _text("ar", FILLER + "Revenue was $48.2 million." + FILLER)
        context = context_for_claim("Revenue was $48.2 million.", "Revenue was $48.2 million", [item])
        self.assertTrue(context.quote_in_text)
        rendered = render_passages([item], context.spans)
        self.assertIn("Revenue was $48.2 million.", rendered)
        self.assertLess(len(rendered), 2 * QUOTE_WINDOW_CHARS + 2500)

    def test_statement_matching_chunk_is_included_beyond_the_quote(self) -> None:
        # The quote supports the claim, but a contradiction sits far away.
        item = _text(
            "ir",
            "Revenue grew 31% to $48.2 million. " + FILLER
            + "Restated: FY2025 revenue was restated down to $41.0 million after an audit. " + FILLER,
        )
        context = context_for_claim(
            "FY2025 revenue was $48.2 million.", "Revenue grew 31% to $48.2 million", [item]
        )
        self.assertIn("restated down to $41.0 million", render_passages([item], context.spans))

    def test_missing_quote_falls_back_to_wider_context(self) -> None:
        items = [_text("ir", "Revenue grew. " + FILLER + "Gross margin 41% for the year. " + FILLER)]
        context = context_for_claim("Gross margin was 41%.", "gross margin was 41%", items)
        self.assertFalse(context.quote_in_text)
        self.assertIn("Gross margin 41% for the year", render_passages(items, context.spans))

    def test_unsearchable_pdfs_are_shown_even_when_the_quote_is_in_text(self) -> None:
        # The PDF might contradict a quote found elsewhere, and its text cannot be searched.
        items = [_text("ir", "Gross margin was 41%."), _pdf("ar")]
        self.assertEqual(["ar"], context_for_claim("Margin 41%.", "gross margin was 41%", items).pdf_ids)

    def test_a_short_source_is_shown_whole(self) -> None:
        # A decisive note sharing no words with the claim must still be visible.
        note = "The board withdrew the previously published results pending restatement."
        item = _text("ir", "Revenue was $48.2 million in FY2025. " * 40 + note)
        context = context_for_claim("Revenue was $48.2 million.", "Revenue was $48.2 million", [item])
        self.assertIn(note, render_passages([item], context.spans))

    def test_a_common_quote_cannot_flood_the_prompt(self) -> None:
        item = _text("ar", ("net sales rose. " + "Lorem ipsum dolor sit amet. " * 100) * 80)
        context = context_for_claim("Net sales rose.", "net sales", [item])
        self.assertLess(len(render_passages([item], context.spans)), 12_000)


class BatchPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = {"ir": _text("ir", "Revenue was $10. Cash was $5."), "ar": _pdf("ar")}

    def test_claims_sharing_a_bundle_are_batched_six_at_a_time(self) -> None:
        text_only = {"ir": self.items["ir"]}
        claims = [_claim(f"t{i}", "Revenue was $10.", "Revenue was $10", ["ir"]) for i in range(8)]
        claims += [_claim(f"p{i}", "Debt was $3.", "debt was $3", ["ir", "ar"]) for i in range(7)]
        batches = plan_batches(claims, {**text_only, "ar": self.items["ar"]}, batch_size=6)
        sizes = [(len(b.claims), bool(b.pdf_ids), b.warms_cache) for b in batches]
        self.assertEqual([(6, False, False), (2, False, False), (6, True, True), (1, True, False)], sizes)

    def test_pdf_batches_put_the_attachment_first_and_claims_last(self) -> None:
        claims = [_claim("p0", "Debt was $3.", "debt was $3", ["ir", "ar"])]
        (batch,) = plan_batches(claims, self.items)
        content = batch_user_content(batch, None)
        self.assertEqual("input_file", content[0]["type"])
        self.assertIn("Claims to verify", content[-1]["text"])
        self.assertNotIn("Claim ID", content[-2]["text"])

    def test_two_pdf_batches_over_one_bundle_share_their_leading_attachment(self) -> None:
        claims = [_claim(f"p{i}", f"Debt was ${i}.", f"debt was ${i}", ["ir", "ar"]) for i in range(7)]
        first, second = plan_batches(claims, self.items, batch_size=6)
        self.assertEqual(batch_user_content(first, None)[0], batch_user_content(second, None)[0])


class BatchFailureTests(unittest.TestCase):
    def _verify(self, responder):  # type: ignore[no-untyped-def]
        class Model:
            def structured_response(self, **kwargs):  # type: ignore[no-untyped-def]
                return responder(**kwargs)

        claims = [_claim(f"c{i}", "Revenue was $10.", "Revenue was $10", ["ir"]) for i in range(3)]
        with tempfile.TemporaryDirectory() as temp:
            verifier = ClaimVerifier(llm=Model(), events=RunEvents(RunStorage(Path(temp))), max_workers=2)
            return verifier.verify(claims, [_text("ir", "Revenue was $10.")], Path(temp))

    def test_a_failed_batch_suppresses_every_claim_in_it(self) -> None:
        def boom(**_):  # type: ignore[no-untyped-def]
            raise RuntimeError("timeout")

        self.assertEqual({"insufficient_evidence"}, {c.status for c in self._verify(boom)})

    def test_a_verdict_for_an_unknown_claim_id_is_ignored(self) -> None:
        def stray(**_):  # type: ignore[no-untyped-def]
            return {"verdicts": [{"claim_id": "c9", "status": "supported", "confidence": 1,
                                  "rationale": "", "supporting_evidence_ids": [], "blocking_issues": []}]}

        self.assertEqual({"insufficient_evidence"}, {c.status for c in self._verify(stray)})

    def test_supported_verdicts_with_a_present_quote_are_kept(self) -> None:
        def accept_all(*, user_content, **_):  # type: ignore[no-untyped-def]
            return {"verdicts": [{"claim_id": f"c{i}", "status": "supported", "confidence": 1,
                                  "rationale": "", "supporting_evidence_ids": ["ir"],
                                  "blocking_issues": []} for i in range(3)]}

        self.assertEqual({"supported"}, {c.status for c in self._verify(accept_all)})


if __name__ == "__main__":
    unittest.main()
