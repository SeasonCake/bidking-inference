@echo off
chcp 65001 >nul
REM BidKing one-click diagnostics. Double-click to run.
REM Generates a privacy-safe report under Windows Documents\BidKingLive\exports.
REM Finds collect_diagnostics.ps1 in source, old package, or grouped package layout.
REM
REM 2026-06-29 fix: group users reported "no response" because the window closed
REM   instantly when PowerShell exited 0 -- this .bat only paused on error. Now:
REM   1) always pause at the end so the window stays open and shows the result path;
REM   2) set BIDKING_DIAG_NONINTERACTIVE=1 so the .ps1 skips its own key-wait
REM      (this .bat does the single pause, avoiding a double-wait);
REM   3) the .ps1 writes output to Windows Documents\BidKingLive\exports and opens
REM      the exact location; it never falls back to the package or data directory.
REM
REM KEEP THIS .bat PURE ASCII. cmd.exe does not honor a UTF-8 BOM in .bat files
REM (the BOM breaks @echo off) and reads no-BOM UTF-8 CJK as GBK garbage on Chinese
REM Windows, silently breaking the `set` env vars. CJK text belongs in the .ps1 only.
setlocal
set "BIDKING_DIAG_NONINTERACTIVE=1"
echo [START] BidKing diagnostics
set "PS1=%~dp0collect_diagnostics.ps1"
if not exist "%PS1%" set "PS1=%~dp0scripts\collect_diagnostics.ps1"
if not exist "%PS1%" set "PS1=%~dp0..\scripts\collect_diagnostics.ps1"
if not exist "%PS1%" (
  echo.
  echo [ERROR] collect_diagnostics.ps1 not found.
  echo It should be next to this .bat, in scripts\, or in ..\scripts\.
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
set "RC=%errorlevel%"
echo.
echo ==================================================
if "%RC%"=="0" (
  echo  Done. See the exact result path shown above.
  echo  Saved under Windows Documents\BidKingLive\exports.
) else (
  echo  Failed with code %RC%. Review the fixed failure message above.
)
echo  Send the WHOLE .txt file privately to support staff.
echo ==================================================
echo.
pause
endlocal & exit /b %RC%
