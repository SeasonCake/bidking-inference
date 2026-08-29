<p align="right"><a href="README.md">简体中文</a> · <strong>English</strong></p>

# BidKing Inference

[![CI](https://github.com/SeasonCake/bidking-inference/actions/workflows/ci.yml/badge.svg)](https://github.com/SeasonCake/bidking-inference/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

A domain-neutral discrete-inference Python library distilled from the engineering of the
private BidKing product. It compares bounded candidate states against incomplete,
approximate, or categorical observations and provides posterior and deterministic
simulation summaries.

> **Version note:** the private BidKing product currently follows the `0.3.4` line. This
> repository is an independently versioned public inference core; its current public
> release is [`v0.1.0`](https://github.com/SeasonCake/bidking-inference/releases/tag/v0.1.0).
> They are not the same executable product and do not share version semantics.

## Product context

These historical screenshots show the real product setting that motivated the public
inference core. This repository does not contain the private client, game integration,
calibration data, or runtime shown below.

<p align="center">
  <img src="docs/assets/screenshots/bidking-ui-compact-dark-historical.png"
       alt="Historical compact dark BidKing interface" width="428">
</p>

<p align="center"><em>Historical compact dark layout from an earlier development version.</em></p>

<p align="center">
  <img src="docs/assets/screenshots/bidking-live-gameplay-historical.png"
       alt="Historical BidKing live integration and map view" width="1100">
</p>

<p align="center"><em>Historical live integration, map view, and settlement inference; a 0.3.4 demo video and current screenshots will follow.</em></p>

Third-party game imagery, names, trademarks, and assets visible in screenshots are not
covered by this repository's MIT License. See [`NOTICE.md`](NOTICE.md) and the
[screenshot notes](docs/assets/screenshots/README.md).

## What the public core includes

- strict candidates, scalar attributes, and fail-closed input parsing;
- exact, interval, approximate, and categorical evidence;
- stable log-space posterior scoring for multiple weighted fields;
- bounded joint-state enumeration and posterior diagnostics;
- deterministic weighted-pool simulation with or without replacement;
- record adapters, a JSON CLI, synthetic examples, and an installable package.

This repository is intentionally **not** the BidKing product. It contains no game
tables, capture code, client UI, production service, private calibration, or real user
data. The public core is sufficient to build a small discrete-inference tool using your
own shareable candidate data. See [`OPEN_SOURCE_BOUNDARY.md`](OPEN_SOURCE_BOUNDARY.md).

## Quick start

Python 3.10 or newer is sufficient; runtime code uses only the standard library.

```powershell
python scripts/run_example.py examples/synthetic_session.json
python scripts/run_example.py examples/synthetic_multidimensional.json
python scripts/verify.py
```

Install and run the CLI:

```powershell
python -m pip install .
auction-inference examples/synthetic_session.json
```

## Examples and documentation

```powershell
python examples/constraint_filter.py
python examples/posterior_summary.py
python examples/monte_carlo_summary.py
python examples/joint_posterior.py
python examples/pool_simulation.py
python examples/record_adapter.py
```

- [Public API](docs/PUBLIC_API.md)
- [Strict input schema](docs/INPUT_SCHEMA.md)
- [Project relationship and boundary](PROJECT_RELATIONSHIP.md)
- [Contribution guide](CONTRIBUTING.md)
- [Maintenance and support](MAINTAINING.md)

## Companion skills

The companion repository
[`evidence-first-agent-skills`](https://github.com/SeasonCake/evidence-first-agent-skills)
publishes reusable architecture-survey, claim-verification, CLI-contract, and
agent-compatibility workflows distilled from BidKing/LC2 engineering experience. It
contains no private product source, customer data, raw incident records, or production
topology.

## License and contribution

Copyright (c) 2026 SeasonCake. Released under the MIT License. Contributions use the
Developer Certificate of Origin 1.1 (`git commit -s`); no CLA is required.
