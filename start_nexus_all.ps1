param([string]$Root = $PSScriptRoot)
$ErrorActionPreference = "Stop"
$logDir = Join-Path $Root "workspace\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "nexus_control.log"
$env:TEMP = Join-Path $Root "workspace\tmp"; $env:TMP = $env:TEMP
# t329u: Kindprozesse (Server/Gateway) duerfen die Std-Handles des Aufrufers NICHT erben.
# Sonst haelt der dauerlaufende Server eine geerbte Pipe offen und jeder Aufrufer, der die
# Ausgabe liest (Pipeline, Remote-Shell, Scheduler), wartet ewig -> "haengender" Neustart.
function Disable-NexusStdHandleInheritance {
  try {
    if (-not ('NexusStdHandles' -as [type])) {
      Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class NexusStdHandles {
  [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr GetStdHandle(int n);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetHandleInformation(IntPtr h, int mask, int flags);
  public static int Clear() { int ok = 0; foreach (int n in new int[]{-10,-11,-12}) { IntPtr h = GetStdHandle(n); if (h != IntPtr.Zero && h != new IntPtr(-1) && SetHandleInformation(h, 1, 0)) ok++; } return ok; }
}
"@
    }
    return [NexusStdHandles]::Clear()
  } catch { return -1 }
}
function Log([string]$message) {
  Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date).ToString("s"), $message) -Encoding UTF8
}
function Test-Ollama {
  try {
    $r = Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 3
    return ($null -ne $r.PSObject.Properties["models"])
  } catch { return $false }
}
$controlLock = $null
$ollamaLock = $null
try {
  $controlLock = [IO.File]::Open((Join-Path $logDir "nexus_control.lock"),"OpenOrCreate","ReadWrite","None")
  Log "START local NEXUS; Funnel is independent"
  $ollamaLock = New-Object Threading.Mutex($false, "Local\NexusOllamaStart")
  $locked = $false
  try {
    try { $locked = $ollamaLock.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { Log "BLOCKED Ollama start already in progress"; exit 23 }
    if (Test-Ollama) {
      Log "REUSED healthy Ollama; existing process environment unchanged"
    } else {
      $listeners = @(Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue)
      $existing = @(Get-Process -Name ollama -ErrorAction SilentlyContinue)
      if ($listeners.Count -gt 0 -or $existing.Count -gt 0) {
        Log "BLOCKED existing Ollama/listener not healthy; no duplicate or restart"
        exit 21
      }
      if (-not (Test-Path -LiteralPath "G:\Ollama\models" -PathType Container)) {
        Log "ERROR existing model directory missing"; exit 21
      }
      $ollamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
      if (-not (Test-Path -LiteralPath $ollamaExe -PathType Leaf)) {
        $command = Get-Command ollama -ErrorAction Stop
        $ollamaExe = $command.Source
      }
      $oldModels = $env:OLLAMA_MODELS
      try {
        $env:OLLAMA_MODELS = "G:\Ollama\models"
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
        $process = Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "ollama-$stamp.out.log") -RedirectStandardError (Join-Path $logDir "ollama-$stamp.err.log")
        Log ("OLLAMA started once pid={0} models=G:\Ollama\models" -f $process.Id)
      } finally { $env:OLLAMA_MODELS = $oldModels }
      $deadline = (Get-Date).AddSeconds(30)
      while (-not (Test-Ollama) -and (Get-Date) -lt $deadline -and -not $process.HasExited) {
        Start-Sleep -Milliseconds 500
        $process.Refresh()
      }
      if (-not (Test-Ollama)) { Log "ERROR Ollama readiness failed; see timestamped logs"; exit 21 }
    }
  } finally { if ($locked) { $ollamaLock.ReleaseMutex() } }
  $pwsh = (Get-Process -Id $PID).Path
  & $pwsh -NoProfile -File (Join-Path $Root "start_nexus.ps1") -Root $Root
  $rc = $LASTEXITCODE
  if ($rc -ne 0) { Log ("ERROR NEXUS startup rc={0}" -f $rc); exit $rc }
  # Gateway 8300 (einmalig, kein Duplikat)
  if (-not (Get-NetTCPConnection -LocalPort 8300 -State Listen -ErrorAction SilentlyContinue)) {
    try { . (Join-Path $Root "nexus_python.ps1"); $py = Get-NexusPython -Root $Root } catch { Log ("ERROR gateway python: {0}" -f $_.Exception.Message); exit 25 }
    $gw = Join-Path $Root "nexus\bridge_gateway\gateway_allowlist.py"
    $gl = Join-Path $Root "logs\bridge"
    $null = Disable-NexusStdHandleInheritance
    $gp = Start-Process -FilePath $py -ArgumentList "`"$gw`"" -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $gl "gateway-stdout.log") -RedirectStandardError (Join-Path $gl "gateway-stderr.log")
    $gd = (Get-Date).AddSeconds(20)
    while (-not (Get-NetTCPConnection -LocalPort 8300 -State Listen -ErrorAction SilentlyContinue) -and (Get-Date) -lt $gd) { Start-Sleep -Milliseconds 500 }
    if (Get-NetTCPConnection -LocalPort 8300 -State Listen -ErrorAction SilentlyContinue) { Log ("GATEWAY started pid={0}" -f $gp.Id) } else { Log "ERROR gateway 8300 not listening" }
  } else { Log "REUSED gateway 8300" }
  Log "START local verified; no Funnel configuration changed"
  exit 0
} catch {
  Log ("ERROR start controller: {0}" -f $_.Exception.GetType().Name)
  exit 24
} finally {
  if ($ollamaLock) { $ollamaLock.Dispose() }
  if ($controlLock) { $controlLock.Dispose() }
}
