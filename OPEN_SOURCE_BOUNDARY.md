# Open-source boundary

Included:

- newly written domain-neutral constraints and composable observations;
- multi-field posterior scoring, bounded joint enumeration, and diagnostics;
- generic record adapters and deterministic weighted-pool simulation;
- synthetic fixtures and examples;
- reviewed, author-supplied documentation screenshots listed in `NOTICE.md`;
- tests, CI, contributor documentation, and public-boundary checks.

Excluded:

- private product source and history;
- game tables, extracted assets, captured traffic, unreviewed screenshots, and real match data;
- client, server, activation, packaging, deployment, protection, and production tooling;
- private model parameters, field mappings, calibration tables, business thresholds,
  decision strategy, incident logs, product diagnostics, and receipts;
- third-party binaries or source copied from reference projects.

The repository must remain useful without the private project, a game installation,
credentials, GUI interaction, or machine-specific paths.

Documentation screenshots provide historical context only. They are not fixtures,
runtime inputs, calibration evidence, or permission to redistribute standalone game
assets.

The intended level is a medium open core: another developer can install the package,
adapt shareable records, score multidimensional candidates, inspect uncertainty, and
run synthetic pool simulations. Reconstructing the BidKing product still requires the
excluded data model, calibration, policy, integrations, and operational code.
