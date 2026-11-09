# Offline sample run

A full pipeline run you can reproduce with no API keys and no network:

```bash
PYTHONPATH=src python3 -m gus_reporter.cli \
  --request examples/sample_run/request.json \
  --replay examples/sample_run/cassette
```

It writes a fresh run directory identical to [`output/`](output/) (CI checks this).
Start with [`output/report.md`](output/report.md): ten kept claims, each next to the
source it cites, and five suppressed claims with the verifier's reason.

**What is real and what is authored.** Quillfeather Robotics is fictional and every
URL is on a reserved `.example` domain. The web pages the browse agent "saw" and the
model's answers were written by hand in
[`scripts/build_sample_cassette.py`](../../scripts/build_sample_cassette.py), because
recording a live run costs money. Everything between them is the production code path:
source planning, browse waves and child-job discovery, raw fetch and HTML parsing,
evidence bundling and routing to the 16 specialists, the publish gate, the risk matrix
and the renderer. The authored verifier keeps a claim only if its quote appears in a
source it was shown, and rejects four claims on scripted grounds (a contradicted net
profit, an unchecked sanctions screen, an unsourced incorporation state, an
over-general coverage claim).

So this sample shows the mechanics and the artifact trail. It is not evidence of model
quality; that is what [`eval/`](../../eval/) measures.

To record a real cassette (needs both keys, costs money):

```bash
PYTHONPATH=src python3 -m gus_reporter.cli --request my_request.json --record cassettes/my_company
```
