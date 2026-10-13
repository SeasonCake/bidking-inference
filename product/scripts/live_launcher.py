"""BidKing Live 启动器壳 —— PyInstaller 打成 BidKingLive.exe(猫图标),玩家双击即跑。
壳本身不提权:它拉起 scripts/start_live_npcap_overlay.ps1(该脚本自己弹 UAC 提权 + 首次自动装
Npcap + 起 monitor + 起 overlay),用包内嵌 Python。壳启动后即退出,悬浮窗独立运行。
"""
import os
import subprocess
import sys

# 冻结后 sys.executable = BidKingLive.exe 路径; 它放在包根目录
base = os.path.dirname(os.path.abspath(sys.executable))
ps1 = os.path.join(base, "scripts", "start_live_npcap_overlay.ps1")
embed_py = os.path.join(base, "runtime", "python", "python.exe")

CREATE_NO_WINDOW = 0x08000000
try:
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
         "-File", ps1, "-PythonPath", embed_py],
        creationflags=CREATE_NO_WINDOW,
        cwd=base,
    )
except Exception as exc:  # noqa: BLE001
    import ctypes
    ctypes.windll.user32.MessageBoxW(
        0, f"启动失败: {exc}\n请确认完整解压、未被杀软拦截。", "BidKing Live", 0x10)
