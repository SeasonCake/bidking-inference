# BidKing Inference

A domain-neutral Python library for bounded discrete inference. It is useful when a
small set of candidate states must be compared against incomplete, approximate, or
categorical observations. The medium open core includes:

- strict candidates, scalar attributes, and fail-closed observation parsing;
- exact, interval, approximate, and categorical evidence;
- stable log-space posterior scoring for multiple weighted fields;
- bounded joint-state enumeration and posterior diagnostics;
- deterministic weighted-pool simulation with or without replacement;
- record adapters, a JSON CLI, synthetic examples, and an installable package.

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
python scripts/run_example.py examples/synthetic_multidimensional.json
python scripts/verify.py
```

After an editable or wheel install, the equivalent console entry is
`auction-inference examples/synthetic_session.json`.

The legacy fixture prints accepted/rejected hypotheses, normalized estimates, and a
deterministic Monte Carlo summary. The multidimensional fixture demonstrates hard and
soft evidence, weighted posterior rows, diagnostics, and a smallest credible set.

## Install

From a source checkout:

```powershell
python -m pip install .
auction-inference examples/synthetic_session.json
```

The package has no runtime dependencies. Supported Python versions are exercised by CI.

## Examples and API

```powershell
python examples/constraint_filter.py
python examples/posterior_summary.py
python examples/monte_carlo_summary.py
python examples/joint_posterior.py
python examples/pool_simulation.py
python examples/record_adapter.py
```

- `docs/PUBLIC_API.md` documents the supported imports and failure behavior.
- `docs/INPUT_SCHEMA.md` documents the strict synthetic CLI schema.
- `OPEN_SOURCE_BOUNDARY.md` explains what is intentionally excluded.

The public core is sufficient to build a small discrete-inference tool with your own
shareable candidate data. It deliberately does not include BidKing field mappings,
calibration tables, product thresholds, business rules, or runtime integration.

## Project maintenance

See `CONTRIBUTING.md`, `MAINTAINING.md`, `SUPPORT.md`, `SECURITY.md`, and `CHANGELOG.md`.
The public issue and pull-request templates require synthetic, shareable evidence.

## License and contribution

Copyright (c) 2026 SeasonCake. Released under the MIT License. Contributions use the
Developer Certificate of Origin 1.1 (`git commit -s`); no CLA is required.
