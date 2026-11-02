# NEXUS AI Master - SAFE stop (drain + graceful shutdown)
#
# Only NEXUS-owned server processes are ever considered, identified by the exact
# run_server.py command line under -Root. There is no blanket taskkill and no
# port-owner sweep.
#
# Flow:
#   1. own/foreign detection
#   2. authenticated, loopback-only maintenance drain  (POST /api/bridge/maintenance/drain)
#   3. wait until DRAINED (0 in-flight) or timeout -> on timeout: cancel drain, BLOCKED (work preserved)
#   4. graceful shutdown                               (POST /api/bridge/maintenance/shutdown)
#   5. wait for the owned PID(s) to exit; only then, and only if already drained,
#      a Stop-Process on the exact owned PID(s) is allowed as a last resort.
#
# -AllowLegacyIdleStop: one-time bootstrap for a server started before the drain
#   contract existed. Requires the authenticated bridge to prove there are no
#   running/queued/awaiting_approval tasks, then stops only the exact owned PID.
param(
  [string]$Root = $PSScriptRoot,
  [int]$Port = 8299,
  [int]$DrainTimeoutSec = 300,
  [int]$ShutdownGraceSec = 30,
  [int]$StartupGraceSec = 5,
  [switch]$AllowLegacyIdleStop,
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"

$logDir = Join-Path $Root "workspace\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "nexus_control.log"

function Log([string]$message) {
  Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date).ToString("s"), $message) -Encoding UTF8
}

function Get-OwnedServer {
  $serverPath = [IO.Path]::GetFullPath((Join-Path $Root "run_server.py"))
  $escaped = [regex]::Escape($serverPath)
  $pattern = '(?i)(?:^|\s)"?' + $escaped + '(?:"|\s|$)'
  @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
    $_.Name -match '^python(w|3(\.\d+)?)?\.exe$' -and $_.CommandLine -match $pattern
  })
}

# Phase 2 (02.11.2026): WindowsApps-Starter + sein python-Kind = EIN Server.
# Liefert Count (Server) und Pids (Stopp-Reihenfolge: Kind zuerst, dann Starter).
function Get-ServerGroups($procs) {
  $procs = @($procs)
  $byId = @{}; foreach ($p in $procs) { $byId[[int]$p.ProcessId] = $p }
  $kids = @($procs | Where-Object {
    $par = $byId[[int]$_.ParentProcessId]
    $par -and ("" + $par.ExecutablePath + " " + $par.CommandLine) -match '(?i)\\WindowsApps\\python'
  })
  $kidIds = @($kids | ForEach-Object { [int]$_.ProcessId })
  $rest = @($procs | Where-Object { $kidIds -notcontains [int]$_.ProcessId } | ForEach-Object { [int]$_.ProcessId })
  [pscustomobject]@{ Count = $procs.Count - $kids.Count; Pids = @($kidIds + $rest); Pairs = $kids.Count }
}

