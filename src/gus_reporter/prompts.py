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
      "linked_documents": [
        {
          "title": "Annual Report 2025",
          "url": "https://investor.apple.com/annual-report-2025.pdf",
          "document_type": "annual_report"
        }
      ],
      "notes": "Official investor relations hub with links to results, SEC filings, and annual reports. Keep notes short and source-oriented."
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


LEGAL_SOURCE_CAPTURE_SHAPE = """{
  "success": true,
  "capture_mode": "court_docket_search",
  "sources": [
    {
      "page_title": "Apple Inc. v. Samsung Electronics - Docket",
      "final_url": "https://www.courtlistener.com/docket/123456/apple-inc-v-samsung/",
      "source_kind": "court_docket",
      "case_identifier": "5:11-cv-01846",
      "jurisdiction": "N.D. Cal.",
      "linked_documents": [
        {
          "title": "Complaint",
          "url": "https://storage.courtlistener.com/recap/...",
          "document_type": "complaint"
        }
      ],
      "notes": "Patent infringement case. Last activity 2025-09-15."
    }
  ],
  "coverage_gaps": [
    "State court records were not accessible from this path"
  ],
  "error": null
}"""


LEGAL_SOURCE_CAPTURE_ERROR_SHAPE = """{
  "success": false,
  "capture_mode": "court_docket_search",
  "sources": [],
  "coverage_gaps": [
    "Court records database required login or subscription"
  ],
  "error": {
    "type": "blocked",
    "message": "Encountered login, paywall, CAPTCHA, or anti-bot challenge"
  }
}"""


NEWS_SOURCE_CAPTURE_SHAPE = """{
  "success": true,
  "capture_mode": "news_aggregator_search",
  "sources": [
    {
      "page_title": "Apple announces major restructuring of services division",
      "final_url": "https://reuters.com/technology/apple-restructuring-2026-03/",
      "source_kind": "news_article",
      "publication_date": "2026-03-15",
      "publisher": "Reuters",
      "linked_documents": [],
      "notes": "Reports 200 job cuts in services division effective Q2 2026."
    }
  ],
  "coverage_gaps": [
    "Industry trade press results were paywalled"
  ],
  "error": null
}"""


NEWS_SOURCE_CAPTURE_ERROR_SHAPE = """{
  "success": false,
  "capture_mode": "news_aggregator_search",
  "sources": [],
  "coverage_gaps": [
    "News aggregator blocked automated access"
  ],
  "error": {
    "type": "blocked",
    "message": "Encountered login, paywall, CAPTCHA, or anti-bot challenge"
  }
}"""


def build_legal_tinyfish_goal(
    *,
    company_name: str,
    source_label: str,
    source_type: str,
    ticker: str = "",
    jurisdiction: str = "",
) -> str:
    source_focus = {
        "court_docket_search": (
            "Search for active and recent litigation involving the company. Collect "
            "case numbers, court names, filing dates, named parties, and links to "
            "docket entries or case documents."
        ),
        "regulatory_enforcement_search": (
            "Search for regulatory enforcement actions, consent decrees, fines, penalties, "
            "and settlements involving the company from agencies such as SEC, FTC, DOJ, EPA, "
            "CFPB, or equivalent regulators. Collect action dates, agency names, and amounts."
        ),
        "sanctions_screening": (
            "Search for the company name on sanctions and debarment databases including "
            "OFAC SDN list, EU consolidated sanctions, and government debarment lists. "
            "Report any matches with designation dates and list names."
        ),
        "corporate_registry_lookup": (
            "Search corporate registry records for incorporation details, registered agents, "
            "UCC filings, liens, judgments, and beneficial ownership disclosures. Collect "
            "registry names, filing dates, and document links."
        ),
        "legal_search_discovery": (
            "Starting from search results, locate official legal sources such as court "
            "dockets, regulatory enforcement pages, sanctions databases, or corporate "
            "registry filings involving the company. Open the best primary-source paths "
            "and return the direct source URLs and any linked legal documents."
        ),
    }
    source_hint = source_focus.get(
        source_type,
        "Capture raw legal source material and any linked legal documents.",
    )
    ticker_hint = (
        f" The public ticker or filing identifier is {ticker}."
        if ticker
        else ""
    )
    jurisdiction_hint = (
        f" The primary jurisdiction is {jurisdiction}."
        if jurisdiction
        else ""
    )
    return (
        f"You are collecting raw legal source material for {company_name} from the "
        f"source labeled '{source_label}'.{ticker_hint}{jurisdiction_hint}\n\n"
        "Instructions:\n"
        f"1. {source_hint}\n"
        "2. Prefer official court, regulatory, and government sources over news summaries.\n"
        "3. Your job is source discovery and direct-link collection, not legal analysis.\n"
        "4. For each relevant page or document you find, return the page title, final URL, "
        "source kind, case identifier or action reference if stated, jurisdiction, and any "
        "linked legal documents.\n"
        "5. Do not interpret legal outcomes, assess liability, or summarize legal arguments. "
        "Keep notes short and focused on what the source is.\n"
        "6. If a page is blocked, irrelevant, or requires login, return a structured error or "
        "coverage gap instead of guessing.\n\n"
        "Termination conditions:\n"
        "- Stop after capturing up to eight clearly relevant legal sources.\n"
        "- Stop after six meaningful navigation steps if the source path is weak.\n"
        "- Stop immediately if the page shows a CAPTCHA, anti-bot wall, login gate, or "
        "access-denied message.\n\n"
        "Return JSON matching this exact shape:\n"
        f"{LEGAL_SOURCE_CAPTURE_SHAPE}\n\n"
        "If capture fails, return this shape instead:\n"
        f"{LEGAL_SOURCE_CAPTURE_ERROR_SHAPE}"
    )


