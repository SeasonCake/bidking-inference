# Contributor guidance

This repository is a public-candidate extraction, not a mirror of the private product.

- Keep the package domain-neutral and deterministic.
- Use synthetic fixtures for runnable examples. Separately labeled, explicitly selected
  historical aggregates may be used for charts; never label measured history synthetic.
- Do not add current game tables, captured traffic, customer diagnostics, unselected
  product/runtime code, activation, deployment, packaging, private paths, or copied
  third-party assets.
- Additions to `legacy/` require an exact pre-0.2.8
  Git identity, a frozen allowlist, secret/PII/path/binary review, a reproducible manifest
  digest, and a clear historical/unsupported lifecycle. Never update legacy files from
  the current product tree by similarity or filename alone.
- `research/` is the selected, source-checkout-only research layer. Its limited later
  source adaptations are enumerated in `docs/research/PROVENANCE.json`: V30 transport,
  table decoding and OCR helpers; selected Aisha helpers; M10 scheduling/trace helpers.
  This is not permission to copy a whole newer engine, internal calibration tools,
  current parameters, live integration, or the entire private source history.
- Keep `src/auction_inference`, its public API and its independent package version
  separate from research dependencies. Build/reproduction outputs go under ignored
  `outputs/`; do not commit executables, PDBs or other generated runtime artifacts.
- Every research adaptation needs an exact source identity and adaptation note;
  new teaching code must be labeled as such. Preserve the old 56-source/7-data pins.
- Author-supplied documentation screenshots may be added only after privacy/metadata
  review, an exact allowlist update, and a `NOTICE.md` statement separating third-party
  imagery from the MIT-licensed code. Never extract or vendor game assets from them.
- Run `python scripts/verify.py` before committing.
- Lead README and release notes with user value, architecture, runnable examples, and
  useful contribution routes. Keep scope, provenance, privacy, and review mechanics in
  their relevant documents; link them briefly instead of repeating a promotional-page module.
- Keep `LICENSE`, package metadata, README files, and companion-repository links
  consistent when public project identity changes.
