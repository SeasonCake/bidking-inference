@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在运行入站抓包深度诊断(约需 15-20 秒, 第8项会采样15秒)...
echo 建议先进一局游戏、走到出价/读牌那一步再跑, 这样能量到真实入站流量。
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnose_inbound_capture.ps1"
echo.
echo 若上面显示了生成的 txt 路径, 请把那个 txt 整份发给作者。
pause
