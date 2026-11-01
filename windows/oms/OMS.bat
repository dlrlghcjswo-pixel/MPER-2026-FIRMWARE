@echo off
cd /d "%~dp0"
if exist "%~dp0스마트 총기 보관함 OMS.exe" (
  start "" "%~dp0스마트 총기 보관함 OMS.exe"
  exit /b 0
)
if exist "%~dp0OMS.exe" (
  start "" "%~dp0OMS.exe"
  exit /b 0
)
where pyw >nul 2>&1 && start "" pyw -3 "%~dp0main.py" && exit /b 0
start "" py -3 "%~dp0main.py"
