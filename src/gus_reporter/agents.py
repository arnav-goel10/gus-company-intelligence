from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainAgent:
    name: str
    section: str
    preferred_categories: tuple[str, ...]
    extraction_instruction: str
    synthesis_instruction: str
    use_all_evidence: bool = False


@dataclass(frozen=True)
class ExtractionAgent:
    name: str
    section: str
    preferred_categories: tuple[str, ...]
    extraction_instruction: str
    preferred_source_types: tuple[str, ...] = ()
    use_all_evidence: bool = False


DOMAIN_AGENTS: dict[str, DomainAgent] = {
    "company_overview": DomainAgent(
        name="overview_agent",
        section="company_overview",
        preferred_categories=("company_overview", "industry"),
        extraction_instruction=(
            "Focus on official company profile facts, products, markets, scale signals, "
            "and core business model details."
        ),
        synthesis_instruction=(
            "Write a concise entity snapshot for decision-makers. Keep it factual and "
            "avoid marketing tone."
        ),
    ),
    "financials": DomainAgent(
        name="financial_agent",
        section="financials",
        preferred_categories=("financials", "company_overview", "news"),
        extraction_instruction=(
            "Focus on revenue, gross margin, EBITDA, net income, free cash flow, cash, debt, "
            "market value, funding, runway, guidance, and quantified performance indicators. "
            "Prefer primary sources such as investor relations pages, filings, audited reports, "
            "and official funding announcements."
        ),
        synthesis_instruction=(
            "Write a finance-focused summary highlighting verified metrics, data quality, "
            "red flags, and any missing disclosure."
        ),
    ),
    "legal": DomainAgent(
        name="legal_agent",
        section="legal",
        preferred_categories=("legal", "risk_signals", "news"),
        extraction_instruction=(
            "Focus on litigation, disputes, enforcement, sanctions, investigations, "
            "regulatory actions, and legally material exposure."
        ),
        synthesis_instruction=(
            "Write a legal and compliance summary with emphasis on what is active, "
            "material, and verified."
        ),
    ),
    "industry": DomainAgent(
        name="industry_agent",
        section="industry",
        preferred_categories=("industry", "company_overview", "news"),
        extraction_instruction=(
            "Focus on market positioning, sector dynamics, competitive references, "
            "customer concentration, and strategic narrative grounded in evidence."
        ),
        synthesis_instruction=(
            "Write a market and industry summary that is useful for diligence, not "
            "for marketing."
        ),
    ),
    "leadership": DomainAgent(
        name="leadership_agent",
        section="leadership",
        preferred_categories=("leadership", "company_overview", "news"),
        extraction_instruction=(
            "Focus on founders, executives, board members, appointments, departures, "
            "and governance-relevant leadership signals."
        ),
        synthesis_instruction=(
            "Write a leadership and governance summary centered on named people and "
            "structural control signals."
        ),
    ),
    "employees": DomainAgent(
        name="employees_agent",
        section="employees",
        preferred_categories=("employees", "leadership", "news"),
        extraction_instruction=(
            "Focus on hiring signals, layoffs, org scale, geographic team footprint, "
            "and workforce-related public signals."
        ),
        synthesis_instruction=(
            "Write a workforce summary based on verified public indicators and flag "
            "coverage gaps clearly."
        ),
    ),
    "news": DomainAgent(
        name="news_agent",
        section="news",
        preferred_categories=("news", "company_overview", "industry"),
        extraction_instruction=(
            "Focus on recent material events such as funding, acquisitions, product "
            "launches, leadership changes, lawsuits, investigations, recalls, or layoffs."
        ),
        synthesis_instruction=(
            "Write a recent-events summary with emphasis on what changed and why it matters."
        ),
    ),
    "risk_signals": DomainAgent(
        name="risk_agent",
        section="risk_signals",
        preferred_categories=("risk_signals", "legal", "financials", "news"),
        extraction_instruction=(
            "Focus only on red flags, contradictions, unresolved issues, concentration "
            "risk, governance gaps, financial strain, or reputational warnings."
        ),
        synthesis_instruction=(
            "Write a risk-focused summary that reads like a risk memo. Call out only "
            "verified issues."
        ),
        use_all_evidence=True,
    ),
}


