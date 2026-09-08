# 工程案例：失败、过期结果与状态一致性

[English](FAILURE_WORKFLOWS.md) · [返回首页](../README.md)

三组可运行、完全重新编写的合成案例，来自实际维护中反复遇到的故障模式。
你可以把它们用于文档、偏好设置或预览工具，研究失败边界并提交小而明确的反例；无需游戏、账号、服务或私有数据。

## 先运行，再读合同

在仓库根目录运行：

```powershell
python examples/failure_workflows.py
python examples/failure_workflows.py --scenario atomic-replace-error
python examples/failure_workflows.py --scenario refresh-late-result
python examples/failure_workflows.py --scenario staged-local-error
python -m unittest discover -s tests -p test_failure_workflows.py -v
```

默认命令输出9个确定性JSON案例，顶层标有 `synthetic: true`。临时文件只写进该次新建的临时目录；没有网络请求。
代码位于 [failure_workflows.py](../examples/failure_workflows.py)，正反测试位于 [test_failure_workflows.py](../tests/test_failure_workflows.py)。
这些是可导入、可改造的仓库参考代码，不属于已安装 `auction_inference` 包的稳定API；只用pip安装库不会附带这些示例。

| 切片 | 数据流 | 关键不变量 | 可提交的有效问题 |
| --- | --- | --- | --- |
| 原子JSON快照 | 先编码 → 同目录临时文件 → 替换目标 | 编码失败不碰磁盘；替换失败不删旧目标，只清理本次临时文件；清理错误不掩盖原错误 | 用合成文件复现的读锁、临时文件残留、异常分类或平台差异 |
| 刷新状态 | revision＋fresh/stale → 请求句柄 → 接受/失败 | 完成被接受后才去重；失败可重试；A→B→A时旧A不能覆盖新A | 用虚拟时钟和完成顺序复现重复工作、取消或旧结果覆盖 |
| 分阶段保存 | 远端确认 → 本地提交 → 必要时恢复 | 远端、本地和恢复结果分别报告；远端未知不擅自重发 | 远端成功被误写成全部成功、恢复失败被隐藏、错误自动重试 |

## 1. 快照：保留旧值与真正的错误

`write_json_snapshot(destination, value)`先将小型JSON完整编码，再创建同目录临时文件并调用原子替换。
失败时不会递归清理目录，也不删除相邻文件。若替换和清理都失败，`SnapshotWriteError`分别保存`operation_error`与`cleanup_error`。
编码错误直接抛出，原文件和目录未触碰；不支持的值、循环结构、NaN和非法Unicode均有反例。

`replace=`是替换故障注入接缝，传入函数须遵守`os.replace`合同。没有默认重试；真实锁争用的重试策略由调用者决定。
这是“替换可见性”示例，不是断电持久性、网络文件系统、权限保全或多写者事务的保证；不自动创建父目录，也不验证应用业务schema。

维护教训：正常写入成功并不足够。应先证明旧目标、旁系文件和本次临时文件在失败时分别会怎样，再讨论恢复。

## 2. 刷新：请求过，不等于成功处理过

`RefreshGate.request(revision, stale=...)`返回新的不透明本地句柄，或在已有请求/有效结果时返回`None`。
只有`accept(ticket)`会记录当前结果已接受；`fail(ticket)`只释放当前请求以便重试。
传回的必须是该gate当前的原句柄，同值复制、另一个gate的句柄及过期结果都不能完成当前请求。

`refresh-once`演示一次fresh完成、一次stale完成，之后10次tick新增任务为0。
`refresh-late-result`演示A→B→A：前两次晚到结果均不被接受，只有最后一次A能生效。

这是单事件循环模型，不是线程安全调度器。新鲜度由调用者/虚拟时钟决定；句柄不适合序列化后跨进程恢复。
维护教训：若只用“输入已经过期”触发任务，每个tick都可能重复计算；若只比较可见文本，又可能把旧请求误当新结果。

## 3. 保存：不要用一个成功位压扁三个阶段

`save_in_stages(accept_remote, save_local, restore_local)`使用三个回调。
远端回调必须返回真正的布尔值；本地回调成功返回`None`、失败抛异常，`False`等含糊返回不是成功确认。
示例使用内存中的假文档修订，不联系服务、不实现授权系统。

```json
{"remote": "accepted", "local": "failed", "recovery": "restored", "error_type": "OSError", "recovery_error_type": null}
```

上面表示远端已接受、本地提交失败、旧本地状态已恢复；不是“服务不可用”，也不是“全部成功”。
远端响应不确定时为`unknown`，示例不自动重发；真实系统应另行设计幂等、查询和恢复策略。
它不提供跨进程崩溃恢复或完整分布式事务。

## 如何提交有价值的Issue

在[问题模板](https://github.com/SeasonCake/bidking-inference/issues/new/choose)中选择组件，并附：

- 仓库commit或库/产品版本，以及OS和Python版本（若适用）；
- 最小合成输入或事件顺序、准确命令；
- 你认为应保持的不变量、预期结果和实际结果；
- 是否可重复，是否只在某文件系统/平台出现。

例如刷新问题可只给`request A → request B → request A → complete old A`，不必提交长视频、真实对局或整个目录。
Windows成品的公开反馈只写不含敏感信息的步骤与可见现象；账号、授权信息和完整诊断走原私密支持渠道。

## 欢迎继续探索的方向

这些尚不是现成模块，不应按不存在的命令报告bug：

- 用合成结果分开网络可达、服务就绪与业务完成；
- 单值显示投影的单位、舍入和重复应用不叠乘；
- 评测数据的结果统计资格与完整重放资格。

可先提最小输入、输出合同和故意错误的正控。现有[证据生命周期案例](EVIDENCE_LIFECYCLE.zh-CN.md)已有部分资格处理，贡献前先核重复覆盖。
公开的是可复用机制和重新构造的教训；当前产品源码、真实协议/端点、核心参数、游戏表、校准样本与内部事故原文仍不披露。
