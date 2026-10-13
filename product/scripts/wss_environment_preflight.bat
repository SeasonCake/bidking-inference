@echo off
setlocal
set "TOOL_DIR=%~dp0"
for %%I in ("%TOOL_DIR%..") do set "PACKAGE_ROOT=%%~fI"
set "PS1=%PACKAGE_ROOT%\scripts\wss_environment_preflight.ps1"
set "COMPONENT=%LOCALAPPDATA%\BidKingLive\components"

echo [START] BidKing internet-cafe compatibility check (read-only)
if not exist "%PS1%" (
  echo [ERROR] The compatibility check is missing. Fully extract the package.
  pause
  exit /b 2
)
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%PS1%" -Mode preflight -DryRun -PackageRoot "%PACKAGE_ROOT%" -ComponentPath "%COMPONENT%"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [READY] The current user passed the read-only compatibility check.
) else (
  echo [UNSUPPORTED] Review the fixed classification above and send diagnostics privately.
)
echo This check does not install certificates, drivers, services, or components.
echo.
pause
endlocal & exit /b %RC%
