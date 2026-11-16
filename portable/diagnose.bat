@echo off
setlocal
title GpuTrayMonitor - Diagnose file

set "TARGET=%~1"
if "%TARGET%"=="" (
  for %%F in ("%~dp0*.exe") do set "TARGET=%%~fF"
)

echo ============================================================
echo   File diagnosis
echo ============================================================
echo.
if not exist "%TARGET%" (
  echo [ERROR] file not found. Drag the exe onto this .bat, or put
  echo         this .bat next to the exe.
  echo.
  pause
  exit /b 1
)

echo Target : %TARGET%
echo.

echo --- 1) alternate data streams (Zone.Identifier = downloaded mark) ---
dir /r "%TARGET%" | findstr /i "Zone.Identifier"
echo.

echo --- 2) signature ---
powershell -NoProfile -Command "$s = Get-AuthenticodeSignature -LiteralPath '%TARGET%'; Write-Host ('  Status  : ' + $s.Status); Write-Host ('  Message : ' + $s.StatusMessage); if ($s.SignerCertificate) { Write-Host ('  Signer  : ' + $s.SignerCertificate.Subject) }"
echo.

echo --- 3) SmartScreen switches ---
reg query "HKLM\SOFTWARE\Policies\Microsoft\Windows\System" /v EnableSmartScreen 2>nul
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer" /v SmartScreenEnabled 2>nul
echo.

echo --- 4) other copies found under the user profile ---
powershell -NoProfile -Command "$n = [string]::Join('', [char[]](0x663E,0x5B58,0x76D1,0x89C6,0x5668)); Get-ChildItem $env:USERPROFILE -Filter '*.exe' -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Name -like ('*' + $n + '*') } | Select-Object -First 5 FullName, Length, LastWriteTime | Format-List"
echo.
echo Copy this window's text to the developer if the prompt still appears.
echo.
pause
