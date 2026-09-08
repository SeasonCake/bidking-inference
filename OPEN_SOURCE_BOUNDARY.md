# Open-source boundary

Included:

- newly written domain-neutral constraints and composable observations;
- multi-field posterior scoring, bounded joint enumeration, and diagnostics;
- binary forecast calibration and leave-one-evidence-out sensitivity diagnostics;
- generic record adapters and deterministic weighted-pool simulation;
- synthetic fixtures and examples;
- newly written generic failure-workflow reference code for JSON snapshots, refresh
  handles and fake document-save outcomes, with deterministic fault injection;
- a frozen, reviewed subset of the real `v0.2.0-hotfix1` source and pre-0.2.8 historical
  map/item tables under `legacy/`;
- reviewed, author-supplied documentation screenshots listed in `NOTICE.md`;
- tests, CI, contributor documentation, and public-boundary checks.

Excluded:

- private `0.2.8+` product source and history;
- current game tables, extracted assets, captured traffic, unreviewed screenshots, and
  real match data;
- client, server, activation, packaging, deployment, protection, and production tooling;
- private model parameters, field mappings, calibration tables, business thresholds,
  decision strategy, incident logs, product diagnostics, and receipts;
- third-party binaries or source copied from reference projects.

The engineering cases are independent synthetic reconstructions, not renamed extracts
of product persistence, authorization, network or calculation implementations. They do
not import the private repository or disclose its protocols, constants or incident text.

The maintained package must remain useful without the private project, a game
installation, credentials, GUI interaction, or machine-specific paths. The legacy
snapshot is historical reference material and is not required by the package.

Documentation screenshots provide historical context only. They are not fixtures,
runtime inputs, calibration evidence, or permission to redistribute standalone game
assets.

The intended level is a medium open core with a historical reference layer: another
developer can install the maintained package, adapt shareable records, score
multidimensional candidates, inspect uncertainty/calibration/sensitivity, run synthetic
pool simulations, and study the early real implementation and tables. Reconstructing
the current BidKing product still requires the excluded newer model, calibration,
policy, integrations, client evolution, and operational code.
