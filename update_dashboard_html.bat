@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\infomax\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo.
echo Updating ETF dashboard HTML...
echo.

"%PYTHON_EXE%" "%~dp0build_etf_dashboard.py"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
  echo Done. Updated etf_active_weight_dashboard.html
) else (
  echo Dashboard update failed. Exit code: %EXIT_CODE%
)
echo.
pause
exit /b %EXIT_CODE%
