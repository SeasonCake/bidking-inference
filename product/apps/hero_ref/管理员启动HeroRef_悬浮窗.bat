@echo off
setlocal
chcp 65001 >nul
title BidKing Hero Ref 管理员启动
echo.
echo Hero Ref 需要管理员权限监测游戏包。
echo 如果没有弹出管理员授权，或进游戏后一直等待对局包：
echo 请右键本文件，选择“以管理员身份运行”。
echo.
call "%~dp0Start-HeroRef.bat" %*
endlocal
