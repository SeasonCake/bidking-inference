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
- selected historical adaptations in `research/`: v0.3.0 TCP reassembly, table text
  decoding and OCR normalization; Aisha window/grid helpers; the later Match10 trace
  limiter and managed scheduler with self-contained harnesses;
- newly written synthetic visibility/replay/table-diff examples and reviewed historical
  aggregate summaries/charts, separately classified in `docs/research/PROVENANCE.json`.

Excluded:

- unselected private product source and history, including the full `0.2.8+` engine;
- current game tables, extracted assets, captured traffic, unreviewed screenshots, and
  real match data;
- client, server, activation, packaging, deployment, protection, and production tooling;
- private model parameters, field mappings, calibration tables, business thresholds,
  decision strategy, incident logs, product diagnostics, and receipts;
- third-party binaries or source copied from reference projects.

The earlier failure-workflow and evidence-lifecycle cases are independent synthetic
reconstructions. The new research layer also includes genuine historical adaptations:
each names its exact source and changes. Some selected helpers remain reused in later
private versions; old age is not a claim of non-use. This limited selection does not
disclose the full calibration/compensation or integration chain.

The maintained package must remain useful without the private project, a game
installation, credentials, GUI interaction, or machine-specific paths. The legacy
snapshot is historical reference material and is not required by the package.

Documentation screenshots provide historical context only. They are not fixtures,
runtime inputs, calibration evidence, or permission to redistribute standalone game
assets.

The intended level is a maintained public core with historical and research layers: another
developer can install the maintained package, adapt shareable records, score
multidimensional candidates, inspect uncertainty/calibration/sensitivity, run synthetic
pool simulations, study the early real implementation and tables, and run the selected
standalone research tools. Research is source-checkout-only, outside the stable API.
Unimplemented C++/identity labs are documented proposals, not completed integrations.
Reconstructing
the current BidKing product still requires the excluded newer model, calibration,
policy, integrations, client evolution, and operational code.
