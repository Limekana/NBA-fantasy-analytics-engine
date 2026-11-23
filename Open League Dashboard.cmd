@echo off
title NBA League Dashboard
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\open_league_dashboard.ps1"
if errorlevel 1 (
  echo.
  echo The dashboard could not start. Please copy the error above into Codex.
  pause
  exit /b 1
)
echo.
echo If your browser did not open, copy the dashboard address shown above into its address bar.
echo You can close this window. The dashboard stays running.
pause