function Get-Listeners {
  @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Get-BridgeToken {
  if ($env:NEXUS_BRIDGE_TOKEN) { return $env:NEXUS_BRIDGE_TOKEN.Trim() }
  foreach ($p in @((Join-Path $Root "workspace\bridge\.token"), (Join-Path $Root "workspace\certs\.bridge_token"))) {
    if (Test-Path -LiteralPath $p) { return (Get-Content -LiteralPath $p -Raw).Trim() }
  }
  return ""
}

function Invoke-MaintPost([string]$Uri, [hashtable]$Body, [hashtable]$Headers) {
  $json = ($Body | ConvertTo-Json -Compress)
  try {
    return @{ ok = $true; code = 200; data = (Invoke-RestMethod -Uri $Uri -Method Post -Headers $Headers -ContentType "application/json" -Body $json -TimeoutSec 15) }
  } catch {
    $code = 0
    try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    return @{ ok = $false; code = $code; data = $null; error = $_.Exception.Message }
  }
}

$lock = $null
try {
  $lock = [IO.File]::Open((Join-Path $logDir "nexus_start.lock"), "OpenOrCreate", "ReadWrite", "None")

  $owned = @(Get-OwnedServer)
  $listeners = @(Get-Listeners)
  $groups = Get-ServerGroups $owned
  if ($DryRun) {
    $verdict = if ($groups.Count -gt 1) { 'WUERDE BLOCKEN (mehrere Server)' } elseif ($owned.Count -eq 0 -and $listeners.Count -gt 0) { 'WUERDE BLOCKEN (fremder Port-Inhaber)' } elseif ($owned.Count -eq 0) { 'schon gestoppt' } else { 'WUERDE STOPPEN' }
    Write-Output ("DRYRUN procs={0} server={1} pairs={2} stopOrder={3} listeners={4} -> {5}" -f $owned.Count, $groups.Count, $groups.Pairs, ($groups.Pids -join ','), $listeners.Count, $verdict)
    exit 0
  }

  if ($owned.Count -eq 0 -and $listeners.Count -eq 0) {
    Log "STOP already stopped (no owned process, no listener)"
    Write-Output "NEXUS already stopped"
    exit 0
  }
  if ($groups.Count -gt 1) {
    $msg = "BLOCKED: multiple owned NEXUS servers ($($groups.Count)); no process stopped"
    Log $msg; Write-Output $msg; exit 12
  }
  if ($owned.Count -eq 0 -and $listeners.Count -gt 0) {
    $msg = "BLOCKED: port $Port held by a non-NEXUS process ($($listeners.OwningProcess -join ',')); no process stopped"
    Log $msg; Write-Output $msg; exit 13
  }

  $ownedPids = @($groups.Pids)
  Log ("STOP begin port=$Port ownedPids=$($ownedPids -join ',')")

  $token = Get-BridgeToken
  if (-not $token) {
    $msg = "BLOCKED: bridge token missing; cannot authenticate maintenance drain; no process stopped"
    Log $msg; Write-Output $msg; exit 31
  }
  $headers = @{ Authorization = "Bearer $token" }
  $base = "http://127.0.0.1:$Port/api/bridge/maintenance"

  # --- 2. request drain ---
  $r = Invoke-MaintPost "$base/drain" @{ reason = "safe stop via stop_nexus.ps1"; actor = "stop_nexus.ps1" } $headers
  $legacy = $false
  if (-not $r.ok) {
    if ($r.code -in @(401, 403)) {
      $msg = "BLOCKED: maintenance auth/local check failed (http $($r.code)); no process stopped"
      Log $msg; Write-Output $msg; exit 31
    } elseif ($r.code -eq 404) {
      $legacy = $true
    } else {
      $msg = "BLOCKED: drain request failed (http $($r.code)); no process stopped"
      Log $msg; Write-Output $msg; exit 30
    }
  }

  if ($legacy) {
    if (-not $AllowLegacyIdleStop) {
      $msg = "BLOCKED: pre-drain server has no maintenance endpoint; re-run with -AllowLegacyIdleStop for a verified idle-only stop; no process stopped"
      Log $msg; Write-Output $msg; exit 30
    }
    # Prove no active bridge tasks via authenticated API before any stop.
    try {
      $tasks = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/bridge/tasks?limit=200" -Headers $headers -TimeoutSec 10
    } catch {
      $msg = "BLOCKED: legacy idle check could not read bridge tasks; no process stopped"
      Log $msg; Write-Output $msg; exit 30
    }
    $active = @($tasks.tasks | Where-Object { $_.status -in @("running", "queued", "awaiting_approval") })
    if ($active.Count -gt 0) {
      $msg = "BLOCKED: legacy path found $($active.Count) active task(s); no process stopped"
      Log $msg; Write-Output $msg; exit 30
    }
    Log "LEGACY idle stop: 0 active tasks; stopping exact owned PID(s) $($ownedPids -join ',')"
    foreach ($p in $ownedPids) { Stop-Process -Id $p -ErrorAction SilentlyContinue }
  } else {
    # --- 3. wait for drain ---
    $deadline = (Get-Date).AddSeconds($DrainTimeoutSec)
    $drained = $false
    $confirm = 0
    while ((Get-Date) -lt $deadline) {
      try {
        $st = Invoke-RestMethod -Uri $base -Headers $headers -TimeoutSec 5
        $m = $st.maintenance
        if ($m.state -eq "DRAINED" -and [int]$m.in_flight -eq 0) {
          $confirm++
          if ($confirm -ge 2) { $drained = $true; break }
        } else {
          $confirm = 0
        }
      } catch {}
      Start-Sleep -Milliseconds 500
    }
    if (-not $drained) {
      $c = Invoke-MaintPost "$base/cancel" @{ actor = "stop_nexus.ps1" } $headers
      $msg = "BLOCKED: drain timeout with work still in flight; drain cancelled, running work preserved, no process stopped"
      Log $msg; Write-Output $msg; exit 30
    }
    Log "DRAINED (0 in-flight)"

    # --- 4. graceful shutdown ---
    $s = Invoke-MaintPost "$base/shutdown" @{ confirm = $true; actor = "stop_nexus.ps1" } $headers
    if (-not $s.ok) {
      $msg = "BLOCKED: graceful shutdown not accepted (http $($s.code)); drained work preserved, no process stopped"
      Log $msg; Write-Output $msg; exit 30
    }
    Log "graceful shutdown requested"
  }

  # --- 5. wait for exit / port release ---
  $deadline = (Get-Date).AddSeconds($ShutdownGraceSec)
  while ((Get-Date) -lt $deadline) {
    $o = @(Get-OwnedServer)
    $l = @(Get-Listeners)
    if ($o.Count -eq 0 -and $l.Count -eq 0) {
      Log "STOP complete: owned process exited and port released"
      Write-Output "NEXUS stopped"
      exit 0
    }
    Start-Sleep -Milliseconds 500
  }

  # Last resort: work is drained (or legacy-verified idle); stop only the exact owned PID(s).
  $still = @((Get-ServerGroups @(Get-OwnedServer)).Pids)
  if ($still.Count -gt 0) {
    Log ("last-resort Stop-Process on drained owned PID(s): $($still -join ',')")
    foreach ($p in $still) { Stop-Process -Id $p -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
  }
  $o = @(Get-OwnedServer)
  $l = @(Get-Listeners)
  if ($o.Count -eq 0 -and $l.Count -eq 0) {
    Log "STOP complete after graceful fallback"
    Write-Output "NEXUS stopped"
    exit 0
  }
  $msg = "BLOCKED: owned process/port still present after drain; no further action"
  Log $msg; Write-Output $msg; exit 30
} catch {
  Log ("BLOCKED stop failed: {0}" -f $_.Exception.GetType().Name)
  Write-Output "BLOCKED: stop failed"
  exit 31
} finally {
  if ($lock) { $lock.Dispose() }
}
