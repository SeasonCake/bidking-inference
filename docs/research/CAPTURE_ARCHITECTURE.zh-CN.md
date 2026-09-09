# WSS → C++ 薄层：比较成本转移，不比较语言口号

状态：**研究协议，C++ producer/IPC 基准尚未实现**。
仓库中可运行的 [TCP重组工具](PYTHON_TOOLS.zh-CN.md)不是TLS解密器、游戏适配器或本章基准。

## 问题从哪来

如果每次版本变动，都要修复“取得数据 → 解码 → 映射字段 → 交给推断”的多个边界，
把一段工作前移到语义更清楚的位置可能值得。但“换成C++”不是一个完整方案：
观察点、对象寿命、线程和输出合同不明确时，复杂度只会被搬到另一边。

WebSocket定义握手与消息帧，WSS再使用TLS；应用payload的语义仍由应用决定。
因此C++读取了序列化消息，也不意味着可以省去应用解析。
这是协议层次的区别，不是某个游戏的具体实现证明。
来源：[RFC 6455](https://www.rfc-editor.org/rfc/rfc6455.html)。

## 四个可以实际比较的边界

| 观察点 | 可能减少什么 | 新增/保留什么 | 合成实验中的对应臂 |
| --- | --- | --- | --- |
| 编码消息交付之后 | 保留成熟消息入口，少改上游 | 解码、schema变更、重连、重复 | A：编码消息producer |
| C++中接收同一编码消息 | 一部分跨语言复制 | 仍需解析；异常/内存管理 | B：C++编码消息reader |
| 自有host的typed callback | 下游对wire字段的依赖 | ABI、对象寿命、重入、线程退出 | C：语义producer |
| 独立sidecar输出统一事件 | 下游生命周期与依赖耦合 | IPC排队、背压、丢弃、重启恢复 | D：统一consumer |

这些都是实验设计，尚无吞吐/延迟结果；不承诺B/C/D一定更快。

## 共同事件合同 v0（提案）

每个臂输出相同的字段：`schema_version`、`producer_id`、`epoch`、
`sequence`、`event_kind`、`observed_at_monotonic_ns`、`facts`。
`facts`只使用虚构仓库的计数/格数/质量；缺失明确为缺失，不能自动变成0。
带来源的同一事实可以重复到达，但consumer不能据此重复增加物品数。

```text
encoded/typed producer
  -> normalize schema + preserve source/epoch
  -> bounded queue (declared overflow policy)
  -> same consumer
  -> accepted / duplicate / stale / missing / rejected counters
```

计时点至少包括生成、进入队列、离开队列、consumer确认；各进程时钟需要明确如何对齐。
不能拿A的端到端耗时与B的纯解析耗时直接比较。

## 最小实验与可推翻条件

1. 固定一组人工事件，包含正常、重复、乱序、缺字段、跨epoch晚到与停止信号。
2. 先比较每条规范化输出与拒绝原因；A/B输出不等时，不进入“性能优化”结论。
3. 分别运行冷启动与暖态，并记录工具链、机器、构建配置和输入哈希。
4. 报告事件数、丢弃/重复/过期数、吞吐、p50/p95/p99、CPU/RSS、分配/复制计数。
5. 追加断开/重连、队列满、消费者慢、callback重入和退出中到达事件。
6. 修改一个schema版本，记录适配文件/逻辑变化、测试和实际工时，而不只看每秒事件数。

任一臂静默丢事件、改变缺失语义、复用上一epoch结果，便推翻“行为等价”。
没有同机同输入基准时，性能一栏保持“未测”。下一步贡献可选A/C两臂的自有host；
真实游戏入口、第三方上游与许可必须另定对象，本页没有附当前地址、协议或接入闭环。
