@echo off
setlocal
title GpuTrayMonitor - Restore SmartScreen app check

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo ============================================================
echo   Restore "Check apps and files" (SmartScreen)
echo ============================================================
echo.

reg delete "HKLM\SOFTWARE\Policies\Microsoft\Windows\System" /v EnableSmartScreen /f >nul 2>&1
if errorlevel 1 (
  echo   [SKIP]   policy value was not set
) else (
  echo   [OK]     policy value removed
)

reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer" /v SmartScreenEnabled /t REG_SZ /d Warn /f >nul
if errorlevel 1 (
  echo   [FAILED] SmartScreenEnabled = Warn
) else (
  echo   [OK]     SmartScreenEnabled = Warn
)

echo.
echo Restored. Sign out and back in for the change to fully apply.
echo.
pause
