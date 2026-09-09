# Match10探索：源码成立，业务结果仍可能失败

这是一段历史运行时观测实验的复盘。公开材料只保留自有的小型限频/调度工具、独立harness和重构后的教训，
不提供游戏插件、原始地址、完整加载链、私有offer/认证协议或原始用户堆栈。

[运行独立工程](../../research/match10-scheduling/README.md) ·
[来源条目](PROVENANCE.json)：`match10-readiness-limiter`、`match10-managed-scheduler`、`match10-harnesses`。

## 最终结果先说清

研究没有达到可交付的游戏业务功能，最终延期。此前部分源码、合成检查、编译、签名或启动成功都应保留，
但不能把它们累计成“功能已经突破”或“最终业务通过”。本公开工程也不重新证明那些旧运行结果。

| 证据层 | 可以说明什么 | 不能代替什么 |
| --- | --- | --- |
| 源码与合成harness | 明确输入下的限频、准入、重复启动、取消/排空与错误分支 | 真实外部运行时的线程/加载/退出顺序 |
| 编译与签名/产物身份 | 代码可由指定工具链构建；能辨认所测产物 | 依赖已正确生成和加载，或业务数据已可用 |
| 进程启动、返回码或心跳 | 某个进程/入口走到了相应阶段 | 所有子阶段已完成、观察目标已就绪 |
| 端到端业务检查 | 真正消费到了所需结果且能正常收尾 | 其他版本、机器和输入也都可用 |

## 两个真正有用的失败模式

### 1. 进程仍在，不等于内部运行时仍可调用

退出期曾暴露这样的时序：后台调度还在尝试工作，外部运行时却已进入拆卸。
“对象/运行时引用非空”或“进程还有心跳”不能证明下一次调用仍安全；最终卸载回调也未必足够早。

这里可复用的不是某个具体入口地址，而是分开思考：何时停止接受新工作、谁拥有在途任务、什么时候真正排空、
何时才可以释放资源。这个小工程只演示managed scheduler的那一段，不声称解决完整外部运行时退出问题。

### 2. 稳定身份所持有的句柄，可能阻碍依赖重生成

后续版本变化触发了interop适配文件的重新生成，而另一条身份/生命周期路径仍稳定持有该文件。
生成器需要删除或替换它，持有者却不允许；最终加载与业务阶段没有完成。

这是两个单独看似合理的组件在生命周期边界发生冲突：生成输出需要被更新，消费时又希望身份稳定。
处理方向应是明确生成阶段、使用阶段和资源所有权，而不是因为一侧“验证成功”就宣布另一侧也成功，
也不是盲目删除全部校验或把所有锁都去掉。本例没有实施新的保护、文件接管或游戏修改。

## 为什么公开这两个小工具

- 限频器能说明：不断变化的状态码不能成为每次都输出日志的借口；尝试计数和实际输出次数是不同量。
- 一次性调度器能说明：重复Start应有明确结果；取消请求与排空完成不同；Dispose不是强制终止阻塞回调。
- 合成harness允许别人构造很短的反例，而不需要下载旧游戏、提供账号或分享真实对局。

本轮保留历史方法逻辑，只做namespace与旧C#编译器兼容包装。harness的状态文本换为人工标签，原虚拟时钟与主要线程检查保留，
再补输入、异常和外层返回码/超时控制。原harness的单次断言计数与现在的命名case计数不是同一口径。

已知边界也保留：limiter不是线程安全组件，极端终端时钟饱和有重复输出行为；scheduler没有完整外部业务恢复协议。
详见运行说明。展示这些边界是为了让使用者知道能复用哪一段，不把历史代码称为通用生产框架。

## 可以提交什么样的Issue或fork实验

1. 给出最短的时间/状态序列，说明期望的输出次数与实际结果；注明是否涉及回拨或终端值。
2. 给出Start、step、cancel、drain、Dispose的事件顺序，指出哪个资源在超时后仍被使用。
3. 用合成阻塞回调或故意抛错回调说明上报/退出状态，而不是上传完整诊断目录。
4. 提议一个清晰的新合同再给正常和错误对照；若需要多调用方、重启生命周期或不同调度器，不把它们当作当前例子已支持。

共享源码、失败复盘和可复现输入各有作用。签名、编译、心跳和返回码都不是最终业务结果的替身；
这项历史研究的延期状态不会因为公开了几个有用的小类而改变。

## English summary

The historical experiment did not reach a deliverable game feature. Source checks,
compilation, signing and process startup were useful evidence, but did not establish
successful dependency generation, loading or business output.

Two lessons were decisive: a live process is not proof that an internal runtime is still
safe to call during teardown, and a stable file handle can conflict with regeneration of
an interop dependency. The published lab exposes only the small author-owned limiter,
one-shot managed scheduler and synthetic harnesses. It contains no game addresses,
private authorization messages or original user traces, and does not claim to solve the
full integration. Report small event-order counterexamples and retain the distinction
between a cancellation request, a completed drain and business success.
