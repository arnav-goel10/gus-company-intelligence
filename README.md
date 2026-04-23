# GUS Automated Company Report Generator

This repository is the first implementation slice of an automated multi-agent
company intelligence pipeline using Tinyfish for acquisition and OpenAI
`gpt-5.4-mini` by default for structured extraction and verification.

The current scaffold is intentionally narrow:

- Tinyfish executes discovery/navigation URL batches, captures source maps and
  direct document links, and retries blocked financial pages with `stealth`
  after a `lite` first pass.
- Direct raw-source fetching downloads or references the actual HTML, text, and
  PDF artifacts that OpenAI analyzes.
- The financial coordinator now fans out into specialist sub-agents for document
  discovery, statement metrics, capital structure, guidance/funding, and
  finance-specific red flags.
- Domain agents focus on individual sections such as financials, legal, news,
  leadership, workforce, and risk.
- OpenAI `gpt-5.4-mini` extracts typed claims for each domain agent by default.
- OpenAI `gpt-5.4-mini` verifies each claim against its source evidence by default.
- Section agents synthesize section summaries.
- An executive aggregation pass compiles the overall report.
- The report assembler publishes only verified claims and blocks output when
  section coverage is too weak.

There is no runtime human-in-the-loop path in this implementation.

## Project Layout

- `src/gus_reporter/config.py`: environment loading
- `src/gus_reporter/models.py`: request, evidence, claim, and report models
- `src/gus_reporter/agents.py`: domain-agent definitions and routing
- `src/gus_reporter/tinyfish_client.py`: Tinyfish batch/async client with
  vault, agent-memory, and `lite -> stealth` retry support
- `src/gus_reporter/raw_source_client.py`: direct raw HTML/text/PDF acquisition
  layer used after Tinyfish discovery
- `src/gus_reporter/openai_client.py`: Responses API client for the configured OpenAI model
- `src/gus_reporter/pipeline.py`: end-to-end pipeline
- `src/gus_reporter/ui_server.py`: zero-dependency local operator UI
- `src/gus_reporter/ui_state.py`: UI run-state projection for live progress
- `src/gus_reporter/storage.py`: run artifact persistence
- `examples/company_request.json`: example input
- `tests/`: stdlib unit tests for deterministic logic

## Environment

Copy `.env.example` values into your shell or a local `.env` loader of choice.
This project does not depend on third-party Python packages.

Required variables:

- `OPENAI_API_KEY`
- `TINYFISH_API_KEY`

Useful Tinyfish tuning variables:

- `TINYFISH_DEFAULT_BROWSER_PROFILE` defaults to `lite`
- `TINYFISH_BATCH_SIZE` controls `run-batch` chunking
- `TINYFISH_MAX_TOTAL_JOBS` caps the total Tinyfish jobs a single run may fan
  out into
- `TINYFISH_MAX_ACQUISITION_WAVES` caps how many discovery-expansion waves the
  financial crawler may execute
- `TINYFISH_RETRY_WITH_STEALTH` enables automatic anti-bot retry for blocked
  financial jobs
- `TINYFISH_REQUEST_MAX_RETRIES` and `TINYFISH_REQUEST_RETRY_BACKOFF_SECONDS`
  control transport-level retries for 429 and 5xx responses

Useful OpenAI tuning variables:

- `OPENAI_REQUEST_MAX_RETRIES` and `OPENAI_REQUEST_RETRY_BACKOFF_SECONDS`
  control transport-level retries for 429 and 5xx responses
- `OPENAI_EXTRACTION_MAX_WORKERS` and `OPENAI_VERIFICATION_MAX_WORKERS`
  control parallel OpenAI fan-out during extraction and verification

Model policy:

- The default model is `gpt-5.4-mini`.
- You can override it with `OPENAI_MODEL` if you want to move to a different model later.

## Usage

Run deterministic tests first:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Run a report:

```bash
PYTHONPATH=src python3 -m gus_reporter.cli --request examples/company_request.json
```

Artifacts are written under `runs/<timestamp>-<company-slug>/`.

Run the operator UI:

```bash
PYTHONPATH=src python3 -m gus_reporter.ui_server --host 127.0.0.1 --port 8787
```

Then open [http://127.0.0.1:8787](http://127.0.0.1:8787).

The UI currently provides:

- a financial-run launcher from a single company-name prompt
- live stage progress across planning, acquisition, extraction, verification, and reporting
- per-source Tinyfish status tracking and preview links when available
- final report rendering with risk matrix and section summaries
- artifact inspection for every JSON file written during the run

## Target Report Format

The implementation is being shaped around the most common diligence-report
pattern used by commercial, legal, compliance, and investment teams:

1. Executive summary
2. Scope and methodology
3. Entity profile
4. Key findings
5. Risk matrix
6. Domain sections such as financial, legal, news, leadership, and workforce
7. Recommendations
8. Source log / appendix

## Request Shape

The request file is JSON. Minimal example:

```json
{
  "company_name": "Example Corp",
  "company_website": "https://example.com",
  "primary_jurisdiction": "US",
  "focus_areas": [
    "company_overview",
    "financials",
    "legal",
    "industry",
    "leadership",
    "employees",
    "news",
    "risk_signals"
  ]
}
```

You can also supply explicit `source_jobs` for better coverage. For authenticated
workflows, request-level Tinyfish fields are supported:

- `tinyfish_use_vault`
- `tinyfish_credential_item_ids`
- `tinyfish_enable_agent_memory`
- per-job `source_type`, `preferred_run_mode`, and `allow_stealth_fallback`

The financial planner now seeds:

- official investor-relations pages when provided
- `/investor-relations` and `/investors` candidates when no IR URL is given
- explicit financial source URLs such as annual report PDFs
- SEC filing index coverage when a ticker is supplied

Financial jobs use tighter Tinyfish goals that ask for bounded, structured JSON
including reporting periods, key metrics, document links, capital events, red
flags, and data gaps.

## Current Limits

- No external vector store or graph database is wired in yet.
- Retrieval is source-bounded per claim in this slice.
- Source planning is deterministic from the request plus explicit source jobs.
- The local UI surfaces Tinyfish live preview URLs when a run exposes them, but
  the UI is still operator-facing rather than end-user polished.
- Company-name-only source discovery is not implemented yet; the current live
  flow still works best when the request includes website, ticker, or known
  source URLs.
- There is not yet a source-specific parser for SEC HTML or XBRL responses; the
  current financial slice still relies on Tinyfish page extraction plus OpenAI
  verification.

## Near-Term Next Steps

1. Add evidence deduplication and temporal entity resolution.
2. Add a graph-backed ownership and related-party layer.
3. Add batch backfills and background OpenAI jobs for larger runs.
4. Add source-specific adapters for SEC, registries, news, and court sources.
