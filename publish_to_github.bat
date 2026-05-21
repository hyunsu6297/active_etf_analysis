@echo off
setlocal

where git >nul 2>nul
if errorlevel 1 (
  echo Git is not installed or is not available in PATH.
  echo Install Git for Windows, then run this file again.
  pause
  exit /b 1
)

if not exist .git (
  git init
)

git branch -M main
git remote get-url origin >nul 2>nul
if errorlevel 1 (
  git remote add origin https://github.com/hyunsu6297/active_etf_analysis.git
) else (
  git remote set-url origin https://github.com/hyunsu6297/active_etf_analysis.git
)

git add .github data .nojekyll README.md requirements.txt build_etf_dashboard.py etf_holdings_pipeline.py update_daily_dashboard.py download_etf_data.bat update_dashboard_html.bat etf_active_weight_dashboard.html index.html plotly-2.35.2.min.js "%~dp0종목리스트.xlsx"
git commit -m "Initial active ETF dashboard"
git push -u origin main

pause
