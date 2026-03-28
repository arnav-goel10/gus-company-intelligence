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


def get_active_agents(focus_areas: list[str]) -> list[DomainAgent]:
    return [DOMAIN_AGENTS[area] for area in focus_areas if area in DOMAIN_AGENTS]
