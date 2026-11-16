@echo off
rem Debug launcher: keeps a console so tracebacks and logs are visible.
cd /d "%~dp0"

set PYTHON="C:\Users\cferi\AppData\Local\Programs\Python\Python313\python.exe"
if not exist %PYTHON% set PYTHON=python

%PYTHON% gpumon.pyw --verbose
echo.
echo ---- exited with code %ERRORLEVEL% ----
pause
