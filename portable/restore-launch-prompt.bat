@echo off
setlocal
title GpuTrayMonitor - Restore the security prompt

echo ============================================================
echo   Restore "launching applications and unsafe files"
echo ============================================================
echo.
echo Removes the per-user overrides, so Windows goes back to asking
echo before running files that came from the Internet.
echo.

set "ZONES=0 1 2 3 4"
for %%Z in (%ZONES%) do (
  reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings\Zones\%%Z" /v 1806 /f >nul 2>&1
  if errorlevel 1 (
    echo   [SKIP]   zone %%Z  ^(not set^)
  ) else (
    echo   [OK]     zone %%Z  restored
  )
)

echo.
echo Restored to Windows defaults.
echo.
pause
