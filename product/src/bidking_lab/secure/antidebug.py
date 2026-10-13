"""L3 反调试检测(推荐起步档: 手段 1+2, 行为 B+C, 不含时序检测=避免误判)。

【行为中立·绝不硬锁】本模块只【检测并返回 DebugVerdict】, 不退出/不阻断 —— 命中后"怎么办"
(B 静默降级 / C 上报服务端)由上层策略 + L4 激活权威决定, 故本身【无误判把正常用户锁死的风险】。
所有检测都包 try/except: 反调试自身绝不能让 app 崩。

检测手段:
- 1 IsDebuggerPresent + CheckRemoteDebuggerPresent(Win API): 高置信=调试器【附加到本进程】, 误判极低。
- 2 已知逆向/调试工具进程扫描(psutil): 低置信=机器上在跑这些工具(注意: 在跑 ≠ 附加到我们),
    仅 informational, 由上层弱加权。
不做时序检测(慢机/杀软误判高)。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

# 已知逆向/调试/内存工具进程名(小写, 去 .exe 后比对)
_KNOWN_TOOLS: frozenset[str] = frozenset({
    "x64dbg", "x32dbg", "x96dbg", "ollydbg", "ollyice", "immunitydebugger",
    "ida", "ida64", "ida32", "idaq", "idaq64", "idau64",
    "windbg", "dnspy", "dnspy-x86", "ghidra", "ghidrarun", "binaryninja",
    "cheatengine", "cheatengine-x86_64", "cheatengine-i386",
    "frida", "frida-server", "fridaserver", "scylla", "scylla_x64", "scylla_x86",
    "processhacker", "pestudio", "reclass", "reclass.net", "hyperdbg",
})


@dataclass
class DebugVerdict:
    debugger_attached: bool = False                          # 高置信: 调试器附加本进程
    tools_running: list[str] = field(default_factory=list)   # 低置信: 机器上在跑的已知工具
    errors: list[str] = field(default_factory=list)          # 检测自身的异常(不影响结果可用性)

    @property
    def suspicious(self) -> bool:
        return self.debugger_attached or bool(self.tools_running)

    @property
    def high_confidence(self) -> bool:
        """仅"调试器真附加"算高置信(适合触发 B 降级); 工具在跑只是 informational。"""
        return self.debugger_attached

    def summary(self) -> str:
        if not self.suspicious:
            return "clean"
        return f"debugger_attached={self.debugger_attached} tools={self.tools_running}"


def _is_debugger_present_win() -> bool:
    if not sys.platform.startswith("win"):
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    if k32.IsDebuggerPresent():
        return True
    flag = ctypes.c_int(0)
    # CheckRemoteDebuggerPresent(GetCurrentProcess(), &flag) 能抓部分 IsDebuggerPresent 漏的
    if k32.CheckRemoteDebuggerPresent(k32.GetCurrentProcess(), ctypes.byref(flag)):
        return bool(flag.value)
    return False


def _scan_known_tools() -> list[str]:
    try:
        import psutil
    except Exception:  # noqa: BLE001 - 无 psutil 就跳过该手段
        return []
    found: set[str] = set()
    for p in psutil.process_iter(["name"]):
        try:
            name = (p.info.get("name") or "").lower()
        except Exception:  # noqa: BLE001
            continue
        if not name:
            continue
        base = name[:-4] if name.endswith(".exe") else name
        if base in _KNOWN_TOOLS:
            found.add(base)
    return sorted(found)


def detect() -> DebugVerdict:
    """跑全部检测, 返回 verdict。绝不抛异常(各手段失败计入 errors)。"""
    v = DebugVerdict()
    try:
        v.debugger_attached = _is_debugger_present_win()
    except Exception as e:  # noqa: BLE001
        v.errors.append(f"dbg:{type(e).__name__}")
    try:
        v.tools_running = _scan_known_tools()
    except Exception as e:  # noqa: BLE001
        v.errors.append(f"scan:{type(e).__name__}")
    return v


def evaluate(report_hook=None) -> DebugVerdict:
    """检测 +(C)可选上报。返回 verdict 供上层决定(B)降级。

    report_hook(verdict): C 的接缝, 后续接 collector /telemetry 上报可疑设备; 默认 None=不上报。
    本函数【不降级、不退出】—— 降级由调用方按 verdict.high_confidence/suspicious 决定(B), L4 激活做权威拒绝。
    """
    v = detect()
    if v.suspicious and report_hook is not None:
        try:
            report_hook(v)
        except Exception:  # noqa: BLE001 - 上报失败绝不影响 app
            pass
    return v
