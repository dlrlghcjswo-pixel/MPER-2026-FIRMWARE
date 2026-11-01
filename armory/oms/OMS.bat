@echo off
setlocal
cd /d "%~dp0"

REM Do NOT force-kill a running OMS here — main.py single-instance lock refuses
REM a second start and tells the user the program is already open.
REM (Dev restart: close the existing OMS window first, then run OMS.bat.)

set "LOG=%TEMP%\oms-launch.log"
echo [%DATE% %TIME%] launch from %CD% > "%LOG%"

where pyw >nul 2>&1
if %ERRORLEVEL%==0 (
  echo using pyw >> "%LOG%"
  start "OMS" pyw -3 "%~dp0main.py"
  exit /b 0
)

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  echo using py >> "%LOG%"
  start "OMS" py -3 "%~dp0main.py"
  exit /b 0
)

where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
  echo using pythonw >> "%LOG%"
  start "OMS" pythonw "%~dp0main.py"
  exit /b 0
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  echo using python >> "%LOG%"
  start "OMS" python "%~dp0main.py"
  exit /b 0
)

echo Python not found. >> "%LOG%"
echo Python(py/pyw) is required.
echo Log: %LOG%
notepad "%LOG%"
pause
exit /b 1
