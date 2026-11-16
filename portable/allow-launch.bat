@echo off
setlocal
title GpuTrayMonitor - Allow launching local apps and scripts

echo ============================================================
echo   Allow "launching applications and unsafe files"
echo ============================================================
echo.
echo This stops the "Open File - Security Warning" prompt that appears
echo when you double-click an exe or a .vbs script.
echo.
echo It is a per-user (HKCU) setting, no administrator rights needed.
echo Tradeoff: downloaded programs will no longer ask before running.
echo Run restore-launch-prompt.bat to undo.
echo.

set "ZONES=0 1 2 3 4"
for %%Z in (%ZONES%) do (
  reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings\Zones\%%Z" /v 1806 /t REG_DWORD /d 0 /f >nul
  if errorlevel 1 (
    echo   [FAILED] zone %%Z
  ) else (
    echo   [OK]     zone %%Z  =  Allow
  )
)

echo.
echo Done. Try double-clicking the exe or the .vbs again.
echo If it still asks, sign out and back in once.
echo.
pause
