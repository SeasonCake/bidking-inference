# BidKing legacy source and data

This directory preserves a reviewed subset of early BidKing code and historical game
metadata. It is separate from the maintained `auction_inference` package.

## Included snapshots

### `source-v0.2.0-hotfix1/`

- provenance: annotated tag `v0.2.0-hotfix1`, commit
  `5b2754f9b39d34a812b3cb6febe92a74d90b467a`;
- lifecycle: historical, read-only reference;
- 56 files / 1,589,297 bytes;
- deterministic path-and-SHA256 manifest digest:
  `4a3af572d1376e7d268e9b60b32499f3fa97f2746144e7377d1d87d548b48247`;
- contains the real early Tk overlay, reference engine, inference modules, simulation
  utilities, and their small schema helpers from the free/open-era product line.

The snapshot is exact at Git blob level, but deliberately incomplete as a runnable
application: capture, live runtime, packaging, deployment, user samples, and generated
assets are not included. It is useful for studying the early architecture and algorithms,
not as a supported client build.

### `data-v0.2.7-hotfix3/`

- provenance: the last pre-0.2.8 commit
  `acae7011a346b2e0583bf882dd9dd841ac081c4a` (source version
  `0.2.7-hotfix3`);
- lifecycle: historical, read-only reference;
- 7 files / 641,335 bytes;
- deterministic path-and-SHA256 manifest digest:
  `1cb2ff181202b370d1e0c6da5cc5667a8719be49e91b465ca66be506d5a15f99`;
- contains the frozen maps, heroes, battle items, droppable-item catalog, old drop-map
  mapping, item categories, and measured quality weights used by that generation.

These tables describe an old game build and are not current authority. Names, text, IDs,
and other underlying game metadata may belong to their respective rights holders; see
the repository [`NOTICE.md`](../NOTICE.md).

## Deliberately excluded

- from this frozen snapshot: all `0.2.8+` product source, newer map adaptation, current calibration, and current
  decision policy;
- activation, upload, private/server code, production locators, packaging and protection;
- raw captures, player or customer data, diagnostics, sample manifests, and incident
  records;
- third-party binaries, extracted images, models, audio, or other game assets.

The maintained public API lives in [`src/auction_inference`](../src/auction_inference).
Legacy files may contain obsolete assumptions and are not covered by Semantic Versioning.

Separately selected later helper adaptations are now in [research](../docs/research/README.md),
with their own provenance. They do not alter these frozen snapshots or turn this
historical application subset into a current runnable client.
