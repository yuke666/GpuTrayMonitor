@echo off
setlocal
title GpuTrayMonitor - Unblock files

set "DIR=%~dp0"
echo ============================================================
echo   Remove the "downloaded from the internet" mark
echo ============================================================
echo.
echo Folder: %DIR%
echo.

powershell -NoProfile -Command "$n = 0; Get-ChildItem -LiteralPath '%DIR%' -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object { try { Unblock-File -LiteralPath $_.FullName -ErrorAction Stop; $n++ } catch {} }; Write-Host ('Unblocked ' + $n + ' file(s).')"

echo.
echo If SmartScreen still blocks the exe after this, the cause is low
echo application reputation, not the download flag. Options:
echo   1) start the app with the .vbs launcher (no exe signature check)
echo   2) turn off "Check apps and files" with disable-smartscreen.bat
echo.
pause
