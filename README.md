# BidKing Inference (public candidate)

A small, domain-neutral Python library for reasoning about hidden auction inventory from
bounded observations. It demonstrates three reusable primitives:

- strict discrete hypotheses and interval evidence;
- fail-closed constraint filtering and posterior normalization;
- deterministic Monte Carlo summaries over synthetic value pools.

This candidate is intentionally **not** the BidKing product. It contains no game tables,
capture code, client UI, production service, private calibration, or real user data.

## Quick start

Python 3.10 or newer is sufficient; runtime code uses only the standard library.

```powershell
python -m auction_inference.cli examples/synthetic_session.json
python scripts/verify.py
```

The CLI prints JSON with accepted/rejected hypotheses, normalized posterior estimates,
and a deterministic Monte Carlo summary.

## Status

The code and synthetic tests are ready for isolated review. Repository name, copyright
holder, and final license remain author decisions; see `LICENSE-DECISION.md`.
