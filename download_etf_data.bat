@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\infomax\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "TODAY=%%D"

echo.
echo ETF holdings download
echo Date format: YYYY-MM-DD
echo Leave blank to use today's date: %TODAY%
echo.

set /p START_DATE=Start date [%TODAY%]: 
if "%START_DATE%"=="" set "START_DATE=%TODAY%"

set /p END_DATE=End date [%TODAY%]: 
if "%END_DATE%"=="" set "END_DATE=%TODAY%"

echo.
echo Downloading ETF data from %START_DATE% to %END_DATE%...
echo.

"%PYTHON_EXE%" "%~dp0etf_holdings_pipeline.py" --start "%START_DATE%" --end "%END_DATE%" --skip-existing
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
  echo Done.
) else (
  echo Download failed. Exit code: %EXIT_CODE%
)
echo.
pause
exit /b %EXIT_CODE%
