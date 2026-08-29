# BidKing Inference

A small, domain-neutral Python library for reasoning about hidden auction inventory from
bounded observations. It demonstrates three reusable primitives:

- strict discrete hypotheses and interval evidence;
- fail-closed constraint filtering and posterior normalization;
- deterministic Monte Carlo summaries over synthetic value pools.

This candidate is intentionally **not** the BidKing product. It contains no game tables,
capture code, client UI, production service, private calibration, or real user data.

## Companion skills

The companion repository
[`evidence-first-agent-skills`](https://github.com/SeasonCake/evidence-first-agent-skills)
publishes reusable workflows distilled from private BidKing engineering experience:
evidence levels, release verification, CLI contracts, UI acceptance, handoff recovery,
and fresh-clone truth. It contains no private product source, incident records, customer
data, or production topology. See `PROJECT_RELATIONSHIP.md` for the boundary.

## Quick start

Python 3.10 or newer is sufficient; runtime code uses only the standard library.

```powershell
python scripts/run_example.py examples/synthetic_session.json
python scripts/verify.py
```

After an editable or wheel install, the equivalent console entry is
`auction-inference examples/synthetic_session.json`.

The CLI prints JSON with accepted/rejected hypotheses, normalized posterior estimates,
and a deterministic Monte Carlo summary.

## Status

Copyright (c) 2026 SeasonCake. Released under the MIT License. Contributions use the
Developer Certificate of Origin 1.1 (`git commit -s`); no CLA is required.
