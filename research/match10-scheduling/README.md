# 历史限频器与一次性调度器

两份自有历史 C# 工具及独立合成测试。这里不是游戏插件，也不实现游戏功能、注入、授权或跨进程协议。
来源见仓库的 [PROVENANCE.json](../../docs/research/PROVENANCE.json)：
`match10-readiness-limiter`、`match10-managed-scheduler`、`match10-harnesses`。
研究背景见 [Match10 教训](../../docs/research/MATCH10_LESSONS.zh-CN.md)。

## Windows 上运行

从仓库根目录执行；只需 Windows PowerShell 5.1 或 PowerShell 7，以及已安装的 .NET Framework C# 编译器。
脚本不安装 SDK、不改执行策略或全局环境，也不需要单独的游戏/网络/用户数据。

```powershell
powershell -NoProfile -File .\research\match10-scheduling\run.ps1
powershell -NoProfile -File .\research\match10-scheduling\run.ps1 -Scenario limiter
powershell -NoProfile -File .\research\match10-scheduling\run.ps1 -Scenario scheduler
```

默认编译器是 `%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe`。
如果此文件不存在，脚本报输入错误；可显式指定兼容的已安装 `csc.exe`，不会自动下载安装：

```powershell
.\research\match10-scheduling\run.ps1 `
  -Compiler "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" `
  -OutputDirectory .\outputs\match10-scheduling
```

默认输出为仓库根的 `outputs/match10-scheduling/run-<时间>-<唯一后缀>/`，包含生成的exe、两阶段stdout/stderr及`RUN.json`。
默认路径由脚本位置计算，不依赖调用者当前目录。每次新建运行子目录，不覆盖前一次证据；研究源码目录内的输出路径会被拒绝。
不要把生成的exe或日志提交为源码。本例没有SDK项目文件；本次没有验证`dotnet build`。

编译参数包含C# 5、AnyCPU、优化、关闭PDB和UTF-8输出；源码、编译器路径/版本、实际参数和返回码保存在本机回执中。
正常输出末尾应为：

```text
SUMMARY passed=18 failed=0 assertions=120 known_limits=1
PHASE run exit=0 timed_out=False exited=True
```

这里的18是命名案例、120是断言执行次数，不是18个产品场景；`known_limits=1`是下面的终端时钟行为记录，**不是严格限频保证通过**。

## 三份源码各做什么

| 文件 | 合同 |
| --- | --- |
| `ReadinessTraceLimiter.cs` | 单调用方输入非负elapsed时间和非空状态；有效尝试均计数，状态变化本身不绕开最小输出间隔。构造参数是调用者提供的间隔，没有产品状态名。 |
| `ManagedInstallScheduler.cs` | 单个生命周期只允许启动一次；按序执行回调，回调返回false结束；取消只发出取消请求，`StopAndDrain`返回是否真正排空；运行中直接Dispose会拒绝。 |
| `Harness.cs` | 合并两份历史harness的虚拟时钟和线程检查，并增加参数/异常/输出失败控制；只使用人工回调、事件和短等待。 |

适配后的工具方法逻辑保留。公开包装移除产品namespace；为旧编译器把`nameof`改为同值字符串，并显式选择`Task.Run(Func<Task>)`重载。
合成状态名不对应任何产品协议。历史120,000毫秒的限频案例是**虚拟时钟遍历**，不是实际运行两分钟的稳定性测试。

## 边界与已知行为

- limiter没有锁，不自动线程安全；由一个调用方提供一致的elapsed时钟。时钟回拨不重置原deadline。
- 历史实现把加法溢出的下一deadline饱和到`long.MaxValue`。到达这个终端值后，重复的同值观察仍可能再次输出。
  测试明确保留并记录这个边界；不要把它用于声称任意整数输入都严格限频。日常例子使用有限、远离溢出的单调时间范围。
- scheduler是一次性对象；停止、完成或Dispose后不会重新启动。取消不能强制打断一个阻塞的`step`，因此回调自己也应有合理上限。
- 排空超时返回false，不是“已经关闭”；不要立即释放仍可能被worker使用的资源。即使排空成功，也只证明这个managed worker完成，不证明外部运行时/插件/业务成功。
- fault回调自身抛异常会被历史实现吞掉，测试保留此语义；这不是替调用者设计完整错误上报/恢复系统。

## 返回码与故障正控

每个外部进程都隐藏启动。编译和运行默认各有20秒deadline，超时终止该直接子进程并确认退出；输出收集和终止确认各有有限等待。
默认csc和本harness不创建后代进程；此脚本不是通用进程树监督器，不应把`-Compiler`替换成会产生未管理后代的任意构建工具。

| 结果 | 返回码/判读 |
| --- | --- |
| 正常全部案例通过 | 0 |
| harness断言失败 | 1；看`CASE ... FAIL`和SUMMARY |
| 输入或runner准备失败 | 2 |
| 编译器失败 | 原样传播编译器返回码，run保持未执行；不是harness测试失败 |
| 直接子进程超时 | 124，`timed_out=true`且记录实际退出状态 |

以下是**应当非零**的控制，不要当正常测试失败修掉：

```powershell
.\research\match10-scheduling\run.ps1 -Scenario failure-control
# 预期 exit 1：故意失败的断言不能被外层脚本吞掉。

.\research\match10-scheduling\run.ps1 -Scenario timeout-control -RunTimeoutSeconds 1
# 预期 exit 124：harness尝试有限的5秒等待，应在1秒deadline被终止并回读退出。
```

若用默认20秒去跑`timeout-control`，它在5秒后正常结束并返回3，表示没有触发预期的父级deadline；不是正常测试集。

有价值的Issue：最小elapsed/状态序列、重复启动/关闭的事件顺序、阻塞回调排空结果、平台/编译器版本差异。
请给源码版本、命令、合成输入、期望与实际固定输出，不需要游戏日志、账号资料或完整系统诊断。

## English summary

This Windows-only reference lab adapts two author-owned historical C# helpers without
shipping a game plugin or authorization protocol. Use the installed .NET Framework
compiler through `run.ps1`; it creates a fresh output directory outside the source tree,
runs hidden processes with deadlines, and propagates compiler/harness failures.

The limiter is single-caller and retains a documented terminal-counter saturation
limitation. The scheduler is one-shot: cancellation is a request, a drain timeout is not
successful shutdown, and managed completion is not business success. The normal run has
18 named cases and 120 assertions; one case characterizes an inherited limit. Failure and
timeout controls intentionally return nonzero. No SDK build, real-game integration,
cross-process receipt or all-platform guarantee is claimed.
