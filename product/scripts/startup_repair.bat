@echo off
setlocal
set "TOOL_DIR=%~dp0"
for %%I in ("%TOOL_DIR%..") do set "PACKAGE_ROOT=%%~fI"
set "ROOT_EXE=%PACKAGE_ROOT%\BidKingLive.exe"

echo [START] BidKing startup repair
if not exist "%ROOT_EXE%" (
  echo.
  echo [ERROR] BidKingLive.exe was not found exactly one directory above this tool.
  echo Please fully extract the official package and run this tool in Diagnostics.
  echo.
  pause
  exit /b 2
)

REM The root executable performs the full integrity gate before opening the
REM native sealed repair payload. START /WAIT is required for a GUI executable.
start "" /wait "%ROOT_EXE%" --repair %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [DONE] Review the fixed result dialog and privacy-safe receipt.
) else (
  echo [FAILED] Startup repair ended with code %RC% and made no unverified cleanup.
)
echo Receipt folder: Windows Documents\BidKingLive\exports
echo.
pause
endlocal & exit /b %RC%
