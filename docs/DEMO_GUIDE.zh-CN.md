# 从68秒演示找到可运行代码

[中文视频](https://www.bilibili.com/video/BV1rRYk63ER5/) ·
[English video](https://x.com/zheng_qili666/status/2099349396895019095) ·
[English guide](DEMO_GUIDE.md) · [返回首页](../README.md)

视频分享两个从 BidKing 开发中整理出来的项目：这里的推断与研究代码，以及配套仓库的
Grok/Codex 集成。以下是成片时间点，等待过程已剪去，不能用68秒推算实际执行速度。

| 时间 | 视频内容 | 可以继续尝试 |
| --- | --- | --- |
| 00:00 | BidKing 竞价估值与结算复盘 | [推断示例](../examples/posterior_summary.py)、[多维联合后验](../examples/joint_posterior.py)、[观测资格案例](EVIDENCE_LIFECYCLE.zh-CN.md) |
| 00:13 | 地图、英雄、日期联合筛选 | [公共数据页面](https://api.bidkinglab.cn/public)、[冻结旧数据图谱](research/HISTORICAL_DATA.zh-CN.md) |
| 00:31 | 在 Codex 中切换 GPT 与 Grok | [Grok/Codex 桌面集成](https://github.com/SeasonCake/evidence-first-agent-skills/tree/main/integrations/grok-codex-bridge)及其模型按钮设置说明 |
| 00:41 | 委派生图与工具执行 | 集成中的持久任务、规范刷新与完成回传；[工具工作流](https://github.com/SeasonCake/evidence-first-agent-skills) |
| 00:53 | Grok 生图结果 | 演示使用另外安装的原生 Imagine 包装器；它不随公开桌面集成分发 |
| 00:59 | 运行公开代码与项目入口 | [快速开始](../README.md#快速开始)、[研究路线](research/README.zh-CN.md)、[贡献题目](research/CONTRIBUTION_IDEAS.zh-CN.md) |

## 先运行一个例子

在仓库根目录，用 Python 3.10+ 安装工具箱后运行：

```powershell
python -m pip install .
auction-inference examples/synthetic_session.json
python examples/posterior_summary.py
python examples/calibration_and_sensitivity.py
python scripts/verify.py
```

这些示例使用合成输入，不需要游戏、产品账号或采集流量。进一步研究时可从
[贡献指南](../CONTRIBUTING.md)选择一个小反例，而不必先运行完整历史桌面程序。

## 公共数据如何看

打开[公共数据页面](https://api.bidkinglab.cn/public)，先选玩法和地图，再叠加英雄与日期范围，
看样本数量和统计如何变化。筛选后的数量只对应当时的数据快照；视频中的283局不是固定常量。
样本统计也不等于完整逐步对局回放，不能直接据此宣称估值准确率。

公共页面与当前桌面成品是线上/产品入口。此仓库提供领域中立推断库、明确标注的历史源码、
冻结旧表及可运行研究，未包含当前完整客户端和统计服务。源码关系见
[项目关系](../PROJECT_RELATIONSHIP.md)及[范围说明](../OPEN_SOURCE_BOUNDARY.md)。
视频中的原生生图组件和当前产品不会因为出现在演示里就成为 `pip install .` 的一部分。

两个公开仓库均可独立复用：本仓侧重推断与可复现研究，配套仓侧重工程工作流与 Grok/Codex 集成。
