# Contributor guidance

This repository is a public-candidate extraction, not a mirror of the private product.

- Keep the package domain-neutral and deterministic.
- Use only synthetic fixtures committed in this repository.
- Do not add current game tables, captured traffic, customer diagnostics, `0.2.8+`
  product/runtime code, activation, deployment, packaging, private paths, or copied
  third-party assets.
- `legacy/` is the only historical exception. Additions there require an exact pre-0.2.8
  Git identity, a frozen allowlist, secret/PII/path/binary review, a reproducible manifest
  digest, and a clear historical/unsupported lifecycle. Never update legacy files from
  the current product tree by similarity or filename alone.
- Author-supplied documentation screenshots may be added only after privacy/metadata
  review, an exact allowlist update, and a `NOTICE.md` statement separating third-party
  imagery from the MIT-licensed code. Never extract or vendor game assets from them.
- Run `python scripts/verify.py` before committing.
- Keep `LICENSE`, package metadata, README files, and companion-repository links
  consistent when public project identity changes.
