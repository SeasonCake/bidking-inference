# BidKing 0.3.5: development progress

[简体中文](DEVELOPMENT_STATUS.zh-CN.md) · [Back to the overview](../README.en.md)

Recorded on 2026-09-06. This is a development snapshot, not release notes or a downloadable candidate.

The 0.3.5 goal is a more consistent experience across estimates, personal bidding preferences,
state recovery and everyday navigation. Source changes and verification are still in progress.
The available Windows application remains
[0.3.4-hotfix1](https://github.com/SeasonCake/bidking-inference/releases/tag/product-v0.3.4-hotfix1).
The public Python inference package retains its separate `v0.1.0` version line.

## Work in progress

| Area | Intended benefit | Progress and remaining work at this snapshot |
| --- | --- | --- |
| Joint constraints and calculation correctness | Combine revealed facts while keeping candidate and estimate semantics consistent | A focused batch of source checks and independent review is complete; this is not a claim of complete hero coverage or price calibration |
| Basic bidding preferences | Save personal presets and adjust three references while retaining original estimates | Source implementation exists; settlement-time association is still under revision and verification, with real UI checks pending |
| Activation recovery and device-change guidance | Distinguish local recovery stages and communicate current state more accurately | Source and logic review have progressed; complete user flows, visible UI and service deployment remain to be verified |
| Refresh and startup | Reduce unnecessary waiting and keep results associated with the current session | One scheduling correction has passed focused checks; full-refresh occupancy, startup performance and real end-to-end latency are not closed |
| Help and navigation | Make help, store entry points and explanatory copy easier to find and consistent | Source and assembly copy are synchronized; real UI and final-artifact verification remain |

A scoped source pass is not a completed product release. Candidate builds, visible interfaces,
packaging, necessary service changes and published-artifact readback need their own evidence.
No 0.3.5 download, release date or unmeasured performance improvement is promised here.

## What you can reuse now

- The [public inference API](PUBLIC_API.md) and [synthetic examples](../examples), independent
  of the private application and game environment.
- The [evidence-lifecycle case study](EVIDENCE_LIFECYCLE.md): distinguish exact, partial and
  missing observations and qualify evidence by session and revision.
- The companion [browser workflow](https://github.com/SeasonCake/evidence-first-agent-skills/tree/main/skills/browser-workflow):
  edit by business key, inspect the draft, and reconcile uncertain saves before retrying.
- The companion [intent checkpoint](https://github.com/SeasonCake/evidence-first-agent-skills/tree/main/skills/intent-checkpoint):
  resolve consequential choices while continuing clearly selected work.

This public update contains progress documentation and reusable workflow abstractions,
not the in-development client, server or current product data. See the
[project relationship](../PROJECT_RELATIONSHIP.md) and [open-source boundary](../OPEN_SOURCE_BOUNDARY.md).
