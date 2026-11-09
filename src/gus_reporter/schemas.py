from __future__ import annotations


def extraction_schema(focus_areas: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document_summary": {"type": "string"},
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "section": {"type": "string", "enum": focus_areas},
                        "statement": {"type": "string"},
                        "importance": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "confidence": {"type": "number"},
                        "as_of": {"type": "string"},
                        "source_quote": {"type": "string"},
                    },
                    "required": [
                        "section",
                        "statement",
                        "importance",
                        "confidence",
                        "as_of",
                        "source_quote",
                    ],
                },
            },
        },
        "required": ["document_summary", "claims"],
    }


def verification_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {
                "type": "string",
                "enum": [
                    "supported",
                    "unsupported",
                    "contradicted",
                    "insufficient_evidence",
                ],
            },
            "confidence": {"type": "number"},
            "rationale": {"type": "string"},
            "supporting_evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "blocking_issues": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "status",
            "confidence",
            "rationale",
            "supporting_evidence_ids",
            "blocking_issues",
        ],
    }


def verification_batch_schema(claim_ids: list[str]) -> dict:
    single = verification_schema()
    verdict = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"claim_id": {"type": "string", "enum": list(claim_ids)}, **single["properties"]},
        "required": ["claim_id", *single["required"]],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"verdicts": {"type": "array", "items": verdict}},
        "required": ["verdicts"],
    }


def report_schema(focus_areas: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "executive_summary": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string", "enum": focus_areas},
                        "summary": {"type": "string"},
                        "highlights": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["name", "summary", "highlights"],
                },
            },
        },
        "required": ["executive_summary", "sections"],
    }


def section_summary_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "highlights": {
                "type": "array",
                "items": {"type": "string"},
            },
            "recommendations": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["summary", "highlights", "recommendations"],
    }


def financial_section_summary_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "highlights": {
                "type": "array",
                "items": {"type": "string"},
            },
            "recommendations": {
                "type": "array",
                "items": {"type": "string"},
            },
            "key_metrics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "metric": {"type": "string"},
                        "value": {"type": "string"},
                        "period": {"type": "string"},
                        "confidence": {"type": "number"},
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "metric",
                        "value",
                        "period",
                        "confidence",
                        "evidence_ids",
                    ],
                },
            },
            "red_flags": {
                "type": "array",
                "items": {"type": "string"},
            },
            "data_gaps": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "summary",
            "highlights",
            "recommendations",
            "key_metrics",
            "red_flags",
            "data_gaps",
        ],
    }


def legal_section_summary_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "highlights": {
                "type": "array",
                "items": {"type": "string"},
            },
            "recommendations": {
                "type": "array",
                "items": {"type": "string"},
            },
            "active_matters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "matter_type": {"type": "string"},
                        "description": {"type": "string"},
                        "jurisdiction": {"type": "string"},
                        "status": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "date_filed_or_announced": {"type": "string"},
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "matter_type",
                        "description",
                        "jurisdiction",
                        "status",
                        "severity",
                        "date_filed_or_announced",
                        "evidence_ids",
                    ],
                },
            },
            "sanctions_flags": {
                "type": "array",
                "items": {"type": "string"},
            },
            "regulatory_exposure": {
                "type": "array",
                "items": {"type": "string"},
            },
            "red_flags": {
                "type": "array",
                "items": {"type": "string"},
            },
            "data_gaps": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "summary",
            "highlights",
            "recommendations",
            "active_matters",
            "sanctions_flags",
            "regulatory_exposure",
            "red_flags",
            "data_gaps",
        ],
    }


def news_section_summary_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "highlights": {
                "type": "array",
                "items": {"type": "string"},
            },
            "recommendations": {
                "type": "array",
                "items": {"type": "string"},
            },
            "material_events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "event_type": {"type": "string"},
                        "headline": {"type": "string"},
                        "date": {"type": "string"},
                        "source_publisher": {"type": "string"},
                        "sentiment": {
                            "type": "string",
                            "enum": ["positive", "negative", "neutral"],
                        },
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "event_type",
                        "headline",
                        "date",
                        "source_publisher",
                        "sentiment",
                        "evidence_ids",
                    ],
                },
            },
            "coverage_sentiment": {
                "type": "string",
                "enum": ["net_positive", "net_negative", "mixed", "insufficient"],
            },
            "coverage_density": {
                "type": "string",
                "enum": ["heavy", "moderate", "light", "dark"],
            },
            "negative_signals": {
                "type": "array",
                "items": {"type": "string"},
            },
            "data_gaps": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "summary",
            "highlights",
            "recommendations",
            "material_events",
            "coverage_sentiment",
            "coverage_density",
            "negative_signals",
            "data_gaps",
        ],
    }


def executive_report_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "executive_summary": {"type": "string"},
            "scope_and_limitations": {"type": "string"},
            "methodology": {"type": "string"},
            "entity_profile": {"type": "string"},
            "diligence_conclusion": {"type": "string"},
            "key_findings": {
                "type": "array",
                "items": {"type": "string"},
            },
            "recommendations": {
                "type": "array",
                "items": {"type": "string"},
            },
            "pdf_section_order": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "executive_summary",
            "scope_and_limitations",
            "methodology",
            "entity_profile",
            "diligence_conclusion",
            "key_findings",
            "recommendations",
            "pdf_section_order",
        ],
    }
