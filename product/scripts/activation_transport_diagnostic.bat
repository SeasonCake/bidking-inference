@echo off
setlocal
set "PYTHONDONTWRITEBYTECODE=1"
set "TOOL_DIR=%~dp0"
set "PACKAGE_ROOT=%TOOL_DIR%"
if not exist "%PACKAGE_ROOT%runtime\python\python.exe" set "PACKAGE_ROOT=%TOOL_DIR%.."
for %%I in ("%PACKAGE_ROOT%") do set "PACKAGE_ROOT=%%~fI"
set "PYTHON=%PACKAGE_ROOT%\runtime\python\python.exe"
set "SCRIPT=%TOOL_DIR%activation_transport_diagnostic.py"

if not exist "%PYTHON%" (
  echo ERROR: package runtime Python was not found.
  pause
  exit /b 2
)
if not exist "%SCRIPT%" (
  echo ERROR: activation transport diagnostic script was not found.
  pause
  exit /b 3
)

pushd "%PACKAGE_ROOT%"
echo [START] Activation network diagnostic started. Please keep this window open...
"%PYTHON%" -B "%SCRIPT%"
set "EXIT_CODE=%ERRORLEVEL%"
popd
echo.
if "%EXIT_CODE%"=="0" (
  echo [DONE] Diagnostic complete. Send activation_transport_diagnostic.txt to the author.
) else (
  echo [FAILED] Diagnostic did not complete. Keep a screenshot; do not send an old report.
)
pause
exit /b %EXIT_CODE%
