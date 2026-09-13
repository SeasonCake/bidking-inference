# 从一个结果，到一致的概率、评估和退出责任

本页包含2026-09-13新增的独立合成实验。输入和参数均为教学设计，可在没有游戏、账号或私有
样本的环境复现。稳定推断库仍为v0.1.0；这些例子没有复制当前产品模型、校准或线程管理实现。

## 一份后验，三种不同的尾部

```powershell
python examples/posterior_projection.py
```

例子复用公开`Candidate`、后验归一化与精确观测接口。六个joint状态的权重是
0.35、0.25、0.20、0.10、0.06、0.04，对件数的完整边际为0.6、0.3、0.1。

| 输出对象 | 显示部分 | 正确的other |
| --- | --- | ---: |
| joint前两项 | 0.35、0.25 | 0.40 |
| 完整件数边际 | 0.60、0.30、0.10 | 0 |
| 件数边际前两项 | 0.60、0.30 | 0.10 |

把joint遗漏的0.4再加到完整边际，总质量会变成1.4；但统一删除other又会丢掉第三行真正的0.1。
主读数和详情都消费同一个已排序投影，平票采用稳定标签顺序。明确观测0时，精确约束把质量集中
到0；未观测时保留先验。缺失字段不自动补0，数字与文本标签也不能被显示转换悄悄合并。

这是显示消费者的数据合同，不声称覆盖某个真实游戏的全部状态或提供新的概率校准结果。

## 汇总误差为0，逐例仍可能很差

```powershell
python examples/regression_evaluation.py
```

两个训练预测为120、80，真实值均100；留出预测为140、60，真实值也均100。用训练集拟合的
偏移为0。留出集signed bias和总体有符号相对误差都是0，但MAE/RMSE为40、MAPE为40%。
高估与低估抵消，没有使单个预测更好。

例子分别报告train/holdout与prediction/reconstruction；留出值即使改得很极端，也不能改变
拟合偏移。真实目标为0的样本仍进入MAE，MAPE另列有效n和排除数。这里是回归误差示例，不改变
公开`calibration.py`的binary Brier/log-loss语义。结算条件下的重构结果不能冒充结算前预测。

## stop、finished和reaped各证明什么

```powershell
python -m research.lifecycle
python -m unittest tests.test_research_lifecycle
```

`Coordinator`演示lease的owner及排空；`WorkerOwner`演示真实线程的停止请求与join后确认。
barrier控制线程继续等待，证明stop已发出时`reaped`仍为false且原handle保留。释放barrier、线程
真实结束并join后才确认回收。worker报错也可以成功reap，因此回收完成与业务成功分别返回。
关闭回调在锁外运行，重复关闭不重复回调；错误owner、伪造同字段lease及停止后提交有反例。

这是本进程自有线程实验，不是终止任意外部进程的工具，也没有普适“两秒退出”的保证。

## 记录、成员与证据关联

```powershell
python -m research.records
python -m unittest tests.test_research_records tests.test_research_manifest
```

- `records.compare`区分absent/null/0/false与字符串；tuple路径避免`a.b`与嵌套`a → b`碰撞。
- `EpochConsumer`由owner选择epoch；晚到事件不能自行激活新epoch。重复、冲突、旧序号分别返回，
  新snapshot缺失的字段清掉旧值。单线程有界队列采用明确的reject-new溢出策略。
- `records.manifest`用虚构3→4成员解释copy/transform/readback闭合。旧pin、漏新成员、数量相同但
  成员不同，以及变换后字节错误均会被发现；不是大型打包或保护系统。

截图、导出和结算关联继续复用[已有证据资格例子](../EVIDENCE_LIFECYCLE.zh-CN.md)。先比较实例、
revision、phase与具体字段；相同脱敏占位符不构成唯一身份。展示记录时间与捕获时间不同，
结算后的record可保留先前预测，但不能反向充当当时可见输入。

Launcher退出与worker退出也不同。下一页的自有native程序记录每次PID与加载地址、同文件SHA，
把运行实例与构建身份分开：[身份和事件实验](NATIVE_LAB.zh-CN.md)。
