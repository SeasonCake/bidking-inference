# Contributor guidance

Use README and OPEN_SOURCE_BOUNDARY.md for the selected public scope. Read
product/README.md before changing the modern application layer. This is a reviewed
source export, not a mirror of private Git history.

- Preserve the independent auction_inference v0.1.0 API, frozen legacy snapshots and
  research classifications. Product changes do not silently change their versions.
- Keep author-owned product implementation usable with self-supplied inputs. Preserve
  schemas, units and numerical boundaries; use fictional fixtures for public tests.
  Missing data or unsupported platforms are not passing tests.
- Keep secrets, real customer records, captured traffic, private machine/production
  details and unlicensed assets out of files and history. Public examples must not
  default to the author's live service or weaken administrator authorization.
- Update the product manifest after selected source changes. Distinguish source,
  game-derived content, external dependencies and generated builds.
- Run `python scripts/verify.py` for the public package/export checks, then the relevant
  product checks in product/VERIFICATION.md. Do not target the hosted service or launch
  a game/capture as a substitute for local tests.
- Stage owned paths explicitly, preserve unrelated work, and separate source publication,
  packaged application release and production deployment.
- Lead documentation with useful capabilities and runnable examples. Keep detailed
  provenance and maintenance evidence in their linked records.
