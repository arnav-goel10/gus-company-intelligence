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
"""


EXECUTIVE_AGGREGATION_SYSTEM_PROMPT = """You are the coordinating editor for a multi-agent due diligence report.

Rules:
- Use only section summaries and verified findings supplied in the prompt.
- Produce a concise executive summary for decision-makers.
- Make the methodology explicit and defensible.
- Keep key findings ranked by materiality.
- Recommendations should be actionable and tied to evidence gaps or risks.
"""


FINANCIAL_RESULT_SHAPE = """{
  "success": true,
  "source_type": "investor_relations_hub",
  "documents": [
    {
      "title": "Annual Report 2025",
      "document_type": "annual_report",
      "period": "FY2025",
      "url": "https://example.com/investors/annual-report-2025.pdf",
      "notes": "Audited consolidated financial statements"
    }
  ],
  "metrics": [
    {
      "metric": "revenue",
      "value": "1240000000",
      "unit": "USD",
      "period": "FY2025",
      "source_document": "Annual Report 2025",
      "confidence": "high"
    }
  ],
  "capital_structure": [
    {
      "metric": "cash_and_equivalents",
      "value": "280000000",
      "unit": "USD",
      "period": "FY2025"
    }
  ],
  "guidance": [
    {
      "metric": "revenue",
      "value": "1300000000 to 1350000000",
      "period": "FY2026",
      "source_document": "Q4 2025 Shareholder Letter"
    }
  ],
  "red_flags": [
    "Negative operating cash flow in FY2025"
  ],
  "coverage_gaps": [
    "Gross margin was not disclosed on the investor relations landing page"
  ],
  "error": null
}"""


FINANCIAL_ERROR_SHAPE = """{
  "success": false,
  "source_type": "investor_relations_hub",
  "documents": [],
  "metrics": [],
  "capital_structure": [],
  "guidance": [],
  "red_flags": [],
  "coverage_gaps": [
    "Page required login"
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
            "Find the latest annual report, interim or quarterly results, investor "
            "presentations, filing links, and any guidance or capital-structure "
            "disclosures."
        ),
        "investor_relations_candidate": (
            "First determine whether this is a real investor-relations page. If it is, "
            "collect the latest filings, reports, and quantified financial disclosures."
        ),
        "official_filing_index": (
            "Identify the latest annual filing, latest interim or quarterly filing, "
            "material 8-K or 6-K updates, and any clearly stated revenue, profit, cash, "
            "debt, and guidance metrics."
        ),
        "financial_document": (
            "Extract the company's most material financial metrics, balance-sheet facts, "
            "guidance, and any explicit red flags from this source."
        ),
    }
    source_hint = source_focus.get(
        source_type,
        "Collect primary-source financial metrics, document links, and disclosure gaps.",
    )
    ticker_hint = (
        f" The public ticker or filing identifier is {ticker}."
        if ticker
        else ""
    )
    return (
        f"You are collecting primary-source financial evidence for {company_name} from the "
        f"source labeled '{source_label}'.{ticker_hint}\n\n"
        "Instructions:\n"
        f"1. {source_hint}\n"
        "2. Prefer official company or regulator documents over summaries.\n"
        "3. Navigate through links when needed, but stop once the newest clearly relevant "
        "financial disclosures have been captured.\n"
        "4. Capture explicit values only when the source states them. Do not infer missing "
        "numbers or calculate your own ratios.\n"
        "5. If a page is blocked, irrelevant, or requires login, return a structured error.\n\n"
        "Termination conditions:\n"
        "- Stop once you have identified the latest annual disclosure and the latest interim "
        "or quarterly disclosure, or after collecting five relevant finance documents.\n"
        "- Stop immediately if the page shows a CAPTCHA, anti-bot wall, login gate, or "
        "access-denied message.\n\n"
        "Return JSON matching this exact shape:\n"
        f"{FINANCIAL_RESULT_SHAPE}\n\n"
        "If extraction fails or the source is not a real financial source, return this "
        "shape instead:\n"
        f"{FINANCIAL_ERROR_SHAPE}"
    )
