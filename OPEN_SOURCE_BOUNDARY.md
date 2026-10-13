# Open-source scope

The stable `src/auction_inference` v0.1.0 API, the modern `product/` source layer,
and the frozen `legacy/` and `research/` layers have separate lifecycles.

The near-complete author-owned product implementation is now included: inference,
desktop UI, capture/state adapters, authentication and renewal, collector/readers,
native source, build tools and tests. Current algorithms and reusable authorization
architecture are no longer excluded merely because they were private or newer than
0.2.8. See [source coverage](product/SOURCE_COVERAGE.md) and the exact
`product/SOURCE_MANIFEST.json`.

Excluded inputs remain:

- actual keys, credentials, activation tokens, encrypted production configuration and
  ready-made signing/encryption inputs;
- customer/player/device records, captured traffic, raw diagnostics, databases,
  production logs and private training/classification inputs;
- current game tables, extracted art, third-party vendor binaries, runtime/driver
  packages, generated build output and local application artwork;
- private Git history, conversations, operational records, deployment-specific
  configuration and personal contact or machine-profile details.

Self-hosting examples require an operator's own configuration or use fictional endpoints.
Source access does not authorize access to the author's hosted services or data.
The collector defaults to loopback; external binding needs explicit authentication.

A source checkout does not reproduce an official encrypted Windows package without
its required data, assets, runtime, toolchain and builder-generated keys. Missing
external inputs remain missing, rather than becoming verified through a placeholder.

Author-owned code is MIT-licensed. Third-party content retains its own rights; see
[NOTICE](NOTICE.md) and [dependencies](product/THIRD_PARTY.md). Source publication,
security assessment and production deployment are distinct.
