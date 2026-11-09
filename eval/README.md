# Evaluation

`suite.json` holds 15 companies: 12 real ones across sectors and listings (US, Canada,
Singapore) and 3 that do not exist. One of those is a near miss, a letter away from a
real company. A fictitious company has no true facts, so any claim the pipeline keeps
about one is an error by construction, and its report should be blocked.

## Metrics

| Metric | How it is measured |
|---|---|
| Kept-claim precision | A judge model reads each kept claim next to only the sources it cites and returns `supported`, `partially_supported`, `not_supported` or `cannot_tell`. Strict precision counts `supported`; lenient adds `partially_supported`. Hand labels override the judge where both exist. |
| Suppression rate | Suppressed claims over extracted claims. Suppressions caused by an API error are counted separately, because they say nothing about the verifier's judgement. |
| Over-suppression | The judge also reads up to 5 suppressed claims per run; the share it finds supported. |
| Citation validity | Deterministic, no model: every cited evidence id exists in the run's evidence log, and the extraction quote appears in a cited source (case and whitespace folded). |
| Fictitious-company leakage | Claims kept, and reports not blocked, for the 3 fictitious names. |

Every rate is reported with a Wilson 95% interval.

The judge (default `gpt-5.4`) is a different and larger model than the pipeline's
verifier (`gpt-5.4-mini`). It never sees the verifier's verdict or reasoning, and it must
quote the sentence that supports the claim. A `supported` verdict whose sentence is not in
the cited text is downgraded to `not_supported`. Check it against hand labels before
trusting it: `score --labels` reports exact agreement and Cohen's kappa.

## Commands

```bash
# cost of the whole suite, spends nothing
PYTHONPATH=src python3 -m gus_reporter.evaluation estimate

# everything: prints the estimate, asks for "yes", runs, judges, scores
PYTHONPATH=src python3 -m gus_reporter.evaluation run --max-usd 150

# offline smoke test on the committed sample with its hand labels
PYTHONPATH=src python3 -m gus_reporter.evaluation score \
  --run examples/sample_run/output --labels eval/labels/sample_run.csv
```

`run` records a replay cassette per company, so a finished eval can be re-scored or
re-judged later without browsing again. Results go to `results/eval/<timestamp>/`
(`metrics.json`, `summary.md`, one run directory per company).

## Hand labels

`label-sheet` writes a CSV with one row per claim: `run, claim_id, pipeline_status,
statement, source_quote, sources, label, note`. `sources` holds an excerpt of each cited
source around the quote. The judge's verdict is left out so it cannot anchor the
labeller. Fill `label` with one of the four judge verdicts; empty rows are ignored.
`labels/sample_run.csv` is a filled example for the offline sample.
