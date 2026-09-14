# From the 68-second demo to runnable code

[English video](https://x.com/zheng_qili666/status/2099349396895019095) ·
[Chinese video](https://www.bilibili.com/video/BV1rRYk63ER5/) ·
[中文指南](DEMO_GUIDE.zh-CN.md) · [Home](../README.en.md)

The demo connects two projects developed while building BidKing: this inference and
research repository, and the companion Grok/Codex integration. The timestamps below
refer to the edited video. Waiting was removed; its duration is not an execution benchmark.

| Time | On screen | Explore next |
| --- | --- | --- |
| 00:00 | Bidding estimates and settlement review | [Posterior example](../examples/posterior_summary.py), [joint inference](../examples/joint_posterior.py), [evidence lifecycle](EVIDENCE_LIFECYCLE.md) |
| 00:13 | Combined map, hero and date filters | [Public data page](https://api.bidkinglab.cn/public), [historical data atlas](research/HISTORICAL_DATA.zh-CN.md) |
| 00:31 | Selecting GPT and Grok in Codex | [Desktop integration and model-picker setup](https://github.com/SeasonCake/evidence-first-agent-skills/tree/main/integrations/grok-codex-bridge) |
| 00:41 | Delegated image work and tools | Persistent tasks, canonical instructions and finite completion return in the companion integration |
| 00:53 | Generated image | A separately installed native Imagine wrapper; it is not distributed with the desktop integration |
| 00:59 | Running public code and project links | [Quick start](../README.en.md#quick-start), [research guide](research/README.md), [contribution ideas](research/CONTRIBUTION_IDEAS.zh-CN.md) |

## Run one example first

From the repository root with Python 3.10+:

```powershell
python -m pip install .
auction-inference examples/synthetic_session.json
python examples/posterior_summary.py
python examples/calibration_and_sensitivity.py
python scripts/verify.py
```

These examples use synthetic inputs and need no game, product account or captured traffic.
See [contribution guidance](../CONTRIBUTING.md) to turn a small counterexample into a useful
Issue or PR without running the entire historical desktop application.

## Reading the public data

On the [public page](https://api.bidkinglab.cn/public), select the mode and map, then add
a hero and date range. Counts and summaries describe that selection and data snapshot;
the 283 sessions shown in the video are not a fixed result. Aggregated session data is
not a complete step-by-step replay or an accuracy evaluation of bidding advice.

The hosted statistics page and current desktop application are separate product entry
points. This repository supplies the domain-neutral library, labeled historical source,
frozen tables and reproducible research, rather than the complete current client/server.
See [project relationship](../PROJECT_RELATIONSHIP.md) and
[source scope](../OPEN_SOURCE_BOUNDARY.md). Neither the native image wrapper nor the
current product is installed by this repository's `pip install .` command.

The two public repositories are independently reusable: inference and research here,
engineering workflows and Grok/Codex integration in the companion repository.
