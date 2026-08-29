<p align="right"><strong>简体中文</strong> · <a href="README.en.md">English</a></p>

# BidKing Inference

[![CI](https://github.com/SeasonCake/bidking-inference/actions/workflows/ci.yml/badge.svg)](https://github.com/SeasonCake/bidking-inference/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

一个从 BidKing 实际产品研发中抽象出来的、领域中立的离散推断 Python 库。它适合用不完整、近似或
类别观测，对有限候选状态进行约束、评分、后验归一化和确定性模拟。

> **版本说明：** BidKing 私有产品当前版本线为 `0.3.4`；本仓是独立维护的公开推断核心，当前公开
> Release 为 [`v0.1.0`](https://github.com/SeasonCake/bidking-inference/releases/tag/v0.1.0)。两者不是同一个
> 可执行产品，也不共用版本语义。

## 界面预览

下面是 BidKing 私有产品研发过程中的历史界面，用于说明本公开推断核心所来自的真实应用背景。
本仓不包含截图中的私有客户端、游戏适配、校准数据或运行时。

<p align="center">
  <img src="docs/assets/screenshots/bidking-ui-compact-dark-historical.png"
       alt="BidKing 紧凑深色界面历史截图" width="428">
</p>

<p align="center"><em>紧凑深色布局历史截图（图中为早期开发版本）。</em></p>

<p align="center">
  <img src="docs/assets/screenshots/bidking-live-gameplay-historical.png"
       alt="BidKing 实机联动与地图视图历史截图" width="1100">
</p>

<p align="center"><em>实机联动、地图视图与结算推断的历史开发截图；后续将补充 0.3.4 演示视频和新截图。</em></p>

截图中的第三方游戏画面、名称、商标与素材不属于本仓 MIT 许可，详见
[`NOTICE.md`](NOTICE.md) 与[截图说明](docs/assets/screenshots/README.md)。

## 公开核心包含什么

- 严格候选、标量属性与 fail-closed 输入解析；
- 精确、区间、近似及类别证据；
- 多字段加权、log-space 稳定后验；
- 有上限的联合状态枚举、熵/有效样本量/可信集合诊断；
- 有放回或无放回的确定性加权池模拟；
- 通用记录适配器、JSON CLI、合成示例及可安装 Python 包。

它不是 BidKing 私有产品的镜像，不含游戏表、抓包、客户端、生产服务、私有校准或真实用户数据。
公开核心足以让使用者接入自己的可分享数据，构建小型离散推断工具；完整边界见
[`OPEN_SOURCE_BOUNDARY.md`](OPEN_SOURCE_BOUNDARY.md)。

## 快速开始

Python 3.10 或更新版本即可；运行时只使用标准库。

```powershell
python scripts/run_example.py examples/synthetic_session.json
python scripts/run_example.py examples/synthetic_multidimensional.json
python scripts/verify.py
```

安装后也可以使用 CLI：

```powershell
python -m pip install .
auction-inference examples/synthetic_session.json
```

## 示例与文档

```powershell
python examples/constraint_filter.py
python examples/posterior_summary.py
python examples/monte_carlo_summary.py
python examples/joint_posterior.py
python examples/pool_simulation.py
python examples/record_adapter.py
```

- [公开 API](docs/PUBLIC_API.md)
- [严格输入 schema](docs/INPUT_SCHEMA.md)
- [项目关系与边界](PROJECT_RELATIONSHIP.md)
- [贡献指南](CONTRIBUTING.md)
- [维护与支持](MAINTAINING.md)

## 配套 skills

配套仓库 [`evidence-first-agent-skills`](https://github.com/SeasonCake/evidence-first-agent-skills)
公开了从 BidKing/LC2 工程实践中抽象出的通用流程：架构调查、结论验证、CLI 合同和 agent 兼容性。
它同样不包含私有源码、客户数据、事故原文或生产拓扑。

## 许可证与贡献

Copyright (c) 2026 SeasonCake，以 MIT License 发布。贡献采用 Developer Certificate of Origin 1.1
（提交时使用 `git commit -s`），不要求 CLA。