def build_news_tinyfish_goal(
    *,
    company_name: str,
    source_label: str,
    source_type: str,
) -> str:
    source_focus = {
        "news_aggregator_search": (
            "Search for recent news articles about the company from major outlets. "
            "Collect article titles, publication dates, publisher names, and direct "
            "article URLs. Prioritize the most recent and most material coverage."
        ),
        "press_release_search": (
            "Search for official press releases from the company on wire services such "
            "as PR Newswire, Business Wire, or GlobeNewsWire. Collect release titles, "
            "dates, and direct URLs."
        ),
        "industry_publication_search": (
            "Search for industry-specific coverage and analyst commentary about the "
            "company in trade publications and industry news sites. Collect article "
            "titles, publication dates, publisher names, and direct URLs."
        ),
        "news_search_discovery": (
            "Starting from search results, locate recent news articles, press releases, "
            "and industry coverage about the company. Open the best paths and return "
            "the direct article URLs, publication dates, and publisher names."
        ),
    }
    source_hint = source_focus.get(
        source_type,
        "Capture raw news source material and any linked articles or press releases.",
    )
    return (
        f"You are collecting raw news source material for {company_name} from the "
        f"source labeled '{source_label}'.\n\n"
        "Instructions:\n"
        f"1. {source_hint}\n"
        "2. Prefer named, reputable publishers over aggregator summaries or social media.\n"
        "3. Your job is source discovery and direct-link collection, not news analysis.\n"
        "4. For each relevant article or release you find, return the page title, final URL, "
        "source kind, publication date if stated, publisher name, and any linked documents.\n"
        "5. Do not summarize article content or assess sentiment. Keep notes short and "
        "focused on what the source is.\n"
        "6. If a page is blocked, irrelevant, or requires login, return a structured error or "
        "coverage gap instead of guessing.\n\n"
        "Termination conditions:\n"
        "- Stop after capturing up to ten clearly relevant news sources.\n"
        "- Stop after six meaningful navigation steps if the source path is weak.\n"
        "- Stop immediately if the page shows a CAPTCHA, anti-bot wall, login gate, or "
        "access-denied message.\n\n"
        "Return JSON matching this exact shape:\n"
        f"{NEWS_SOURCE_CAPTURE_SHAPE}\n\n"
        "If capture fails, return this shape instead:\n"
        f"{NEWS_SOURCE_CAPTURE_ERROR_SHAPE}"
    )


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
            "Confirm the direct document URL, reporting period labels if explicit, and any "
            "related linked source material. Do not extract financial figures or summarize performance."
        ),
        "search_discovery": (
            "Starting from search results, locate official financial sources such as the "
            "company investor-relations hub, annual report, quarterly or interim results, "
            "earnings release, investor presentation, or regulator filing pages. Open the "
            "best primary-source paths and return the direct source URLs and linked documents."
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
        "3. Your job is source discovery and direct-link collection, not financial analysis.\n"
        "4. For each relevant page or document you open, return the page title, final URL, "
        "source kind, reporting period if explicitly stated, and any linked financial documents.\n"
        "5. Do not calculate ratios, infer missing numbers, or extract large blocks of financial text. "
        "Keep notes short and focused on what the source is.\n"
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
