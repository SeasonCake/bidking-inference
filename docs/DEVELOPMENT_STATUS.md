# BidKing 0.3.5: development progress

[简体中文](DEVELOPMENT_STATUS.zh-CN.md) · [Back to the overview](../README.en.md)

Recorded on 2026-09-07. This is a development snapshot, not release notes or a downloadable candidate.

The 0.3.5 goal is a more consistent experience across estimates, personal bidding preferences,
state recovery and everyday navigation. Source changes and verification are still in progress.
The available Windows application remains
[0.3.4-hotfix1](https://github.com/SeasonCake/bidking-inference/releases/tag/product-v0.3.4-hotfix1).
The public Python inference package retains its separate `v0.1.0` version line.

## Work in progress

| Area | Intended benefit | Progress and remaining work at this snapshot |
| --- | --- | --- |
| Joint constraints and calculation correctness | Combine revealed facts while keeping candidate and estimate semantics consistent | Several scoped source revisions and independent reviews have results; real-input qualification, uncovered field semantics and statistical calibration remain separate, without a claim of complete hero accuracy |
| Basic bidding preferences | Save personal presets and adjust three references while retaining original estimates | Settlement-time association and selected save, reopen and restore-default flows have been verified; real manual-input paths and the complete user journey remain in progress |
| Activation recovery and device-change guidance | Distinguish local recovery stages and communicate current state more accurately | Local recovery, commit-failure classification and related logic review have results; complete real activation, device-change journeys and necessary service rollout are not complete |
| Refresh and startup | Reduce unnecessary waiting and keep results associated with the current session | Selected real refresh paths and interface states have been checked; repeated computation triggered by stale input is undergoing further revision and verification, while startup performance and overall end-to-end gains remain unproven |
| Help and navigation | Make help, store entry points and explanatory copy easier to find and consistent | Store entry points, selected copy and local export-recovery flows have been checked; other real states and the final artifact still need verification |

A scoped source pass is not a completed product release. Candidate builds, visible interfaces,
packaging, necessary service changes and published-artifact readback need their own evidence.
No 0.3.5 download, release date or unmeasured performance improvement is promised here.

## Scope of this interim update

This update changes progress documentation and reusable maintenance guidance only, not the
public inference package, existing downloads or product release state. A complete version
will be described separately after its verification and delivery work is complete; a planned
time window is not a published release. Maintenance preserves the scope of completed checks,
later findings and missing real inputs instead of repeating closed work or overstating progress.

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
