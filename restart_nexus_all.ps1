param([string]$Root = $PSScriptRoot, [switch]$AllowLegacyIdleStop)
$ErrorActionPreference = "Stop"
$env:TEMP = "G:\meine-ki\nexus-ai\workspace\tmp"; $env:TMP = $env:TEMP; New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null  # C: tabu (20261028)
$pwsh = (Get-Process -Id $PID).Path
$stopArgs = @("-NoProfile", "-File", (Join-Path $Root "stop_nexus_all.ps1"), "-Root", $Root)
if ($AllowLegacyIdleStop) { $stopArgs += "-AllowLegacyIdleStop" }
& $pwsh @stopArgs
$rc = $LASTEXITCODE
if ($rc -ne 0) {
  Write-Output ("BLOCKED restart: safe stop did not complete, rc={0}; running work preserved" -f $rc)
  exit $rc
}
& $pwsh -NoProfile -File (Join-Path $Root "start_nexus_all.ps1") -Root $Root
$startRc = $LASTEXITCODE
# MH01 20261028: Health-Watchdog mitstarten (Einzelinstanz per Lock, BelowNormal, Logs auf G:)
try {
  $hwRun = @(Get-CimInstance Win32_Process -Filter "name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "health_watch\.py" })
  if ($hwRun.Count -eq 0) {
    try { . (Join-Path $Root "nexus_python.ps1"); $hwPy = Get-NexusPython -Root $Root } catch { Write-Output ("ERROR health_watch nicht gestartet: {0}" -f $_.Exception.Message); if ($startRc -eq 0) { $startRc = 26 }; throw }
    # Stopfix 02.11.2026: abgekoppelt ueber Win32_Process.Create (erbt keine Handles des Aufrufers -> restart kehrt zurueck)
    $si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0; PriorityClass = [uint32]16384 }
    $hwCmd = '"{0}" -B "{1}"' -f $hwPy, (Join-Path $Root 'nexus\health_watch.py')
    $hwr = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $hwCmd; CurrentDirectory = $Root; ProcessStartupInformation = $si }
    if ($hwr.ReturnValue -ne 0) { Write-Output ("ERROR health_watch Start ReturnValue={0}" -f $hwr.ReturnValue); if ($startRc -eq 0) { $startRc = 27 } }
    else { Write-Output ("health_watch gestartet pid={0}" -f $hwr.ProcessId) }
  }
} catch { Write-Output ("ERROR health_watch: {0}" -f $_.Exception.Message); if ($startRc -eq 0) { $startRc = 26 } }
exit $startRc
