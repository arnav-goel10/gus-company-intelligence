# GUS: company research where every claim cites its source

[![CI](https://github.com/arnav-goel10/gus-company-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/arnav-goel10/gus-company-intelligence/actions/workflows/ci.yml)

Give GUS a company name. A browse agent collects the company's own pages, filings,
court and registry searches and news, and up to 19 specialist LLM agents across 8 domains extract claims from
what it found. A verifier then re-reads every claim against the sources it cites. Claims
that fail are suppressed and listed with the reason, and the report is blocked outright
when too few sections survive.

## See it in 30 seconds

No API keys, no network, about one second:

```bash
git clone https://github.com/arnav-goel10/gus-company-intelligence && cd gus-company-intelligence
PYTHONPATH=src python3 -m gus_reporter.cli \
  --request examples/sample_run/request.json --replay examples/sample_run/cassette
```

This replays a recorded run of the full pipeline and writes the report, every claim and
every intermediate artifact. An excerpt of the [output](examples/sample_run/output/report.md):

```text
Publish decision: publish. Coverage and verification thresholds were met.
10 claims kept, 5 suppressed by verification, 23 sources captured.

- FY2025 revenue was $48.2 million, up 31% from $36.8 million.
  > FY2025 revenue $48.2 million, up 31% from $36.8 million
  Source: direct_financials_001 (https://quillfeather.example/investors)

Suppressed claims
| Quillfeather reported a net profit in FY2025.       | contradicted          | The investor page reports a net loss of $6.4 million for FY2025. |
| Quillfeather does not appear on any sanctions list. | insufficient_evidence | No sanctions screening source was captured, so absence from a list was never checked. |
| Quillfeather was incorporated in Delaware in 2017.  | unsupported           | No captured source states where the company is incorporated. |
```

The sample company is fictional, and its web pages and model answers were written by
hand so the demo costs nothing. The code between them is the production path.
[examples/sample_run/README.md](examples/sample_run/README.md) says exactly which parts
are authored. CI replays it on every push and fails if the output changes.

## How a run works

```
request ──► planning ──► acquisition ──► extraction ──► verification ──► assembly
              │              │               │               │              │
              │              │               │               │              └─ supported claims only,
              │              │               │               │                 blocks on weak coverage
              │              │               │               └─ each claim re-judged against
              │              │               │                  the sources it cites
              │              │               └─ up to 19 specialists in 8 domains emit
              │              │                  typed, schema-validated claims
              │              └─ browse waves spawn child jobs from what they find,
              │                 then raw HTML, text and PDF are downloaded directly
              └─ deterministic seed jobs: IR pages, SEC index, court and
                 registry searches, news searches, explicit URLs
```

Each stage is one module behind a small interface (`src/gus_reporter/providers.py`), so
the same pipeline runs against the live APIs, a recorder, or a replay cassette.

| Stage | Module | What it decides |
|---|---|---|
| Planning | `planning.py` | The first wave of browse jobs, purely from the request |
| Discovery | `discovery.py` | Which found URLs become new browse jobs and which are downloaded, by one policy per category |
| Acquisition | `acquisition.py` | Up to 6 browse waves under a 50-job cap, then raw capture |
| Evidence | `evidence.py` | How sources are bundled for the model (12,000-char text cap, at most 4 PDFs) and which specialist sees which |
| Extraction | `extraction.py` | One model call per specialist; a failed call yields no claims |
| Verification | `verification.py` | Keep or suppress each claim; an API error suppresses, it never passes |
| Assembly | `assembly.py` | Section summaries from kept claims, risk matrix, publish or block |

A page that shows a CAPTCHA or bot wall is recorded as blocked and skipped. GUS uses a
standard browser profile, never retries around a block, and its raw fetcher identifies
itself with a project user agent.

## Evaluation

[`eval/`](eval/README.md) holds a 15-company suite: 12 real companies listed in the US,
Canada and Singapore, and 3 names that do not exist, one of them a letter away from
Peloton. Any claim kept about a fictitious company is an error by construction. The
harness reports, with Wilson 95% intervals:

- **kept-claim precision**, from a separate `gpt-5.4` judge that sees only the cited
  sources and must quote the supporting sentence, checked against hand labels with
  Cohen's kappa
- **suppression and over-suppression rates**, with outage suppressions counted apart
- **citation validity**, deterministically: cited ids resolve and the quote is in a cited source
- **fictitious-company leakage**

```bash
PYTHONPATH=src python3 -m gus_reporter.evaluation estimate   # prints the cost, spends nothing
PYTHONPATH=src python3 -m gus_reporter.evaluation run --max-usd 150
```

`run` prints the estimate and waits for `yes` before any paid call. The suite has not
been run against live APIs yet, so this README reports no precision number.

## Cost

Prices are list prices read on 9 Nov 2026: `gpt-5.4-mini` $0.75 in, $0.075 cached,
$4.50 out per million tokens, `gpt-5.4` $2.50, $0.25, $15.00, and Tinyfish $0.016 per
agent step. Call counts follow from the request exactly. Page length, PDF count, claims
found and browse steps are assumptions with a low, typical and high value
(`src/gus_reporter/cost.py`). Saved output is in [`results/`](results/).

| | Low | Typical | High |
|---|---|---|---|
| One company, all 8 domains (Apple request) | $2.65 | $18.21 | $131.89 |
| Full eval, 15 companies, judge on 20 kept and 5 suppressed claims each | $40.53 | $350.97 | $2,270.10 |

The high case is dominated by verification. Every extracted claim cites the whole
evidence bundle its specialist saw, so each verification call re-sends up to four PDFs.
After a real run, `usage.json` holds the billed tokens and the CLI prints the exact cost.

## Quickstart

Standard library only, Python 3.9 or newer.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests      # 73 tests, under a second

cp .env.example .env    # OPENAI_API_KEY and TINYFISH_API_KEY
PYTHONPATH=src python3 -m gus_reporter.cli --request examples/company_request.json
PYTHONPATH=src python3 -m gus_reporter.cli --request req.json --record cassettes/acme   # live, and replayable
```

A real run needs both keys: OpenAI for extraction, verification and synthesis, Tinyfish
for browsing. The model defaults to `gpt-5.4-mini` (`OPENAI_MODEL`). Every run writes its
artifacts to `runs/<timestamp>-<company>/`, including `report.md` and `usage.json`.

## Tests

The golden tests in `tests/test_golden_behaviour.py` pin what planning, discovery and a
full end-to-end run send to the model and write to disk. They were written before
`pipeline.py` (2,600 lines) was split into the modules above, and stayed green through
the split. The replay tests check that a recorded run reproduces exactly, and that any
change to a prompt or to the evidence fails replay instead of passing silently.

## Operator UI

A zero-dependency local web UI shows a run live: stage progress, per-source status with
browser preview links, the risk matrix, section summaries, and every artifact.

```bash
PYTHONPATH=src python3 -m gus_reporter.ui_server --host 127.0.0.1 --port 8787
```

Set `GUS_UI_TOKEN` to require a bearer token on every API route.

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

Only `company_name` is required. Explicit `source_jobs` are supported, as are
authenticated Tinyfish workflows (`tinyfish_use_vault`, `tinyfish_credential_item_ids`,
`tinyfish_enable_agent_memory`) and a per-job `proxy_country_code` for region-specific pages.

## Known limits

- An extracted claim cites every source its specialist saw. The verifier narrows that to
  the supporting ids, but verification cost scales with bundle size.
- The claim-specific text comes before the evidence in each verification prompt, so
  repeated evidence is not prompt-cached.
- No SEC or XBRL parser yet. Financial pages go through browse extraction and raw capture.
- Source planning is rule-based, with no learned source ranking.

## License

MIT, see [LICENSE](LICENSE).
