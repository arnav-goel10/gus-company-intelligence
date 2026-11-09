"""What a run costs: an estimate before spending, and the exact bill after.

Prices are list prices read from the providers' own pages on 9 Nov 2026:
  OpenAI   https://developers.openai.com/api/docs/pricing (standard tier)
  Tinyfish https://www.tinyfish.ai/pricing (TinyAgent, per agent step)

Before a run, the number of browse jobs and model calls that depend only on
the request is computed exactly (seed jobs, extraction specialists, report
sections). What depends on the web (how many child jobs discovery spawns, how
long the pages are, how many claims the agents find) is a stated assumption
with a low, typical and high value. After a run, run_cost() reads usage.json
and tinyfish_runs.json and gives the billed figure, no assumptions.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from gus_reporter.agents import get_active_agents, get_extraction_agents
from gus_reporter.models import ReportRequest
from gus_reporter.planning import seed_source_jobs

PRICES_READ_ON = "2026-11-09"

# USD per 1M tokens: input, cached input, output.
OPENAI_PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
}
TINYFISH_USD_PER_STEP = 0.016


def model_price(model: str) -> tuple[float, float, float]:
    for name in sorted(OPENAI_PRICES, key=len, reverse=True):
        if model == name or model.startswith(f"{name}-20"):
            return OPENAI_PRICES[name]
    raise KeyError(f"No price on file for model {model!r}; add it to OPENAI_PRICES.")


def token_cost(model: str, *, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float:
    price_in, price_cached, price_out = model_price(model)
    uncached = max(0, input_tokens - cached_input_tokens)
    return (
        uncached * price_in + cached_input_tokens * price_cached + output_tokens * price_out
    ) / 1_000_000


# --------------------------------------------------------------------------
# Exact cost of a finished run
# --------------------------------------------------------------------------


def run_cost(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    usage_path = run_dir / "usage.json"
    calls = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.exists() else []
    model_usd = 0.0
    tokens = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    for call in calls:
        if call.get("failed") or not call.get("model"):
            continue
        for key in tokens:
            tokens[key] += int(call.get(key, 0))
        model_usd += token_cost(
            call["model"],
            input_tokens=int(call.get("input_tokens", 0)),
            cached_input_tokens=int(call.get("cached_input_tokens", 0)),
            output_tokens=int(call.get("output_tokens", 0)),
        )
    runs = json.loads((run_dir / "tinyfish_runs.json").read_text(encoding="utf-8"))
    steps = sum(int(run.get("num_of_steps") or 0) for run in runs)
    return {
        "model_calls": len(calls),
        **tokens,
        "model_usd": round(model_usd, 4),
        "browse_jobs": len(runs),
        "browse_steps": steps,
        "browse_usd": round(steps * TINYFISH_USD_PER_STEP, 4),
        "total_usd": round(model_usd + steps * TINYFISH_USD_PER_STEP, 4),
    }


# --------------------------------------------------------------------------
# Estimate before a run
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    name: str
    steps_per_browse_job: int
    spawned_jobs_fraction: float  # share of the remaining job budget discovery fills
    evidence_items_per_bundle: int
    text_tokens_per_item: int  # the bundle caps each item at 12,000 chars (about 3,000 tokens)
    pdfs_per_bundle: int  # the bundle attaches at most 4
    tokens_per_pdf: int  # extracted text plus one low-detail image per page
    claims_per_specialist: int
    extraction_output_tokens: int
    verification_output_tokens: int
    judge_cited_items: int


SCENARIOS = (
    Scenario("low", 8, 0.0, 6, 800, 0, 0, 2, 400, 150, 1),
    Scenario("typical", 20, 0.5, 15, 2000, 1, 40_000, 6, 1200, 250, 2),
    Scenario("high", 50, 1.0, 30, 3000, 4, 100_000, 12, 4000, 600, 3),
)

_PROMPT_OVERHEAD_TOKENS = 600  # system prompt, preamble, schema
_SECTION_INPUT_TOKENS = 2500
_SECTION_OUTPUT_TOKENS = 700
_EXECUTIVE_INPUT_TOKENS = 5000
_EXECUTIVE_OUTPUT_TOKENS = 1800
_JUDGE_OUTPUT_TOKENS = 250


def request_shape(request: ReportRequest, max_total_jobs: int) -> dict[str, int]:
    """The parts of a run's size that follow from the request alone."""
    agents = get_active_agents(request.focus_areas)
    return {
        "seed_jobs": len(seed_source_jobs(request, "lite")),
        "max_total_jobs": max_total_jobs,
        "extraction_calls": sum(len(get_extraction_agents(agent)) for agent in agents),
        "sections": len(agents),
    }


def estimate_run(
    request: ReportRequest,
    *,
    model: str,
    max_total_jobs: int = 50,
    judge_model: str | None = None,
    kept_fraction: float = 0.6,
    extra_judged: int = 0,
    judge_sample: int = 0,
) -> dict[str, Any]:
    """extra_judged: suppressed claims the eval also judges, per run."""
    shape = request_shape(request, max_total_jobs)
    estimates = {}
    for scenario in SCENARIOS:
        spare = max(0, max_total_jobs - shape["seed_jobs"])
        jobs = shape["seed_jobs"] + round(spare * scenario.spawned_jobs_fraction)
        browse_usd = jobs * scenario.steps_per_browse_job * TINYFISH_USD_PER_STEP

        bundle_tokens = (
            scenario.evidence_items_per_bundle * scenario.text_tokens_per_item
            + scenario.pdfs_per_bundle * scenario.tokens_per_pdf
        )
        claims = shape["extraction_calls"] * scenario.claims_per_specialist
        # Verification re-sends the specialist's whole evidence bundle with every
        # claim, because an extracted claim cites every item it was shown.
        model_usd = token_cost(
            model,
            input_tokens=shape["extraction_calls"] * (bundle_tokens + _PROMPT_OVERHEAD_TOKENS)
            + claims * (bundle_tokens + _PROMPT_OVERHEAD_TOKENS)
            + shape["sections"] * _SECTION_INPUT_TOKENS
            + _EXECUTIVE_INPUT_TOKENS,
            cached_input_tokens=0,
            output_tokens=shape["extraction_calls"] * scenario.extraction_output_tokens
            + claims * scenario.verification_output_tokens
            + shape["sections"] * _SECTION_OUTPUT_TOKENS
            + _EXECUTIVE_OUTPUT_TOKENS,
        )
        judge_usd = 0.0
        if judge_model:
            kept = round(claims * kept_fraction)
            if judge_sample:
                kept = min(kept, judge_sample)
            kept += min(extra_judged, claims)
            per_item = scenario.text_tokens_per_item + (
                scenario.tokens_per_pdf if scenario.pdfs_per_bundle else 0
            )
            judge_usd = token_cost(
                judge_model,
                input_tokens=kept * (scenario.judge_cited_items * per_item + _PROMPT_OVERHEAD_TOKENS),
                cached_input_tokens=0,
                output_tokens=kept * _JUDGE_OUTPUT_TOKENS,
            )
        estimates[scenario.name] = {
            "browse_jobs": jobs,
            "claims": claims,
            "browse_usd": round(browse_usd, 2),
            "model_usd": round(model_usd, 2),
            "judge_usd": round(judge_usd, 2),
            "total_usd": round(browse_usd + model_usd + judge_usd, 2),
        }
    return {"shape": shape, "model": model, "judge_model": judge_model, "estimates": estimates}


def scenario_table() -> list[dict[str, Any]]:
    return [asdict(scenario) for scenario in SCENARIOS]
