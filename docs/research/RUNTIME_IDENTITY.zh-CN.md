# 地址正确，为什么仍然不能调用？

状态：**概念与实验设计**；本页尚无完整ABI/PDB/ASLR可运行实验。
已有的 [C#调度测试](MATCH10_LESSONS.zh-CN.md)只验证自有回调，不验证本页所有底层结论。

## 六个名词回答六个不同问题

| 概念 | 回答的问题 | 最常见的错推 |
| --- | --- | --- |
| 模块文件哈希 / MVID | 正在研究哪个文件、哪个托管模块版本？ | 名字一样或MVID看起来合理＝可信/获授权 |
| RVA / VA | 偏移与该次加载地址如何关联？ | 磁盘偏移＝RVA；旧构建RVA可套到新构建 |
| ASLR | 加载位置为什么不应写死？ | 每次启动基址一定变化；随机化＝完整性证明 |
| ABI | 参数、返回值、布局与栈/展开约定怎样对接？ | 函数地址正确＝可以随意强转调用 |
| PDB | 地址/栈如何回到匹配构建的函数和源码？ | 随便同名符号文件就能解释当前二进制 |
| stack trace | 此刻能恢复哪些调用帧？ | 看到了栈顶＝已经证明根因 |

MVID是区分托管模块版本的UUID，不是数字签名；它是身份线索，不承担信任裁决。
来源：[ModuleVersionId](https://learn.microsoft.com/en-us/dotnet/api/system.reflection.module.moduleversionid?view=net-10.0)。

在映像已加载、地址属于该模块的前提下，`VA = loaded_base + RVA`；
PE磁盘布局与加载布局不同，file offset不能不经节映射直接代入。
来源：[PE格式](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format)。

ASLR允许映像在加载时重定位，实验应保持默认设置，并记录实际base，不用关闭ASLR制造稳定地址。
来源：[DYNAMICBASE](https://learn.microsoft.com/en-us/cpp/build/reference/dynamicbase-use-address-space-layout-randomization?view=msvc-170)。

## Lab A提案：把身份和地址拆开记录

输入是两份自己编写、仅改变一个常量的托管程序，另配自有原生host。
结果表记录源commit、编译器/选项、文件SHA-256、MVID（托管）、loaded base、
选定符号与RVA；每次运行单独一行。

先检查“同一文件重复运行”和“两个不同构建”，不要把两者混为一组。
负例为把A的符号/RVA解释套到B：应报告身份不匹配或无法解释，而不是强行调用。
两次base相同不构成ASLR失效证据，也不应要求每次MVID都变化——确定性构建可能复现同一身份。

## Lab B提案：符号和调用合同

只在自有测试程序中设置受控异常，比较原始地址栈、匹配符号栈与不匹配符号的拒绝/未解析结果。
调试器要求符号匹配对应构建，文件名相同不够。
来源：[PDB与源码映射](https://learn.microsoft.com/en-us/visualstudio/debugger/specify-symbol-dot-pdb-and-source-files-in-the-visual-studio-debugger?view=visualstudio)。

ABI正例使用明确导出的C边界、固定宽度字段、约定布局和所有权；
负例先用静态尺寸/偏移/签名检查拒绝不匹配，不要求执行未定义行为来“演示崩溃”。
Windows x64的整数/指针与浮点参数按约定使用不同寄存器，另有栈和保存责任；
正确地址无法弥补错误签名。
来源：[Microsoft x64 calling conventions](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/x64-architecture)。

## 和 Match10 历史怎么连接

历史探索最终延期，不能改写为“用PDB突破完成”。
调度器排空、线程归属、对象句柄生命周期、interop再生成、原生调用与游戏业务结果是不同层。
完整ASLR重复运行台账和符号化回执没有作为本批公开证据提供，因此上面两项明确是新实验设计，
不是声称历史已经逐项完成的操作手册。

身份、完整性、调用权限、服务端权威、可观测性也要分别说明：
一个哈希可用于比较文件是否相同，却不会自动授予调用权限；一份漂亮栈图也不是业务成功证明。
本章不增加保护链，不提供现行产品绕过路线，不分发私有PDB或第三方二进制。
