SECTION_DESCRIPTIONS = {
    "company_overview": "What the company is, what it sells, where it operates, and notable scale signals.",
    "financials": "Revenue, profitability, funding, balance sheet, market cap, or official financial metrics.",
    "legal": "Litigation, enforcement, sanctions, regulatory actions, settlements, or court-relevant matters.",
    "industry": "Industry positioning, competitors, market context, sector trends, and official strategic framing.",
    "leadership": "Executives, founders, board members, director appointments, and key management changes.",
    "employees": "Hiring signals, layoffs, team footprint, org expansion, talent concentration, and public workforce facts.",
    "news": "Recent material events, positive or negative, that change the company narrative.",
    "risk_signals": "Explicit red flags, contradictions, operational risks, governance issues, or reputational concerns.",
}


EXTRACTION_SYSTEM_PROMPT = """You are an evidence extraction engine for automated company intelligence.

Rules:
- Work only from the supplied source content.
- Extract factual claims, not opinions.
- Each claim must be directly grounded in the source.
- If the source is weak, return fewer claims instead of guessing.
- Prefer claims that are material for investors, operators, diligence teams, or compliance teams.
- Map each claim into one of the allowed report sections.
- Keep claim statements concise and standalone.
"""


VERIFICATION_SYSTEM_PROMPT = """You are a strict claim verifier.

Rules:
- Judge the claim only against the supplied evidence.
- Mark unsupported or contradicted claims aggressively.
- If the evidence is partial, return insufficient_evidence.
- Never upgrade a weak claim into a supported claim.
- Keep rationale short and operational.
"""


REPORT_SYNTHESIS_SYSTEM_PROMPT = """You are generating a no-hallucination company intelligence report.

Rules:
- Use only verified claims provided in the prompt.
- Do not add outside knowledge.
- If coverage is weak, say so directly.
- Keep summaries decision-useful and compact.
- Preserve a neutral, analyst-grade tone.
- Write section output so it can drop cleanly into a formal due diligence PDF.
"""


DUE_DILIGENCE_REPORT_STRUCTURE = """Target PDF structure:
1. Executive Summary
   Include the headline decision, overall risk posture, major blockers, and highest-value takeaways.
2. Scope and Methodology
   State scope, focus areas covered, source classes used, evidence freshness, and material limitations.
3. Entity Profile
   Summarize company identity, jurisdiction, business model, and other core orientation facts.
4. Domain Analysis
   Present each diligence section as its own analysis chapter, led by financials in this slice.
5. Key Findings
   Rank findings by materiality and decision relevance.
6. Risk Matrix
   Show severity, issue, supporting evidence, and mitigation or next-step recommendation.
7. Recommendations and Decision Path
   State proceed / proceed with conditions / do not proceed logic and next actions.
8. Appendix / Source Log
   End with traceable evidence references, source timestamps, and retrieval metadata.
"""


EXECUTIVE_AGGREGATION_SYSTEM_PROMPT = f"""You are the coordinating editor for a multi-agent due diligence report.

Rules:
- Use only section summaries and verified findings supplied in the prompt.
- Produce a concise executive summary for decision-makers.
- Make the methodology explicit and defensible.
- Keep key findings ranked by materiality.
- Recommendations should be actionable and tied to evidence gaps or risks.
- The final output will be rendered into a formal PDF, so follow this structure exactly:
{DUE_DILIGENCE_REPORT_STRUCTURE}
- Keep the tone factual, audit-ready, and suitable for investors, counsel, risk teams, or compliance reviewers.
"""


FINANCIAL_SOURCE_CAPTURE_SHAPE = """{
  "success": true,
  "capture_mode": "search_discovery",
  "sources": [
    {
      "page_title": "Apple Investor Relations",
      "final_url": "https://investor.apple.com/",
      "source_kind": "investor_relations_hub",
      "reporting_period": "",
      "captured_text": "Verbatim or near-verbatim financial source text captured from the page or document. Preserve numbers exactly as shown.",
      "linked_documents": [
        {
          "title": "Annual Report 2025",
          "url": "https://investor.apple.com/annual-report-2025.pdf",
          "document_type": "annual_report"
        }
      ],
      "notes": "Official investor relations hub with links to results, SEC filings, and annual reports."
    }
  ],
  "coverage_gaps": [
    "Latest quarterly document was not reachable from this path"
  ],
  "error": null
}"""


FINANCIAL_SOURCE_CAPTURE_ERROR_SHAPE = """{
  "success": false,
  "capture_mode": "search_discovery",
  "sources": [],
  "coverage_gaps": [
    "Search results were blocked by CAPTCHA"
  ],
  "error": {
    "type": "blocked",
    "message": "Encountered login, paywall, CAPTCHA, or anti-bot challenge"
  }
}"""


def build_financial_tinyfish_goal(
    *,
    company_name: str,
    source_label: str,
    source_type: str,
    ticker: str = "",
) -> str:
    source_focus = {
        "investor_relations_hub": (
            "Capture the investor relations hub itself, then collect raw source text and "
            "document links for the newest clearly relevant annual report, quarterly or "
            "interim results, investor presentation, and filing index."
        ),
        "investor_relations_candidate": (
            "First determine whether this is a real investor-relations page. If it is, "
            "capture raw source text plus any linked reports, results releases, or filing pages."
        ),
        "official_filing_index": (
            "Capture the filing index page and the newest clearly relevant annual and "
            "quarterly or interim filing materials you can reach from it."
        ),
        "financial_document": (
            "Capture raw text, reporting period labels, and any linked finance documents from "
            "this financial source. Do not summarize the business performance."
        ),
        "search_discovery": (
            "Starting from search results, locate official financial sources such as the "
            "company investor-relations hub, annual report, quarterly or interim results, "
            "earnings release, investor presentation, or regulator filing pages. Open the "
            "best primary-source paths and capture raw source material."
        ),
    }
    source_hint = source_focus.get(
        source_type,
        "Capture raw primary-source financial material and relevant finance document links.",
    )
    ticker_hint = (
        f" The public ticker or filing identifier is {ticker}."
        if ticker
        else ""
    )
    return (
        f"You are collecting raw financial source material for {company_name} from the "
        f"source labeled '{source_label}'.{ticker_hint}\n\n"
        "Instructions:\n"
        f"1. {source_hint}\n"
        "2. Prefer official company or regulator sources over third-party summaries.\n"
        "3. Capture source material, not your own analysis. Preserve reported figures, dates, "
        "period labels, and wording exactly when possible.\n"
        "4. For each relevant page or document you open, return the page title, final URL, "
        "source kind, reporting period if explicitly stated, captured raw text, and any "
        "linked financial documents.\n"
        "5. Do not calculate ratios, infer missing numbers, or summarize performance beyond "
        "brief source notes that identify what the page is.\n"
        "6. If a page is blocked, irrelevant, or requires login, return a structured error or "
        "coverage gap instead of guessing.\n\n"
        "Termination conditions:\n"
        "- Stop after capturing up to five clearly relevant financial sources.\n"
        "- Stop after six meaningful navigation steps if the source path is weak.\n"
        "- Stop immediately if the page shows a CAPTCHA, anti-bot wall, login gate, or "
        "access-denied message.\n\n"
        "Return JSON matching this exact shape:\n"
        f"{FINANCIAL_SOURCE_CAPTURE_SHAPE}\n\n"
        "If capture fails, return this shape instead:\n"
        f"{FINANCIAL_SOURCE_CAPTURE_ERROR_SHAPE}"
    )
