# 先确认观测资格，再计算后验

[English](EVIDENCE_LIFECYCLE.md) · [可运行示例](../examples/evidence_lifecycle.py)

从 BidKing `0.3.4-hotfix1` 的维护经验提炼，演示怎样让证据完整性、版本身份和后验计算协同工作。
用一个六件物品的合成总体，一分钟复现“同样看到2件，为什么结论不同”。

## 1. 同一个数字，可能是不同证据

假设一个完全虚构的六件物品总体只有三个候选：蓝色件数为 1、2、3，先验为 1/4、1/2、1/4。

| 观测 | 应进入推断的约束 | 结果 |
| --- | --- | --- |
| 已完整统计，蓝色共2件 | exact(2)，在第一次计算前加入 | 只保留2件候选，概率1 |
| 只检查了一部分，已见2件蓝色 | interval(2, 6)，不是exact(2) | 2件/3件候选概率2/3、1/3 |
| 没有拿到蓝色计数 | 不构造该字段的约束，不补0 | 保留三个候选的原先验 |
| 完整统计为0件 | exact(0)，与本例整个候选池冲突 | 明确报错；不是正常的空后验或旧结果 |

这里的“部分观测”假定是无重复、无误检的已见件数，因此能够提供下界。带噪测量不能直接套用该假设。
上界6来自这个玩具总体的明确容量，不是经验阈值。未知数据应当继续未知，不能为了算出一个数字补齐。

## 2. 相同形状不等于相同身份

输入还带有显式 session 和 revision。示例先检查这两个字段，再构造上述约束：

- session不匹配：不用于当前运行；
- 任一revision未知：返回withheld，而非沿用“最近一次已知版本”；
- revision不匹配：返回withheld，清空当前后验输出；
- 新快照与当前session/revision匹配：重新计算，允许恢复。

本例只消费调用者提供的标识，不探测程序、不读磁盘身份、不计算哈希、不收集网络数据。
它不能证明调用者提供的标识真实或稳定。实际系统必须在自己的输入边界提供可靠身份，并让UI/下游
用新状态替换旧展示；不能只是改标签而保留旧数值。

## 3. 一分钟复现

在仓库根目录运行，Python 3.10+，无需游戏、密钥、GUI或第三方包：

```powershell
python examples/evidence_lifecycle.py
python -m unittest discover -s tests -p test_evidence_lifecycle.py -v
```

输出有`synthetic: true`，同时给出完整、部分、缺失、旧revision、未知revision和错误session六种状态。
回归还覆盖首次结果、非法布尔/小数计数、候选池矛盾、清空旧展示以及新快照恢复。
完整仓库检查仍使用`python scripts/verify.py`。

## 4. 接入自己的应用

适用于版本化目录、库存批次、实验快照等有限候选问题；先声明总体、计数含义与版本权威。
接入时用自己的候选集替换示例，并验证身份来源、重复计数和异常恢复；合成测试与真实系统验证分别记录。

相关：[公开边界](../OPEN_SOURCE_BOUNDARY.md)、
[证据审查配套案例](https://github.com/SeasonCake/evidence-first-agent-skills/blob/main/docs/HOTFIX1_CLAIM_REVIEW.zh-CN.md)。
