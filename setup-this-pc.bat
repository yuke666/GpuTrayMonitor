@echo off
setlocal enabledelayedexpansion
title GpuTrayMonitor - Set up this PC

rem One-click setup for a fresh machine:
rem   1. remove the "downloaded from the internet" mark
rem   2. trust the self-signed certificate (current user)
rem   3. stop the legacy attachment prompt (per-user zone policy)
rem   4. keep SmartScreen at its system default
rem   5. verify the signature and create a desktop shortcut
rem
rem Everything here is per-user; no administrator rights required.

set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"
set "PORT=%DIR%\portable"
set "CER=%PORT%\GpuTrayMonitor-selfsigned.cer"
if not exist "%CER%" set "CER=%DIR%\build\signing\GpuTrayMonitor-selfsigned.cer"
set "EXE=%PORT%\GpuMon.exe"

echo ============================================================
echo   GPU Tray Monitor - set up this PC
echo ============================================================
echo   folder: %DIR%
echo.

if not exist "%CER%" (
  echo [ERROR] GpuTrayMonitor-selfsigned.cer not found.
  echo         Expected in portable\ or build\signing\.
  echo.
  pause
  exit /b 1
)
if not exist "%EXE%" (
  echo [ERROR] portable\GpuMon.exe not found.
  echo.
  pause
  exit /b 1
)

echo [1/5] clearing the "downloaded" mark on every file...
powershell -NoProfile -Command "$n=0; Get-ChildItem -LiteralPath '%DIR%' -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object { try { Unblock-File -LiteralPath $_.FullName -ErrorAction Stop; $n++ } catch {} }; Write-Host ('      unblocked ' + $n + ' file(s)')"
echo.

echo [2/5] trusting the certificate (current user)...
certutil -addstore -user Root "%CER%" >nul 2>&1
if errorlevel 1 (
  echo       [FAILED] Trusted Root Certification Authorities
) else (
  echo       [OK]     Trusted Root Certification Authorities
)
certutil -addstore -user TrustedPublisher "%CER%" >nul 2>&1
if errorlevel 1 (
  echo       [FAILED] Trusted Publishers
) else (
  echo       [OK]     Trusted Publishers
)
echo.

echo [3/5] disabling the legacy attachment prompt (per-user zones)...
for %%Z in (0 1 2 3 4) do reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings\Zones\%%Z" /v 1806 /t REG_DWORD /d 0 /f >nul
echo       [OK]     zones 0-4: 1806 = 0 (allow)
echo       note    : downloaded exe/scripts will no longer ask first.
echo.

echo [4/5] checking SmartScreen...
reg query "HKLM\SOFTWARE\Policies\Microsoft\Windows\System" /v EnableSmartScreen >nul 2>&1
if errorlevel 1 (
  echo       [OK]     no policy override, SmartScreen uses its default
) else (
  echo       [WARN]   a policy is disabling SmartScreen.
  echo                If the blue screen stops you, run restore-smartscreen.bat as admin.
)
echo.

echo [5/5] signature and shortcut...
powershell -NoProfile -Command "$s = Get-AuthenticodeSignature -LiteralPath '%EXE%'; Write-Host ('      status : ' + $s.Status); if ($s.SignerCertificate) { Write-Host ('      signer : ' + $s.SignerCertificate.Subject) }"
powershell -NoProfile -ExecutionPolicy Bypass -File "%DIR%\tools\setup_shortcut.ps1" -Root "%DIR%"
echo.

echo ============================================================
echo   Done.
echo   Double-click "GPU Monitor" on the desktop, or portable\GpuMon.exe.
echo   Undo the prompt change with portable\restore-launch-prompt.bat.
echo ============================================================
echo.
pause
