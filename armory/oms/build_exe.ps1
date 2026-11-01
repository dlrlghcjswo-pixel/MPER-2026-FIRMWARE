# Rebuild one-file "스마트 무기고 시스템.exe" from current armory/oms sources.
$ErrorActionPreference = "Continue"
$oms = Split-Path -Parent $MyInvocation.MyCommand.Path
$tmp = "D:\MPER-2026 FIRMWARE\_build_tmp"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$env:TEMP = $tmp
$env:TMP = $tmp
$env:PYTHONPATH = Join-Path $oms "_deps"

Write-Host "Stopping running OMS..."
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
  $_.Name -like "*무기고*" -or ($_.CommandLine -and $_.CommandLine -match "armory\\oms|스마트 무기고|ArmoryOMS")
} | ForEach-Object {
  Write-Host "  kill PID $($_.ProcessId)"
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 3
if (Test-Path (Join-Path $oms "스마트 무기고 시스템.exe")) {
  Rename-Item (Join-Path $oms "스마트 무기고 시스템.exe") (Join-Path $oms "스마트 무기고 시스템.exe.bak_build") -Force -ErrorAction SilentlyContinue
}

Set-Location $oms
Write-Host "PyInstaller..."
python -m PyInstaller --noconfirm ".\스마트 무기고 시스템.spec"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$src = Join-Path $oms "dist\스마트 무기고 시스템.exe"
$dst = Join-Path $oms "스마트 무기고 시스템.exe"
Copy-Item $src $dst -Force
Remove-Item (Join-Path $oms "스마트 무기고 시스템.exe.bak_build") -Force -ErrorAction SilentlyContinue
Get-Item $dst | Format-List FullName, Length, LastWriteTime
Write-Host "EXE_UPDATED"