FINANCIAL_EXTRACTION_AGENTS: tuple[ExtractionAgent, ...] = (
    ExtractionAgent(
        name="financial_document_locator",
        section="financials",
        preferred_categories=("financials",),
        preferred_source_types=(
            "financial_investor_relations",
            "financial_investor_candidate",
            "financial_document",
            "financial_sec_filings",
        ),
        extraction_instruction=(
            "Focus on locating the newest financially material primary sources, including "
            "annual reports, quarterly or interim results, filing indexes, investor "
            "presentations, and official guidance documents. Extract claims only about "
            "document recency, reporting periods, and what was formally disclosed."
        ),
    ),
    ExtractionAgent(
        name="financial_statement_agent",
        section="financials",
        preferred_categories=("financials",),
        preferred_source_types=(
            "financial_investor_relations",
            "financial_document",
            "financial_sec_filings",
        ),
        extraction_instruction=(
            "Focus on income statement and cash flow facts: revenue, gross profit or gross "
            "margin, EBITDA or operating income, net income, free cash flow, and reported "
            "growth or decline across named periods. Prefer explicit numeric statements."
        ),
    ),
    ExtractionAgent(
        name="capital_structure_agent",
        section="financials",
        preferred_categories=("financials",),
        preferred_source_types=(
            "financial_investor_relations",
            "financial_document",
            "financial_sec_filings",
        ),
        extraction_instruction=(
            "Focus on balance sheet and financing facts: cash, debt, liquidity, leverage, "
            "credit facilities, buybacks, dividends, dilution, and other capital structure "
            "signals stated in primary sources."
        ),
    ),
    ExtractionAgent(
        name="funding_guidance_agent",
        section="financials",
        preferred_categories=("financials", "news"),
        preferred_source_types=(
            "financial_investor_relations",
            "financial_document",
            "financial_sec_filings",
        ),
        extraction_instruction=(
            "Focus on management guidance, fundraising, capital raises, debt issuance, "
            "refinancing, and officially announced financially material events. Use news "
            "only when it reflects an official company announcement."
        ),
    ),
    ExtractionAgent(
        name="financial_risk_agent",
        section="financials",
        preferred_categories=("financials", "news", "risk_signals"),
        use_all_evidence=True,
        extraction_instruction=(
            "Focus only on finance-related red flags: missing disclosure, weak liquidity, "
            "shrinking margins, losses, negative cash flow, refinancing pressure, "
            "restatements, or contradictions between sources."
        ),
    ),
)


LEGAL_EXTRACTION_AGENTS: tuple[ExtractionAgent, ...] = (
    ExtractionAgent(
        name="legal_case_locator",
        section="legal",
        preferred_categories=("legal",),
        preferred_source_types=(
            "legal_court_records",
            "legal_regulatory_filings",
            "legal_search_discovery",
        ),
        extraction_instruction=(
            "Focus on locating active and recent litigation, docket entries, case numbers, "
            "named parties, courts, filing dates, and case status. Extract claims only about "
            "what cases exist, their jurisdiction, and current procedural status."
        ),
    ),
    ExtractionAgent(
        name="legal_regulatory_agent",
        section="legal",
        preferred_categories=("legal",),
        preferred_source_types=(
            "legal_regulatory_filings",
            "legal_enforcement",
        ),
        extraction_instruction=(
            "Focus on enforcement actions, consent decrees, cease-and-desist orders, "
            "regulatory settlements, compliance orders, and fines from named agencies "
            "such as SEC, FTC, DOJ, EPA, CFPB, OSHA, or equivalent non-US regulators. "
            "Prefer explicit agency names, action dates, and monetary amounts."
        ),
    ),
    ExtractionAgent(
        name="legal_sanctions_agent",
        section="legal",
        preferred_categories=("legal",),
        preferred_source_types=(
            "legal_sanctions",
            "legal_regulatory_filings",
        ),
        extraction_instruction=(
            "Focus on sanctions list appearances including OFAC SDN, EU consolidated "
            "sanctions, UN sanctions, debarment lists, export control violations, and "
            "AML enforcement actions. Extract the list name, date of designation, and "
            "current status if available."
        ),
    ),
    ExtractionAgent(
        name="legal_corporate_filings_agent",
        section="legal",
        preferred_categories=("legal", "company_overview"),
        preferred_source_types=(
            "legal_corporate_registry",
            "legal_regulatory_filings",
        ),
        extraction_instruction=(
            "Focus on corporate registry facts: incorporation details, registered agents, "
            "UCC filings, liens, judgments, mergers, dissolutions, name changes, and "
            "beneficial ownership disclosures. Prefer official registry sources."
        ),
    ),
    ExtractionAgent(
        name="legal_risk_agent",
        section="legal",
        preferred_categories=("legal", "news", "risk_signals"),
        use_all_evidence=True,
        extraction_instruction=(
            "Focus only on legal red flags: unresolved litigation exposure, repeated "
            "regulatory action, undisclosed disputes, pattern of enforcement, whistleblower "
            "signals, or material legal risks that are not yet captured by other legal "
            "specialists. Do not duplicate claims already covered by case or regulatory agents."
        ),
    ),
)


