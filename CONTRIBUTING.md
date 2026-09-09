# Contributing

1. Keep changes domain-neutral and fixture-driven.
2. Add a positive and a falsifying negative test for behavioral changes.
3. Run `python scripts/verify.py`.
4. Do not add private paths, real captures, proprietary assets, secrets, or unselected product code.

Research has its own [entry points and contribution ideas](docs/research/CONTRIBUTION_IDEAS.zh-CN.md).
Keep historical adaptations, new synthetic examples and historical aggregate data
distinct. Update `docs/research/PROVENANCE.json` for reviewed research files/fixtures;
include source identity and adaptation notes for historical code. New sources require
maintainer selection; a newer filename or version is not permission to copy it.
For C# changes run the independent scheduling runner. For chart changes regenerate
the CSV/figures and inspect the image, then update its exact image allowlist.
Leave the stable API and frozen legacy bytes unchanged unless that change is separately scoped.

Useful small contributions include synthetic filesystem failure cases, refresh event
traces, staged-save outcome counterexamples and clearer input/output contracts.
See [failure workflows](docs/FAILURE_WORKFLOWS.md) for runnable starting points and
unimplemented directions. Start with one normal case and one falsifying control;
keep unsupported platform or concurrency guarantees explicit.

Contributions use Developer Certificate of Origin 1.1 sign-off; no CLA is required.
Create signed-off commits with:

```text
git commit -s
```

The sign-off certifies the contribution under the terms in `DCO`.
