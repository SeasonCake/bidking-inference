# BidKing Inference

这是一个中性、轻量的 Python 推断库，用区间观测约束离散假设，并对保留下来的假设生成后验与
确定性 Monte Carlo 汇总。运行时只使用标准库，示例数据全部为本仓新造的 synthetic fixture。

它不是 BidKing 私有产品的镜像，不含游戏表、抓包、客户端、生产服务、私有校准或真实用户数据。

## 配套 skills

配套仓库 [`evidence-first-agent-skills`](https://github.com/SeasonCake/evidence-first-agent-skills)
公开了从 BidKing 私有项目实践中抽象出的通用流程：证据分级、发布验证、CLI 合同、UI 验收、
交接恢复与 fresh-clone 真相核验。它不包含私有源码、事故原始记录、客户数据或生产拓扑；边界见
`PROJECT_RELATIONSHIP.md`。

```powershell
python scripts/run_example.py examples/synthetic_session.json
python scripts/verify.py
```

完成 editable/wheel 安装后，也可使用 `auction-inference examples/synthetic_session.json`。

Copyright (c) 2026 SeasonCake，以 MIT License 发布。贡献采用 Developer Certificate of
Origin 1.1（提交时使用 `git commit -s`），不要求 CLA。
