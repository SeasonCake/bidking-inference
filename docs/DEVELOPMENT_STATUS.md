# BidKing 0.3.5: released work and what remains

[简体中文](DEVELOPMENT_STATUS.zh-CN.md) · [Back to the overview](../README.en.md)

Recorded on 2026-09-08. This updates the September 7 development snapshot, retained in Git history.

[Windows 0.3.5 is available](https://bidking-dist-1317950063.cos.ap-shanghai.myqcloud.com/bidking-live-v0.3.5-encrypted.zip),
and the update notification has switched to this version. The public Python toolkit keeps
its separate `v0.1.0` version: it is not a mirror of the current client source.
[The older 0.3.4-hotfix1 download](https://github.com/SeasonCake/bidking-inference/releases/tag/product-v0.3.4-hotfix1) remains available.

## What ships in this version

- Bidding preferences: independent ratios for the three references, a shared quote cap,
  named presets and defaults; original estimates are retained and repeated application does not compound ratios.
- Joint constraints: improved handling of Viktor's three mean observations and combined size/mean-value constraints.
- Calculation refresh: improved budgets, cancellation and result association; usable results
  survive timeout/cancellation without stale results replacing the current state.
- Startup and recovery: clearer startup feedback, failure cleanup, directory-permission
  restoration and guidance when activation results disagree with local state.
- Everyday navigation: revised help/store entry points and copy; the server records the
  last tool quote and configuration effective before settlement.

The selected client delivery, required service update, real-match upload checks, complete
download-object readback and update-notification switch are complete. Announcement publication
was confirmed by the author. This documentation round additionally checked HTTP 200 and the
95,466,681-byte download length on September 8; that lightweight check is not a fresh full
download or another packaged runtime test, and the limited real tests do not prove every environment.

## Remaining work and limits

- Existing activation and device-change rules remain in effect. The eight-change limit
  and related server compatibility/recovery work were deferred to hotfix1 or hotfix2.
- Statistical calibration tuning was deferred to 0.3.5-hotfix1. New type-level mean, count
  and presence evidence still require a later selected version and real inputs.
- The diagnostic BAT convenience entry retains a known issue for next-version debugging.
  Normal startup and in-app export are usable; not every diagnostic entry is claimed to pass.
- Complete real-hero coverage, deep-cache behavior, internet-cafe conditions and overall
  performance gains remain unproven. No accuracy or speedup percentage is claimed.

Post-release work now covers test entry points, coverage and code maintenance. It does not
rebuild 0.3.5 or automatically start deferred features. Further product changes and dates
depend on their selected scope, verification and delivery.

## Scope of this public maintenance round

This update describes the version and links an existing download. It does not change the
public package API/version, frozen legacy source/data or existing GitHub release assets.
Current product source, server implementation and runtime data stay outside this repository.
Completed, deferred and unknown work remain distinct; a source pass is not delivery evidence.

## What you can reuse now

- The [public inference API](PUBLIC_API.md) and [synthetic examples](../examples), independent
  of the private application and game environment.
- The [evidence-lifecycle case study](EVIDENCE_LIFECYCLE.md): distinguish exact, partial and
  missing observations and qualify evidence by session and revision.
- The companion [browser workflow](https://github.com/SeasonCake/evidence-first-agent-skills/tree/main/skills/browser-workflow):
  edit by business key, inspect the draft, and reconcile uncertain saves before retrying.
- The companion [intent checkpoint](https://github.com/SeasonCake/evidence-first-agent-skills/tree/main/skills/intent-checkpoint):
  resolve consequential choices while continuing clearly selected work.
- The companion [interim-maintenance guide](https://github.com/SeasonCake/evidence-first-agent-skills/blob/main/docs/INTERIM_MAINTENANCE.md):
  organize periodic reviews around dates, evidence scope, remaining work and next actions
  without presenting development progress as a completed release.

This public update contains progress documentation and reusable workflow abstractions,
not the in-development client, server or current product data. See the
[project relationship](../PROJECT_RELATIONSHIP.md) and [open-source boundary](../OPEN_SOURCE_BOUNDARY.md).
