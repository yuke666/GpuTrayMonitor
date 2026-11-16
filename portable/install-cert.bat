@echo off
setlocal enabledelayedexpansion
title GpuTrayMonitor - Install Self-Signed Certificate

set "DIR=%~dp0"
set "CER=%DIR%GpuTrayMonitor-selfsigned.cer"
if not exist "%CER%" set "CER=%DIR%..\build\signing\GpuTrayMonitor-selfsigned.cer"

echo ============================================================
echo   GpuTrayMonitor - install self-signed certificate
echo ============================================================
echo.

if not exist "%CER%" (
  echo [ERROR] GpuTrayMonitor-selfsigned.cer not found.
  echo         Put this .bat in the same folder as the .cer file.
  echo.
  pause
  exit /b 1
)

echo Certificate: %CER%
echo.
echo Importing into the CURRENT USER stores ^(no admin rights needed^)...
echo.

certutil -addstore -user Root "%CER%" >nul 2>&1
if errorlevel 1 (
  echo   [FAILED]  Trusted Root Certification Authorities
  set "RC=1"
) else (
  echo   [OK]      Trusted Root Certification Authorities
)

certutil -addstore -user TrustedPublisher "%CER%" >nul 2>&1
if errorlevel 1 (
  echo   [FAILED]  Trusted Publishers
  set "RC=1"
) else (
  echo   [OK]      Trusted Publishers
)

echo.
set "APP="
for %%F in ("%DIR%*.exe") do set "APP=%%F"

if defined APP (
  echo Checking signature of: !APP!
  echo.
  powershell -NoProfile -Command "$s = Get-AuthenticodeSignature -LiteralPath '!APP!'; Write-Host ('  Status : ' + $s.Status); if ($s.SignerCertificate) { Write-Host ('  Signer : ' + $s.SignerCertificate.Subject); Write-Host ('  Expires: ' + $s.SignerCertificate.NotAfter) }"
  echo.
) else (
  echo No .exe found next to this script, skipped signature check.
  echo.
)

if defined RC (
  echo Some steps failed. Make sure this account can write the user certificate stores.
) else (
  echo Done. "Unknown publisher" should be gone for this exe on this machine.
)
echo Run "uninstall-cert.bat" to undo.
echo.
pause
