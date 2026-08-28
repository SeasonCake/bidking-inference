# BidKing Inference（公开候选）

这是一个中性、轻量的 Python 推断库，用区间观测约束离散假设，并对保留下来的假设生成后验与
确定性 Monte Carlo 汇总。运行时只使用标准库，示例数据全部为本仓新造的 synthetic fixture。

它不是 BidKing 私有产品的镜像，不含游戏表、抓包、客户端、生产服务、私有校准或真实用户数据。

```powershell
python -m auction_inference.cli examples/synthetic_session.json
python scripts/verify.py
```

仓名、copyright 主体与最终许可证仍待作者冻结，见 `LICENSE-DECISION.md`。
