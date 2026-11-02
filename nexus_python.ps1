# Zentraler NEXUS-Python (Nexus-Tools, 02.11.2026). Per Dot-Sourcing laden:
#   . (Join-Path $Root 'nexus_python.ps1'); $py = Get-NexusPython -Root $Root
# Liest config\python_path.txt, prueft exe + "import psutil, PIL, edge_tts".
# Bei Fehler: Log-Eintrag + throw. KEIN Ausweichen auf py/python/WindowsApps.
function Get-NexusPython {
  param(
    [string]$Root = 'G:\meine-ki\nexus-ai',
    [string]$ConfigPath = '',
    [string]$LogFile = '',
    [switch]$SkipImportCheck
  )
  if (-not $ConfigPath) { $ConfigPath = Join-Path $Root 'config\python_path.txt' }
  if (-not $LogFile) { $LogFile = Join-Path $Root 'workspace\logs\nexus_python.log' }
  function _npLog([string]$m) {
    try {
      New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
      Add-Content -LiteralPath $LogFile -Value ("[{0}] [pid {1}] {2}" -f (Get-Date -f s), $PID, $m) -Encoding UTF8
    } catch { }
  }
  function _npFail([string]$m) { _npLog ("ERROR Get-NexusPython: " + $m); throw ("NEXUS-PYTHON FEHLER: " + $m) }
  if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { _npFail "Config fehlt: $ConfigPath" }
  $py = (@(Get-Content -LiteralPath $ConfigPath -ErrorAction Stop | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') }) | Select-Object -First 1)
  if (-not $py) { _npFail "Config leer: $ConfigPath" }
  $py = $py.Trim('"')
  if ($py -match '(?i)\\WindowsApps\\' -or $py -match '(?i)(^|\\)py\.exe$' -or -not [IO.Path]::IsPathRooted($py)) { _npFail "Unzulaessiger Interpreter (absoluter Pfad noetig, kein py/WindowsApps): $py" }
  if (-not (Test-Path -LiteralPath $py -PathType Leaf)) { _npFail "python.exe existiert nicht: $py" }
  if (-not $SkipImportCheck) {
    $out = & $py -B -c "import sys, psutil, PIL, edge_tts; print(sys.version.split()[0])" 2>&1
    if ($LASTEXITCODE -ne 0) { _npFail ("Import-Check (psutil, PIL, edge_tts) fehlgeschlagen rc={0} mit {1}: {2}" -f $LASTEXITCODE, $py, (($out | Out-String).Trim() -split "`n" | Select-Object -Last 1)) }
    _npLog ("OK {0} (Python {1})" -f $py, (($out | Out-String).Trim()))
  }
  return $py
}