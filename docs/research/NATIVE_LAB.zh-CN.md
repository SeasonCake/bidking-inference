# 自有Windows host：身份、符号与事件边界

2026-09-13，本仓新增可运行的C++/C#实验。它只构建和运行自己的小程序；不连接游戏、加载第三方
插件、提权、修改ASLR或访问符号服务器。MSVC x64和.NET Framework编译器须已安装。

```powershell
python -m research.native_lab.run --repeats 100
```

默认通过Visual Studio的vswhere定位工具链，也可传`--vcvars`、`--csc`。输出进入
`outputs/native-lab/<run>/`：EXE/PDB、编译日志和`receipt.json`。这些生成文件不进入Git。
缺工具链返回input_error；执行失败保留失败收据，不当作未运行的成功。

## 本机实际跑过的控制

| 控制 | 本次结果 |
| --- | --- |
| 同一native文件运行三次 | 各自记录PID/base/VA/RVA；都满足VA=base+RVA且RVA位于该image内 |
| 改变一个编译常量 | 文件SHA与调用结果不同；把第二构建当第一构建被拒绝 |
| x64 ABI布局 | pointer=8、record=24、sequence偏移8、count偏移16；错误packed布局在调用前拒绝 |
| 同名PDB匹配 | 正确GUID/age组合通过；另一构建的同名PDB被拒绝 |
| 符号化栈 | 在受控throw之前捕获并解析出FixtureStackLeaf、FixtureStackMiddle；异常被本程序捕获 |
| 托管MVID | 同一文件重复运行相同；两个有意改变的构建不同 |
| 两条事件生产路径 | 编码/解码与typed直接交付输出相同事件；同consumer的接受/拒绝分类逐项相同 |

地址属于某次加载，MVID属于托管模块，PDB配对使用自己的GUID/age，三者不替代文件/构建身份。
本实验不要求ASLR每次都给不同base，也不声称确定性重建必然改变MVID。错误ABI采用静态布局拒绝，
没有为了演示而执行未定义调用。这里的栈是受控throw前的捕获，不是任意崩溃的完整postmortem。

## 事件对照测到了哪一段

两臂都处理同一套八事件：正常、重复、旧序号、旧epoch、显式null、缺失字段、新epoch晚到及冲突。
encoded臂额外做文本编码/解析，typed臂直接交付结构，两者再输出相同JSONL，通过stdout pipe交给
同一个Python consumer。计数器证明encoded确实执行了解码，避免两个臂实际走同一路径的假对照。

本机MSVC 14.37、Python 3.13，100组/800事件，三个冷进程试次的端到端时间如下（毫秒）：

| 臂 | 三次观测 | 中位数 |
| --- | --- | ---: |
| encoded | 33.792 / 35.040 / 35.355 | 35.040 |
| typed | 33.008 / 32.436 / 31.187 | 32.436 |

端到端包含进程启动、生成/序列化、pipe读取、JSON解析和consumer验证；收据另列producer emit、
进程墙钟与consumer p50/p95/p99，不能混为同一指标。这个小样只证明该输入的等价与测量路线可跑；
早一版控制中也出现过更慢的typed试次，不能据此发布语言速度排名或产品提速百分比。

另用持续运行的自有pipe进程，每臂100次请求、每次8事件，单独测得暖态批次往返：

| 臂 | p50 / p95 / p99（毫秒） | 首批含启动（毫秒） |
| --- | --- | ---: |
| encoded | 0.1367 / 0.1873 / 0.2169 | 19.3620 |
| typed | 0.1139 / 0.1795 / 0.2148 | 18.6982 |

慢消费者控制使64行有界读取队列实际满载，再恢复消费；两臂各8000事件完整且语义相同。停止
请求收到close确认后，还等待进程退出和reader线程结束。新进程进入epoch2后拒绝epoch1晚到事件。
这些是有限输入与单次环境的结果，不能当成长时间运行或所有重连/背压行为的保证。

未测：CPU/RSS、全链分配/复制、长期耐久、跨机性能、真实游戏接入。通用Python拒新队列及
真实线程join另见[生命周期/记录实验](POSTERIOR_AND_LIFECYCLE.zh-CN.md)。
后续扩展见[采集协议](CAPTURE_ARCHITECTURE.zh-CN.md)。

依据：[Microsoft PE格式](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format)、
[符号索引](https://learn.microsoft.com/en-us/windows/win32/api/dbghelp/nf-dbghelp-symsrvgetfileindexinfow)、
[MVID](https://learn.microsoft.com/en-us/dotnet/api/system.reflection.module.moduleversionid)。
这些说明API和字段语义，具体通过结果来自本仓程序，不能相互替代。
