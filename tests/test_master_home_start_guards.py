"""Isolated PowerShell lifecycle contract tests; no real service control."""


def test_start_guards():
    import json
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import tempfile

    root = Path(__file__).resolve().parents[1]
    temporary_root = root / "workspace" / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    assert pwsh, "pwsh/powershell nicht gefunden"
    cases = [
        ("healthy_file_reuse", 0),
        ("healthy_env_reuse", 0),
        ("healthy_slow_core_reuse", 0),
        ("core_timeout", 13),
        ("foreign_listener", 13),
        ("unhealthy_owned", 13),
        ("core_malformed", 13),
        ("bridge_missing404", 13),
        ("bridge_unauthorized", 13),
        ("bridge_malformed", 13),
        ("bridge_wrong_protocol", 13),
        ("capabilities_malformed", 13),
        ("ping_missing", 13),
        ("ping_wrong_permission", 13),
        ("task_missing404", 13),
        ("task_failed", 13),
        ("task_bad_echo", 13),
        ("task_wrong_permission", 13),
        ("token_missing", 13),
        ("duplicate_owned", 12),
        ("launch_failure", 1),
    ]
    results = []
    for case, expected in cases:
        with tempfile.TemporaryDirectory(prefix="nexus_start_contract_", dir=temporary_root) as tmp:
            target = Path(tmp)
            # 02.11.2026: zentraler Python-Pfad (Get-NexusPython) gehoert zum Start-Vertrag
            (target / "config").mkdir()
            shutil.copy2(root / "config" / "python_path.txt", target / "config" / "python_path.txt")
            shutil.copy2(root / "nexus_python.ps1", target / "nexus_python.ps1")
            if case not in ("healthy_env_reuse", "token_missing"):
                token_file = target / "workspace" / "bridge" / ".token"
                token_file.parent.mkdir(parents=True)
                token_file.write_text("start-contract-test-token", encoding="utf-8")
            mock = r'''
    $ErrorActionPreference = "Stop"
    $case = "CASE"
    $testRoot = "TESTROOT"
    $requestLog = Join-Path $testRoot "requests.txt"
    function Get-CimInstance {
      if ($case -notin @("foreign_listener", "launch_failure")) {
        [pscustomobject]@{Name="python.exe";CommandLine=('python.exe "'+(Join-Path $testRoot "run_server.py")+'"');ProcessId=42420}
        if ($case -eq "duplicate_owned") {
          [pscustomobject]@{Name="python.exe";CommandLine=('python.exe "'+(Join-Path $testRoot "run_server.py")+'"');ProcessId=42421}
        }
      }
    }
    function Get-NetTCPConnection {
      if ($case -ne "launch_failure") {
        [pscustomobject]@{OwningProcess=42420;LocalPort=8299}
      }
    }
    function Invoke-RestMethod {
      param([string]$Uri, [hashtable]$Headers, [int]$TimeoutSec, [string]$Method = "GET", [string]$ContentType, [string]$Body)
      $Method = $Method.ToUpperInvariant()
      $path = ([uri]$Uri).AbsolutePath
      Add-Content -LiteralPath $requestLog -Value "$Method $path"
      if ($Method -eq "GET" -and $path -eq "/api/health") {
        if ($case -eq "healthy_slow_core_reuse" -and ($TimeoutSec -lt 15 -or $TimeoutSec -gt 30)) {
          throw "core timeout does not allow the observed slow healthy response"
        }
        if ($case -eq "core_timeout") { throw [TimeoutException]::new("simulated bounded core timeout") }
        if ($case -eq "unhealthy_owned") { throw "simulated unhealthy server" }
        if ($case -eq "core_malformed") { return @{status="unexpected"} }
        return @{ok=$true}
      }
      if ($Headers.Authorization -ne "Bearer start-contract-test-token") {
        Add-Content -LiteralPath $requestLog -Value "AUTH_HEADER_INVALID"
        throw "AUTH_HEADER_INVALID"
      }
      if ($Method -eq "GET" -and $path -eq "/api/bridge/health") {
        if ($case -eq "bridge_missing404") { throw "simulated HTTP 404" }
        if ($case -eq "bridge_unauthorized") { throw "simulated HTTP 401" }
        $health = @{success=$true;ok=$true;protocol="NEXUS_AGENT_BRIDGE_PROTOCOL";marker="NEXUS_AGENT_BRIDGE_V1"}
        if ($case -eq "bridge_malformed") { $health.marker = "WRONG_MARKER" }
        if ($case -eq "bridge_wrong_protocol") { $health.protocol = "OTHER_PROTOCOL" }
        return $health
      }
      if ($Method -eq "GET" -and $path -eq "/api/capabilities") {
        $caps = @{success=$true;protocol="NEXUS_AGENT_BRIDGE_PROTOCOL";schema="nexus.bridge.capabilities.v1";tasks=@(@{name="ping";permission="READ_ONLY"})}
        if ($case -eq "capabilities_malformed") { $caps.schema = "other.schema" }
        if ($case -eq "ping_missing") { $caps.tasks = @(@{name="other";permission="READ_ONLY"}) }
        if ($case -eq "ping_wrong_permission") { $caps.tasks[0].permission = "PRIVILEGED" }
        return $caps
      }
      if ($Method -eq "POST" -and $path -eq "/api/task") {
        $payload = $Body | ConvertFrom-Json
        if ($ContentType -ne "application/json" -or
            @($payload.PSObject.Properties.Name).Count -ne 2 -or
            @($payload.PSObject.Properties.Name | Where-Object { $_ -notin @("task", "params") }).Count -ne 0 -or
            $payload.task -ne "ping" -or
            @($payload.params.PSObject.Properties.Name).Count -ne 1 -or
            @($payload.params.PSObject.Properties.Name)[0] -ne "echo" -or
            $payload.params.echo -ne "nexus-start-health") {
          Add-Content -LiteralPath $requestLog -Value "UNMOCKED_HTTP_FORBIDDEN"
          throw "UNMOCKED_HTTP_FORBIDDEN"
        }
        if ($case -eq "task_missing404") { throw "simulated HTTP 404" }
        $task = @{success=$true;task=@{task="ping";status="succeeded";permission="READ_ONLY";result=@{pong=$true;echo="nexus-start-health"}}}
        if ($case -eq "task_failed") { $task.success=$false; $task.task.status="failed" }
        if ($case -eq "task_bad_echo") { $task.task.result.echo="stale-or-wrong-response" }
        if ($case -eq "task_wrong_permission") { $task.task.permission="PRIVILEGED" }
        return $task
      }
      Add-Content -LiteralPath $requestLog -Value "UNMOCKED_HTTP_FORBIDDEN"
      throw "UNMOCKED_HTTP_FORBIDDEN"
    }
    function Invoke-WebRequest { throw "UNMOCKED_HTTP_FORBIDDEN" }
    function Start-Process { throw "simulated start failure; real process launch forbidden in this test" }
    function Stop-Process { throw "PROCESS_TERMINATION_FORBIDDEN" }
    function taskkill.exe { throw "PROCESS_TERMINATION_FORBIDDEN" }
    & ([scriptblock]::Create([IO.File]::ReadAllText("SOURCE"))) -Root $testRoot
    '''.replace("CASE", case).replace("TESTROOT", str(target)).replace("SOURCE", str(root / "start_nexus.ps1"))
            fixture = target / "case.ps1"
            fixture.write_text(mock, encoding="utf-8")
            child_env = os.environ.copy()
            child_env.pop("NEXUS_BRIDGE_TOKEN", None)
            if case == "healthy_env_reuse":
                child_env["NEXUS_BRIDGE_TOKEN"] = "start-contract-test-token"
            child_env.update(TEMP=str(target), TMP=str(target), PYTHONDONTWRITEBYTECODE="1")
            proc = subprocess.run(
                [pwsh, "-NoProfile", "-File", str(fixture)],
                capture_output=True, text=True, timeout=35, env=child_env,
            )
            logs = "\n".join(
                p.read_text(encoding="utf-8-sig") for p in (target / "workspace/logs").glob("*.log")
            )
            request_log = target / "requests.txt"
            requests = request_log.read_text(encoding="utf-8-sig").splitlines() if request_log.is_file() else []
            output = proc.stdout + proc.stderr + logs
            passed = proc.returncode == expected and not any(
                marker in output or marker in requests
                for marker in ("PROCESS_TERMINATION_FORBIDDEN", "UNMOCKED_HTTP_FORBIDDEN", "AUTH_HEADER_INVALID")
            )
            if case in ("healthy_file_reuse", "healthy_env_reuse", "healthy_slow_core_reuse"):
                passed = passed and requests == [
                    "GET /api/health", "GET /api/bridge/health", "GET /api/capabilities", "POST /api/task",
                ]
                passed = passed and "REUSED existing NEXUS" in logs
            else:
                passed = passed and "REUSED existing NEXUS" not in logs
                passed = passed and "NEXUS ONLINE" not in output
            results.append({
                "case": case, "expected_exit": expected, "actual_exit": proc.returncode,
                "pass": passed, "requests": requests, "output": proc.stdout.strip(), "error": proc.stderr.strip(),
            })
    print(json.dumps({
        "tests": results, "passed": sum(r["pass"] for r in results), "total": len(results),
    }, ensure_ascii=False, indent=2))
    assert all(r["pass"] for r in results), results


