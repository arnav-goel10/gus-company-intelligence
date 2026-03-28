from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


DEFAULT_FOCUS_AREAS = [
    "company_overview",
    "financials",
    "legal",
    "industry",
    "leadership",
    "employees",
    "news",
    "risk_signals",
]


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class SourceJob:
    label: str
    category: str
    url: str
    goal: str
    source_type: str = ""
    browser_profile: str = "lite"
    proxy_country_code: str = ""
    preferred_run_mode: str = "async"
    use_vault: bool = False
    credential_item_ids: list[str] = field(default_factory=list)
    enable_agent_memory: bool = False
    allow_stealth_fallback: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_browser_profile: str) -> "SourceJob":
        return cls(
            label=str(data["label"]).strip(),
            category=str(data["category"]).strip(),
            url=str(data["url"]).strip(),
            goal=str(data["goal"]).strip(),
            source_type=str(data.get("source_type", "")).strip(),
            browser_profile=str(
                data.get("browser_profile", default_browser_profile)
            ).strip()
            or default_browser_profile,
            proxy_country_code=str(data.get("proxy_country_code", "")).strip(),
            preferred_run_mode=str(data.get("preferred_run_mode", "async")).strip()
            or "async",
            use_vault=_coerce_bool(data.get("use_vault", False)),
            credential_item_ids=[
                str(item).strip()
                for item in data.get("credential_item_ids", [])
                if str(item).strip()
            ],
            enable_agent_memory=_coerce_bool(data.get("enable_agent_memory", False)),
            allow_stealth_fallback=_coerce_bool(
                data.get("allow_stealth_fallback", True)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReportRequest:
    company_name: str
    company_website: str = ""
    primary_jurisdiction: str = ""
    ticker: str = ""
    investor_relations_url: str = ""
    financial_source_urls: list[str] = field(default_factory=list)
    tinyfish_use_vault: bool = False
    tinyfish_credential_item_ids: list[str] = field(default_factory=list)
    tinyfish_enable_agent_memory: bool = False
    report_goal: str = ""
    focus_areas: list[str] = field(default_factory=lambda: list(DEFAULT_FOCUS_AREAS))
    source_jobs: list[SourceJob] = field(default_factory=list)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], default_browser_profile: str
    ) -> "ReportRequest":
        source_jobs = [
            SourceJob.from_dict(item, default_browser_profile)
            for item in data.get("source_jobs", [])
        ]
        return cls(
            company_name=str(data["company_name"]).strip(),
            company_website=str(data.get("company_website", "")).strip(),
            primary_jurisdiction=str(data.get("primary_jurisdiction", "")).strip(),
            ticker=str(data.get("ticker", "")).strip(),
            investor_relations_url=str(data.get("investor_relations_url", "")).strip(),
            financial_source_urls=[
                str(item).strip() for item in data.get("financial_source_urls", [])
            ],
            tinyfish_use_vault=_coerce_bool(data.get("tinyfish_use_vault", False)),
            tinyfish_credential_item_ids=[
                str(item).strip()
                for item in data.get("tinyfish_credential_item_ids", [])
                if str(item).strip()
            ],
            tinyfish_enable_agent_memory=_coerce_bool(
                data.get("tinyfish_enable_agent_memory", False)
            ),
            report_goal=str(data.get("report_goal", "")).strip(),
            focus_areas=[
                str(item).strip() for item in data.get("focus_areas", DEFAULT_FOCUS_AREAS)
            ]
            or list(DEFAULT_FOCUS_AREAS),
            source_jobs=source_jobs,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_jobs"] = [job.to_dict() for job in self.source_jobs]
        return payload


@dataclass
class TinyfishRunRecord:
    run_id: str
    status: str
    goal: str
    label: str
    category: str
    url: str
    source_type: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    num_of_steps: int | None = None
    streaming_url: str = ""
    browser_config: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    result: Any = None
    error: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceItem:
    evidence_id: str
    label: str
    category: str
    url: str
    retrieved_at: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractedClaim:
    claim_id: str
    section: str
    statement: str
    importance: str
    confidence: float
    as_of: str
    source_quote: str
    evidence_ids: list[str]
    raw_source_label: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerifiedClaim:
    claim_id: str
    section: str
    statement: str
    status: str
    confidence: float
    rationale: str
    evidence_ids: list[str]
    blocking_issues: list[str]
    source_quote: str
    importance: str
    as_of: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReportSection:
    name: str
    status: str
    summary: str
    highlights: list[str]
    evidence_ids: list[str]
    recommendations: list[str]
    structured_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RiskMatrixEntry:
    section: str
    severity: str
    issue: str
    evidence_ids: list[str]
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FinalReport:
    company_name: str
    generated_at: str
    publish_decision: str
    publish_reason: str
    executive_summary: str
    scope_and_limitations: str
    methodology: str
    entity_profile: str
    diligence_conclusion: str
    key_findings: list[str]
    recommendations: list[str]
    pdf_section_order: list[str]
    risk_matrix: list[RiskMatrixEntry]
    sections: list[ReportSection]
    source_log: list[dict[str, Any]]
    unsupported_claims: int
    total_claims: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["risk_matrix"] = [item.to_dict() for item in self.risk_matrix]
        payload["sections"] = [section.to_dict() for section in self.sections]
        return payload
