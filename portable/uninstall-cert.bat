@echo off
setlocal
title GpuTrayMonitor - Remove Self-Signed Certificate

rem Thumbprint of the certificate created by tools\sign_exe.ps1.
rem Replace it if you generated a new certificate.
set "THUMB=C27960121153C232944A01E399A92080D319E99F"

echo ============================================================
echo   GpuTrayMonitor - remove self-signed certificate
echo ============================================================
echo.

certutil -delstore -user Root %THUMB% >nul 2>&1
if errorlevel 1 (
  echo   [SKIP]    Trusted Root Certification Authorities ^(not present^)
) else (
  echo   [OK]      removed from Trusted Root Certification Authorities
)

certutil -delstore -user TrustedPublisher %THUMB% >nul 2>&1
if errorlevel 1 (
  echo   [SKIP]    Trusted Publishers ^(not present^)
) else (
  echo   [OK]      removed from Trusted Publishers
)

echo.
echo The exe will show "Unknown publisher" again (signature stays, trust is gone).
echo Run "install-cert.bat" to restore.
echo.
pause
