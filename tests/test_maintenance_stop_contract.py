"""Isolated contract tests for the NEXUS safe-stop flow (stop_nexus.ps1).

The real server is never contacted and no real process is terminated. A mocked
PowerShell environment emulates:
  * Get-CimInstance / Get-NetTCPConnection ownership + listener detection
  * the loopback-only maintenance endpoints (drain / status / shutdown / cancel)
  * the authenticated bridge task list used by the legacy idle path

Key safety property under test: the normal drain path and every BLOCKED path
never call Stop-Process / taskkill.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(r"G:\meine-ki\nexus-ai")

MOCK = r'''
$ErrorActionPreference="Stop"
$case="CASE"
$testRoot="TESTROOT"
$stateFile = Join-Path $testRoot "state.txt"
Set-Content -LiteralPath $stateFile -Value "live" -NoNewline
$env:NEXUS_BRIDGE_TOKEN = $null

function Get-CimInstance {
  if ($case -in @("stop_empty","stop_foreign")) { return }
  if ((Get-Content -LiteralPath $stateFile -Raw) -eq "stopped") { return }
  [pscustomobject]@{Name="python.exe";CommandLine=('python.exe '+(Join-Path $testRoot "run_server.py"));ProcessId=42420}
  if ($case -eq "stop_duplicate") {
    [pscustomobject]@{Name="python.exe";CommandLine=('python.exe '+(Join-Path $testRoot "run_server.py"));ProcessId=42421}
  }
}
function Get-NetTCPConnection {
  if ($case -eq "stop_empty") { return }
  if ($case -eq "stop_foreign") { [pscustomobject]@{OwningProcess=9999;LocalPort=8299}; return }
  if ((Get-Content -LiteralPath $stateFile -Raw) -ne "stopped") { [pscustomobject]@{OwningProcess=42420;LocalPort=8299} }
}
function SimThrow([int]$code) {
  $ex = New-Object System.Exception "simulated http error"
  $resp = New-Object psobject
  $resp | Add-Member -MemberType NoteProperty -Name StatusCode -Value $code
  $ex | Add-Member -MemberType NoteProperty -Name Response -Value $resp
  throw $ex
}
function Invoke-RestMethod {
  param($Uri,$Method,$Headers,$ContentType,$Body,$TimeoutSec)
  if ($case -eq "stop_unauthorized") { SimThrow 401 }
  if ($Uri -like "*/maintenance/drain") {
    if ($case -in @("stop_legacy_blocked","stop_legacy_active")) { SimThrow 404 }
    return @{ success=$true; maintenance=@{state="DRAINING";in_flight=0} }
  }
  if ($Uri -like "*/maintenance/cancel") { return @{ success=$true; maintenance=@{state="RUNNING"} } }
  if ($Uri -like "*/maintenance/shutdown") { Set-Content -LiteralPath $stateFile -Value "stopped" -NoNewline; return @{ success=$true; shutdown=@{ok=$true} } }
  if ($Uri -like "*/api/bridge/tasks*") {
    if ($case -eq "stop_legacy_active") { return @{ tasks=@(@{status="running"}) } }
    return @{ tasks=@() }
  }
  if ($Uri -like "*/maintenance") {
    if ($case -eq "stop_timeout") { return @{ success=$true; maintenance=@{state="DRAINING";in_flight=2} } }
    return @{ success=$true; maintenance=@{state="DRAINED";in_flight=0} }
  }
  return @{ ok=$true }
}
function Start-Process { throw "START_FORBIDDEN" }
function Stop-Process { throw "PROCESS_TERMINATION_FORBIDDEN" }
function taskkill.exe { throw "PROCESS_TERMINATION_FORBIDDEN" }
& ([scriptblock]::Create([IO.File]::ReadAllText("SOURCE"))) -Root $testRoot ARGS
'''

CASES = [
    # case, expected exit, extra args, token present
    ("stop_empty", 0, "", True),
    ("stop_drained", 0, "", True),
    ("stop_timeout", 30, "-DrainTimeoutSec 2", True),
    ("stop_unauthorized", 31, "", True),
    ("stop_no_token", 31, "", False),
    ("stop_foreign", 13, "", True),
    ("stop_duplicate", 12, "", True),
    ("stop_legacy_blocked", 30, "", True),
    ("stop_legacy_active", 30, "-AllowLegacyIdleStop", True),
]


def _pwsh() -> str:
    candidates = [
        r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.6.0_x64__8wekyb3d8bbwe\pwsh.exe",
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        "pwsh",
    ]
    for c in candidates:
        if c == "pwsh" or Path(c).is_file():
            return c
    return "pwsh"


def test_stop_contract():
    pwsh = _pwsh()
    results = []
    for case, expected, extra, token in CASES:
        with tempfile.TemporaryDirectory(prefix="nexus_stop_contract_") as tmp:
            target = Path(tmp)
            if token:
                tokdir = target / "workspace" / "bridge"
                tokdir.mkdir(parents=True, exist_ok=True)
                (tokdir / ".token").write_text("test-token-0123456789abcdef", encoding="utf-8")

            fixture_src = (
                MOCK.replace("CASE", case)
                .replace("TESTROOT", str(target))
                .replace("SOURCE", str(ROOT / "stop_nexus.ps1"))
                .replace("ARGS", extra)
            )
            fixture = target / "case.ps1"
            fixture.write_text(fixture_src, encoding="utf-8")

            proc = subprocess.run(
                [pwsh, "-NoProfile", "-File", str(fixture)],
                capture_output=True, text=True, timeout=60,
            )
            logs = "\n".join(
                p.read_text(encoding="utf-8-sig")
                for p in (target / "workspace" / "logs").glob("*.log")
            )
            combined = proc.stdout + proc.stderr + logs
            passed = proc.returncode == expected and "PROCESS_TERMINATION_FORBIDDEN" not in combined
            results.append({
                "case": case, "expected_exit": expected, "actual_exit": proc.returncode,
                "pass": passed, "output": proc.stdout.strip(), "error": proc.stderr.strip(),
            })

    print(json.dumps({"tests": results, "passed": sum(r["pass"] for r in results), "total": len(results)},
                     ensure_ascii=False, indent=2))
    assert all(r["pass"] for r in results), results


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
