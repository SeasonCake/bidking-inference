# BidKing: 0.3.5-hotfix1 product snapshot

[简体中文](DEVELOPMENT_STATUS.zh-CN.md) · [Overview](../README.en.md)

Recorded 2026-09-13. The Windows product and public Python toolkit have separate version lines;
the toolkit remains **v0.1.0**. This source/documentation update does not create a new GitHub
product Release or replace existing assets.

## Selected hotfix work

0.3.5-hotfix1 completed its selected local artifact checks and representative real-use journey;
the maintainer also reported successful use. Changes cover the b049 game update, additional
map estimates and quality/count/cell details, empirical wreck estimates, startup-instance and
shutdown ownership, and selected estimate/help text corrections. The representative journey
linked its screen, same-session export and normal exit. It is not all-map or all-machine coverage.

Server and update-notification configuration were unchanged in this round. Previously deferred
account/server work, additional observation types and unselected features are not implicitly
shipped by the client hotfix. No product-wide accuracy or speedup percentage is claimed.

## Downloads and verification

- The existing [GitHub 0.3.5 product release](https://github.com/SeasonCake/bidking-inference/releases/tag/product-v0.3.5)
  and older releases remain available. That original ZIP previously passed full download/hash readback.
- The maintainer provides a [0.3.5-hotfix1 download](https://bidking-dist-1317950063.cos.ap-shanghai.myqcloud.com/bidking-live-v0.3.5-hotfix1-encrypted.zip).
  The locally verified package is 95,883,037 bytes with SHA-256
  `a3f0553e40f24ba2cccc85860964a6ee6359ebf810a6487fb4a2f7117aa8829f`.
- An independent complete download on September 13 returned 200, received all 95,883,037
  bytes, and matched the locally verified SHA above. This verifies the new object itself;
  no new GitHub product release or asset upload was performed.

## Runnable research added here

- [Posterior, evaluation and lifecycle examples](research/POSTERIOR_AND_LIFECYCLE.zh-CN.md):
  full marginals versus truncated tails, train/holdout, aggregate cancellation, per-case errors,
  stop/finished/reaped ownership and manifest-member closure.
- [Own-host Windows lab](research/NATIVE_LAB.zh-CN.md): build/instance identity, ABI, PDB,
  MVID, a local symbolized stack, cold and warm encoded/typed pipe experiments, slow consumers
  and epoch-aware reconnection.
- Existing [evidence qualification](EVIDENCE_LIFECYCLE.md) and [failure workflows](FAILURE_WORKFLOWS.md)
  remain useful starting points.

All new inputs are synthetic. The 13 stable modules, 56 legacy source files, seven historical
data files and existing images/releases retain their identities. Current product models, real
data, services, capture, activation and production implementations are excluded. See
[project relationship](../PROJECT_RELATIONSHIP.md), [scope](../OPEN_SOURCE_BOUNDARY.md), and
[research roadmap](research/ROADMAP.zh-CN.md).
