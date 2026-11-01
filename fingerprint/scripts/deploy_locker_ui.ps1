# Deploy locker-ui sources to a Pi, rebuild one-file binary, restart stack.
# Usage:
#   cd "D:\MPER-2026 FIRMWARE\armory\raspberry\scripts"
#   .\deploy_locker_ui.ps1 -HostIp 192.168.0.18
#   .\deploy_locker_ui.ps1 -HostIp 192.168.0.30,192.168.0.31,192.168.0.18
param(
  [Parameter(Mandatory = $true)][string[]]$HostIp,
  [string]$User = "pi"
)

$ErrorActionPreference = "Stop"

function Find-RepoRoot {
  $dir = Get-Item $PSScriptRoot
  while ($null -ne $dir) {
    $armoryUi = Join-Path $dir.FullName "armory\raspberry\ui\sfm_bridge.py"
    $fpUi = Join-Path $dir.FullName "fingerprint\ui\sfm_bridge.py"
    if ((Test-Path $armoryUi) -or (Test-Path $fpUi)) { return $dir.FullName }
    $dir = $dir.Parent
  }
  throw "Cannot find repo root containing armory/raspberry/ui or fingerprint/ui"
}

$Root = Find-RepoRoot
$UiCandidates = @(
  (Join-Path $Root "armory\raspberry\ui"),
  (Join-Path $Root "fingerprint\ui")
)
$ScriptsCandidates = @(
  (Join-Path $Root "armory\raspberry\scripts"),
  (Join-Path $Root "fingerprint\scripts")
)
$Ui = $UiCandidates | Where-Object { Test-Path (Join-Path $_ "sfm_bridge.py") } | Select-Object -First 1
$Scripts = $ScriptsCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Ui) { throw "sfm_bridge.py not found under armory/raspberry/ui or fingerprint/ui" }

# Refuse to deploy unpatched store writer
if (-not (Select-String -Path (Join-Path $Ui "sfm_bridge.py") -Pattern "_store_lock" -Quiet)) {
  throw "sfm_bridge.py missing _store_lock fix: $Ui"
}

$Ssh = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=12", "-o", "StrictHostKeyChecking=accept-new")

$Remote = @'
#!/bin/bash
set -e
cd "$HOME/fingerprint/ui"
find . -maxdepth 1 -type f \( -name '*.py' -o -name '*.spec' \) -exec sed -i 's/\r$//' {} +
find static -type f \( -name '*.js' -o -name '*.html' -o -name '*.css' \) -exec sed -i 's/\r$//' {} +
if [ -f "$HOME/fingerprint/scripts/sync_master1.py" ]; then
  sed -i 's/\r$//' "$HOME/fingerprint/scripts/sync_master1.py"
fi
if [ ! -x .venv/bin/pyinstaller ]; then
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt pyinstaller
fi
.venv/bin/pyinstaller --noconfirm --clean locker-ui.spec
cp -f dist/locker-ui "$HOME/fingerprint/bin/locker-ui"
chmod +x "$HOME/fingerprint/bin/locker-ui"
pkill -9 -x locker-ui || true
sleep 1
systemctl --user restart fingerprint-stack.service
sleep 4
pgrep -a locker-ui || true
code=$(curl -s -m 5 -o /tmp/st.json -w "%{http_code}" http://127.0.0.1:8080/api/status || true)
echo "http=$code"
python3 -c 'import json;p=json.load(open("/tmp/st.json"));b=p.get("board")or{};print("sens",b.get("sens"),"door",b.get("door"),"users",sum(1 for v in (p.get("users")or{}).values() if isinstance(v,dict) and v.get("user_id")))'
'@

$RebuildLocal = Join-Path $env:TEMP "_tmp_rebuild_ui_deploy.sh"
[System.IO.File]::WriteAllText($RebuildLocal, ($Remote -replace "`r`n", "`n" -replace "`r", "`n"))

foreach ($ip in $HostIp) {
  $ip = "$ip".Trim()
  if (-not $ip) { continue }
  $Target = "${User}@${ip}"
  Write-Host "==== Upload -> $Target (from $Ui) ===="
  scp @Ssh `
    (Join-Path $Ui "server.py") `
    (Join-Path $Ui "sfm_bridge.py") `
    (Join-Path $Ui "locker_board.py") `
    (Join-Path $Ui "requirements.txt") `
    (Join-Path $Ui "locker-ui.spec") `
    "${Target}:~/fingerprint/ui/"
  if (Test-Path (Join-Path $Ui "static")) {
    scp @Ssh -r (Join-Path $Ui "static") "${Target}:~/fingerprint/ui/"
  }
  $SyncPy = Join-Path $Scripts "sync_master1.py"
  if (Test-Path $SyncPy) {
    scp @Ssh $SyncPy "${Target}:~/fingerprint/scripts/sync_master1.py"
  }
  scp @Ssh $RebuildLocal "${Target}:~/fingerprint/scripts/_tmp_rebuild_ui.sh"
  Write-Host "==== Rebuild/restart on $Target (~2 min) ===="
  ssh @Ssh $Target "chmod +x ~/fingerprint/scripts/_tmp_rebuild_ui.sh && bash ~/fingerprint/scripts/_tmp_rebuild_ui.sh"
  Write-Host "==== Done $Target ===="
}
