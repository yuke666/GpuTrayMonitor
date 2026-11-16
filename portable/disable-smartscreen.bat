@echo off
setlocal
title GpuTrayMonitor - Disable SmartScreen app check

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo ============================================================
echo   Disable "Check apps and files" (SmartScreen)
echo ============================================================
echo.
echo This lowers protection for ALL downloaded programs on this PC.
echo Edge browsing protection is NOT affected.
echo.

reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\System" /v EnableSmartScreen /t REG_DWORD /d 0 /f >nul
if errorlevel 1 (
  echo   [FAILED] policy EnableSmartScreen = 0
) else (
  echo   [OK]     policy EnableSmartScreen = 0
)

reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer" /v SmartScreenEnabled /t REG_SZ /d Off /f >nul
if errorlevel 1 (
  echo   [FAILED] SmartScreenEnabled = Off
) else (
  echo   [OK]     SmartScreenEnabled = Off
)

echo.
echo Done. Start the exe again. If Windows still shows the blue screen,
echo sign out and back in so Explorer reloads the setting.
echo Run restore-smartscreen.bat to undo.
echo.
pause
