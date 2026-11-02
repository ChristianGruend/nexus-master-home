# Unified NEXUS status: origin + gateway + funnel + modules. No secrets.
$ErrorActionPreference = "Continue"
$Root = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$TsExe = "C:\Program Files\Tailscale\tailscale.exe"
$State = Join-Path $Root "workspace\bridge"
$UrlFile = Join-Path $State "tunnel.url"

function Probe([string]$Url) {
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 4
    return [int]$r.StatusCode
  } catch {
    if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
    return -1
  }
}

$origin = Probe "http://127.0.0.1:8299/api/health"
$gw = Probe "http://127.0.0.1:8300/api/bridge/health"
$bridge = Probe "http://127.0.0.1:8299/api/bridge/health"
$url = ""
if (Test-Path $UrlFile) { $url = ([IO.File]::ReadAllText($UrlFile)).Trim() }
$ts = "missing"; $dns=""
if (Test-Path $TsExe) {
  try {
    $j = & $TsExe status --json 2>$null | ConvertFrom-Json
    $ts = $j.BackendState
    if ($j.Self) { $dns = [string]$j.Self.DNSName }
  } catch { $ts = "error" }
}
$funnel = ""
if (Test-Path $TsExe) { $funnel = (& $TsExe funnel status 2>&1 | Out-String) }
$cf = [bool](Get-Process cloudflared -ErrorAction SilentlyContinue)

Write-Host "NEXUS_STATUS_UNIFIED"
Write-Host ("ORIGIN_8299=" + $(if($origin -in 200){'UP'}else{"DOWN($origin)"}))
Write-Host ("BRIDGE_AUTH_PROBE=" + $(if($bridge -in 200,401){"OK($bridge)"}else{"FAIL($bridge)"}))
Write-Host ("GATEWAY_8300=" + $(if($gw -in 200,401){"UP($gw)"}else{"DOWN($gw)"}))
Write-Host ("TAILSCALE=$ts dns=$dns")
Write-Host ("PUBLIC_URL=$url")
Write-Host ("CLOUDFLARED=$cf (must be False)")
Write-Host ("COST=LOCAL->FREE->NOT")
Write-Host "---- funnel ----"
Write-Host $funnel
exit 0