def test_health_deadline_guards():
    """Exercise the actual health function with synthetic responses and budgets."""
    import json
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import tempfile

    root = Path(__file__).resolve().parents[1]
    temporary_root = root / "workspace" / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    assert pwsh, "pwsh/powershell nicht gefunden"
    with tempfile.TemporaryDirectory(prefix="nexus_health_deadline_", dir=temporary_root) as tmp:
        target = Path(tmp)
        fixture = target / "health_deadline.ps1"
        fixture.write_text(r'''
$ErrorActionPreference = "Stop"
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile("SOURCE", [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw "source parse failed" }
$definitions = @($ast.FindAll({
  param($node)
  $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -in @("Get-BridgeToken", "Test-NexusHealth")
}, $true))
if ($definitions.Count -ne 2) { throw "health function definitions missing" }
foreach ($definition in $definitions) { Invoke-Expression $definition.Extent.Text }
$Root = "TESTROOT"
$Port = 8299
$coreHealthTimeoutSec = 30
$env:NEXUS_BRIDGE_TOKEN = "start-contract-test-token"
$script:requestTimeouts = [System.Collections.Generic.List[int]]::new()
function Invoke-RestMethod {
  param([string]$Uri, [hashtable]$Headers, [int]$TimeoutSec, [string]$Method = "GET", [string]$ContentType, [string]$Body)
  $script:requestTimeouts.Add($TimeoutSec)
  $path = ([uri]$Uri).AbsolutePath
  if ($TimeoutSec -lt 1) { throw "request without remaining time" }
  if ($path -eq "/api/health") { return @{ok=$true} }
  if ($Headers.Authorization -ne "Bearer start-contract-test-token") { throw "invalid synthetic auth" }
  if ($path -eq "/api/bridge/health") {
    return @{success=$true;ok=$true;protocol="NEXUS_AGENT_BRIDGE_PROTOCOL";marker="NEXUS_AGENT_BRIDGE_V1"}
  }
  if ($path -eq "/api/capabilities") {
    return @{success=$true;protocol="NEXUS_AGENT_BRIDGE_PROTOCOL";schema="nexus.bridge.capabilities.v1";tasks=@(@{name="ping";permission="READ_ONLY"})}
  }
  if ($path -eq "/api/task" -and $Method -eq "Post") {
    return @{success=$true;task=@{task="ping";status="succeeded";permission="READ_ONLY";result=@{pong=$true;echo="nexus-start-health"}}}
  }
  throw "unmocked HTTP forbidden"
}
function Invoke-WebRequest { throw "unmocked HTTP forbidden" }
$readinessBudgetSec = 0
$zeroBudgetResult = Test-NexusHealth
$zeroBudgetCalls = $script:requestTimeouts.Count
if ($zeroBudgetResult -ne $false -or $zeroBudgetCalls -ne 0) { throw "exhausted budget issued a request" }
$readinessBudgetSec = 2
$shortBudgetResult = Test-NexusHealth
$shortBudgetTimeouts = @($script:requestTimeouts)
if ($shortBudgetResult -ne $true -or $shortBudgetTimeouts.Count -ne 4) { throw "short healthy check failed" }
if (@($shortBudgetTimeouts | Where-Object { $_ -lt 1 -or $_ -gt 2 }).Count -ne 0) {
  throw "request timeout exceeds remaining readiness budget"
}
@{zero_budget_result=$zeroBudgetResult;zero_budget_calls=$zeroBudgetCalls;short_budget_result=$shortBudgetResult;short_budget_timeouts=$shortBudgetTimeouts} | ConvertTo-Json -Compress
'''.replace("SOURCE", str(root / "start_nexus.ps1")).replace("TESTROOT", str(target)), encoding="utf-8")
        child_env = os.environ.copy()
        child_env.update(TEMP=str(target), TMP=str(target), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [pwsh, "-NoProfile", "-File", str(fixture)],
            capture_output=True, text=True, timeout=15, env=child_env,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        result = json.loads(proc.stdout)
        assert result["zero_budget_result"] is False
        assert result["zero_budget_calls"] == 0
        assert result["short_budget_result"] is True
        assert len(result["short_budget_timeouts"]) == 4
        assert all(1 <= timeout <= 2 for timeout in result["short_budget_timeouts"])
        print(json.dumps({"health_deadline_guards": result, "pass": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_start_guards()
    test_health_deadline_guards()