NEWS_EXTRACTION_AGENTS: tuple[ExtractionAgent, ...] = (
    ExtractionAgent(
        name="news_event_locator",
        section="news",
        preferred_categories=("news",),
        preferred_source_types=(
            "news_aggregator",
            "news_press_release",
            "news_search_discovery",
        ),
        extraction_instruction=(
            "Focus on date-stamped material events: funding rounds, acquisitions, "
            "product launches, executive changes, restructurings, IPO signals, and "
            "major partnerships. Each claim must include the approximate event date "
            "and the publishing source."
        ),
    ),
    ExtractionAgent(
        name="news_negative_signal_agent",
        section="news",
        preferred_categories=("news", "risk_signals"),
        preferred_source_types=(
            "news_aggregator",
            "news_industry_publication",
            "news_search_discovery",
        ),
        extraction_instruction=(
            "Focus on negative press coverage: lawsuits mentioned in the press, layoffs, "
            "product recalls, data breaches, investigation reports, short-seller reports, "
            "analyst downgrades, and controversy. Prefer claims with named sources and dates."
        ),
    ),
    ExtractionAgent(
        name="news_sentiment_agent",
        section="news",
        preferred_categories=("news", "industry"),
        preferred_source_types=(
            "news_aggregator",
            "news_industry_publication",
        ),
        extraction_instruction=(
            "Focus on the overall narrative trend: is press coverage net positive, negative, "
            "or mixed? How frequent is coverage? What is the tone of analyst or industry "
            "commentary? Extract claims about sentiment direction and coverage volume, not "
            "individual events."
        ),
    ),
    ExtractionAgent(
        name="news_coverage_gap_agent",
        section="news",
        preferred_categories=("news",),
        use_all_evidence=True,
        extraction_instruction=(
            "Focus on identifying what is NOT being covered: missing press around expected "
            "events such as anticipated earnings with no coverage, a stale or abandoned "
            "newsroom, dark period signals, or conspicuous absence of coverage for a company "
            "of its scale. Extract claims about coverage gaps, not about events themselves."
        ),
    ),
)


def get_active_agents(focus_areas: list[str]) -> list[DomainAgent]:
    return [DOMAIN_AGENTS[area] for area in focus_areas if area in DOMAIN_AGENTS]


def get_extraction_agents(agent: DomainAgent) -> list[ExtractionAgent]:
    if agent.section == "financials":
        return list(FINANCIAL_EXTRACTION_AGENTS)
    if agent.section == "legal":
        return list(LEGAL_EXTRACTION_AGENTS)
    if agent.section == "news":
        return list(NEWS_EXTRACTION_AGENTS)
    return [
        ExtractionAgent(
            name=agent.name,
            section=agent.section,
            preferred_categories=agent.preferred_categories,
            extraction_instruction=agent.extraction_instruction,
            use_all_evidence=agent.use_all_evidence,
        )
    ]
