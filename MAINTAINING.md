# Maintaining and releases

The repository uses Semantic Versioning. A release changes only when public behavior,
documentation, or maintenance metadata changes; private product state is not a release
input.

## Pull requests

- Keep the stable package domain-neutral; product source follows its own documented
  checks. Use synthetic inputs in public tests.
- Require a positive and a falsifying negative test for behavioral changes.
- Require DCO sign-off and a clean `python scripts/verify.py` result.
- Keep unrelated refactors and generated artifacts out of the change.
- Research is not part of package SemVer. Keep its provenance/fixture classifications
  current, verify C# changes with the independent runner, and visually inspect changed charts.
  The selected product layer has its own source manifest; private history and runtime
  data remain outside the public export.

## Release checklist

1. Review the exact commit range and public/private boundary.
2. Run the repository verifier on every supported Python version.
3. Build a wheel, install it into a fresh environment, and run the installed CLI.
4. Verify README links, license metadata, changelog, source archive, and checksum.
5. Tag `vX.Y.Z` only after the version and changelog agree.
6. Publish from the tagged commit and read back the public artifact.

Creating a remote repository, pushing, changing visibility, and publishing a release are
separate maintainer actions; this document does not authorize them.
