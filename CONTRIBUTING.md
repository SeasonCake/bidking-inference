# Contributing

1. Keep changes domain-neutral and fixture-driven.
2. Add a positive and a falsifying negative test for behavioral changes.
3. Run `python scripts/verify.py`.
4. Do not add private paths, real captures, proprietary assets, secrets, or product code.

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
