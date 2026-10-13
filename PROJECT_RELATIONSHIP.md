# Project relationship

`bidking-inference` is the public code and research repository for BidKing.
The stable `auction_inference` package remains v0.1.0. The separate `product/`
layer contains the near-complete author-owned modern application: engine, client/UI,
capture/state, server/authority, native source and build/test tools.

The product export starts from the 0.3.6 source line and includes documented public-use
adaptations. It is not the hosted service, its customer database or its private Git
history. Original Python packaging metadata, the source snapshot and the Windows
product version are separate. Building the complete application requires external inputs.

`legacy/` preserves reviewed v0.2.0-hotfix1 source and pre-0.2.8 tables.
`research/` preserves selected historical adaptations, synthetic models and approved
historical aggregates. Their original pins are unchanged; they are not current game data.

See [product source](product/README.md), [scope](OPEN_SOURCE_BOUNDARY.md),
[research](docs/research/README.md) and [development history](docs/DEVELOPMENT_STATUS.md).

Reusable agent workflows are maintained separately in
[evidence-first-agent-skills](https://github.com/SeasonCake/evidence-first-agent-skills).
Its Grok/Codex integration and workflow skills are not part of the BidKing runtime.
The existing [demo guide](docs/DEMO_GUIDE.md) remains a historical video-to-code map.
