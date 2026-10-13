@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-HeroRef.ps1" %*
if errorlevel 1 (
    echo.
    echo 启动失败。请先看上方红色或黄色提示。
    echo full 完整包请直接运行本文件，无需「导入本机游戏表」。
    echo 常见原因：未以管理员运行；或安全软件拦截 WinDivert。
    echo 仅 public-safe 公开包需先 Import-LocalTables.bat / 导入本机游戏表.bat。
    echo 若 UI 已开但一直「等待 monitor 状态」，请查看：
    echo   data\logs\live\monitor.stderr.log
    pause
)
endlocal
