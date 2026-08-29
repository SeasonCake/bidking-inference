# BidKing Inference

这是一个领域中立的离散推断库，适合用不完整、近似或类别观测，对有限候选状态进行约束、评分和
后验归一化。中等开放核心包括：

- 严格候选、标量属性与 fail-closed 输入解析；
- 精确、区间、近似及类别证据；
- 多字段加权、log-space 稳定后验；
- 有上限的联合状态枚举、熵/有效样本量/可信集合诊断；
- 有放回或无放回的确定性加权池模拟；
- 通用记录适配器、JSON CLI、合成示例及可安装 Python 包。

运行时只使用标准库，示例数据全部为本仓新造的 synthetic fixture。

它不是 BidKing 私有产品的镜像，不含游戏表、抓包、客户端、生产服务、私有校准或真实用户数据。

## 配套 skills

配套仓库 [`evidence-first-agent-skills`](https://github.com/SeasonCake/evidence-first-agent-skills)
公开了从 BidKing 私有项目实践中抽象出的通用流程：证据分级、发布验证、CLI 合同、UI 验收、
交接恢复与 fresh-clone 真相核验。它不包含私有源码、事故原始记录、客户数据或生产拓扑；边界见
`PROJECT_RELATIONSHIP.md`。

```powershell
python scripts/run_example.py examples/synthetic_session.json
python scripts/run_example.py examples/synthetic_multidimensional.json
python scripts/verify.py
```

完成 editable/wheel 安装后，也可使用 `auction-inference examples/synthetic_session.json`。

## 安装、示例与维护

```powershell
python -m pip install .
auction-inference examples/synthetic_session.json
python examples/constraint_filter.py
python examples/posterior_summary.py
python examples/monte_carlo_summary.py
python examples/joint_posterior.py
python examples/pool_simulation.py
python examples/record_adapter.py
```

公开 API 和严格输入 schema 分别见 `docs/PUBLIC_API.md`、`docs/INPUT_SCHEMA.md`；贡献、版本发布、
支持和变更记录见 `CONTRIBUTING.md`、`MAINTAINING.md`、`SUPPORT.md` 与 `CHANGELOG.md`。所有公开
问题复现都应使用可分享的合成输入。

公开核心足以让使用者接入自己的可分享候选数据，构建小型离散推断工具；它不提供 BidKing 的
字段映射、校准表、业务阈值、产品策略或运行时集成。

Copyright (c) 2026 SeasonCake，以 MIT License 发布。贡献采用 Developer Certificate of
Origin 1.1（提交时使用 `git commit -s`），不要求 CLA。
