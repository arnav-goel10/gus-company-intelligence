# GUS · autonomous company-intelligence pipeline

[![CI](https://github.com/arnav-goel10/gus-company-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/arnav-goel10/gus-company-intelligence/actions/workflows/ci.yml)

GUS turns a company name into a source-backed due-diligence report. A planner decomposes the question and dispatches financial, legal, and news agents in parallel; every extracted claim carries provenance back to a captured source, and a verification pass re-checks each claim against its own evidence before it can appear in the report.

The design premise: **a fabricated claim costs more than a missing one.** That inverts the usual generation objective, so the whole pipeline is fail-closed. Unverified claims are suppressed, visibly, and the assembler blocks the report entirely when section coverage is too weak to stand behind.

## How a run works

```
request ──► planning ──► acquisition ──► extraction ──► verification ──► assembly
              │              │               │               │              │
              │              │               │               │              └─ verified claims only;
              │              │               │               │                 blocks on weak coverage
              │              │               │               └─ every claim re-checked against
              │              │               │                  its own source evidence
              │              │               └─ domain agents emit typed claims
              │              │                  (financial fans out into specialists)
              │              └─ Tinyfish browses sources (lite, retries blocked
              │                 pages with stealth), then raw HTML/text/PDF capture
              └─ deterministic source seeding: IR pages, SEC filing
                 indexes, explicit URLs, web-search discovery
```

1. **Planning** seeds source jobs deterministically from the request: investor-relations pages, `/investors` candidates, SEC filing indexes when a ticker is supplied, explicit URLs, and web-search discovery when only a name is given.
2. **Acquisition** runs Tinyfish browse jobs in bounded batches (`lite` first, automatic `stealth` retry for blocked financial pages), then downloads the actual HTML, text, and PDF artifacts the models will read.
3. **Extraction** routes evidence to domain agents (financials, legal, news). The financial coordinator fans out into specialists for document discovery, statement metrics, capital structure, guidance and funding, and finance-specific red flags. Each agent emits typed, schema-validated claims.
4. **Verification** re-reads every claim against the specific evidence it cites. A claim that does not survive is dropped, not softened.
5. **Assembly** builds the report from verified claims only: executive summary, entity profile, key findings, risk matrix, domain sections, recommendations, and a full source log.

Every stage writes JSON artifacts under `runs/<timestamp>-<company>/`, so a finished run is fully auditable: claim → source → verification status.

## Operator UI

A zero-dependency local web UI shows the run as a live intelligence thread: stage progress, per-source acquisition status with Tinyfish preview links, the risk matrix, section summaries, and an inspector for every artifact the run wrote.

```bash
PYTHONPATH=src python3 -m gus_reporter.ui_server --host 127.0.0.1 --port 8787
```

Open http://127.0.0.1:8787. Set `GUS_UI_TOKEN` to require a bearer token for every API route (the UI prompts for it); leave it unset for local use.

## Quickstart

No third-party Python packages are required, the entire pipeline is standard library.

```bash
cp .env.example .env       # add OPENAI_API_KEY and TINYFISH_API_KEY

# deterministic tests (also run in CI)
PYTHONPATH=src python3 -m unittest discover -s tests -v

# run a report from a request file
PYTHONPATH=src python3 -m gus_reporter.cli --request examples/company_request.json
```

Required environment: `OPENAI_API_KEY`, `TINYFISH_API_KEY`. The default model is `gpt-5.4-mini`, overridable with `OPENAI_MODEL`. Concurrency, batching, timeouts, and retry backoff are all tunable through the variables documented in `.env.example`.

## Request shape

```json
{
  "company_name": "Example Corp",
  "company_website": "https://example.com",
  "primary_jurisdiction": "US",
  "ticker": "EXMP",
  "investor_relations_url": "https://example.com/investors",
  "focus_areas": ["financials", "legal", "news"]
}
```

Only `company_name` is required; everything else improves coverage. Explicit `source_jobs` are supported, as are authenticated Tinyfish workflows (`tinyfish_use_vault`, `tinyfish_credential_item_ids`, `tinyfish_enable_agent_memory`, and per-job `source_type`, `preferred_run_mode`, `allow_stealth_fallback`).

## Project layout

| Path | Responsibility |
|---|---|
| `src/gus_reporter/pipeline.py` | end-to-end orchestration: planning, waves, extraction, verification, assembly |
| `src/gus_reporter/agents.py` | domain-agent definitions and routing |
| `src/gus_reporter/tinyfish_client.py` | Tinyfish batch client: vault, agent memory, lite to stealth retry |
| `src/gus_reporter/raw_source_client.py` | direct raw HTML/text/PDF acquisition after discovery |
| `src/gus_reporter/openai_client.py` | Responses API client with retries and timeouts |
| `src/gus_reporter/models.py`, `schemas.py` | request, evidence, claim, and report types with strict schemas |
| `src/gus_reporter/ui_server.py`, `ui_state.py`, `web/` | zero-dependency operator UI and live run-state projection |
| `src/gus_reporter/storage.py` | run artifact persistence |
| `tests/` | 45 stdlib unit tests over the deterministic logic |

## Design decisions

- **Fail-closed, everywhere.** Verification can only remove claims. The assembler refuses to publish sections whose coverage is too thin, and says so in the artifact trail.
- **Provenance is structural, not decorative.** Claims reference the evidence they came from; the report renderer cannot cite what the pipeline did not capture.
- **Zero runtime dependencies.** Standard library only: the pipeline, the HTTP clients, and the operator UI. Nothing to install, nothing to audit beyond this repo.
- **Bounded fan-out.** Worker pools, batch sizes, job caps, and acquisition waves are all capped by configuration, because an autonomous crawler without limits is a liability.

## Current limits

- Retrieval is source-bounded per claim; there is no external vector store or graph database yet.
- Source planning is deterministic from the request plus discovery waves; no learned source ranking.
- No source-specific parsers for SEC HTML or XBRL yet; financial pages go through Tinyfish extraction plus model verification.
- The operator UI is built for the operator, not for end-user delivery.

## Roadmap

1. Evidence deduplication and temporal entity resolution.
2. A graph-backed ownership and related-party layer.
3. Published benchmark runs with full artifact traces.
4. Source-specific adapters for SEC, registries, news, and court sources.

## License

Code is MIT licensed (see [LICENSE](LICENSE)).
