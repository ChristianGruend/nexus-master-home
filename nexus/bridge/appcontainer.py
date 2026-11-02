"""Windows-AppContainer-Sandbox für selbst erzeugten Code (keine Downloads, keine Admin-Rechte).

Grenzen, die das Betriebssystem erzwingt (nicht Python-interne Guards):

  - AppContainer-Token ohne Capabilities: kein Netzwerk (auch kein Loopback zur NEXUS-
    Bridge), kein Zugriff auf Dateien, die nicht ausdrücklich für genau diese
    Container-SID freigegeben sind (Benutzerprofil, Tokens, NEXUS-Quellcode bleiben zu).
  - Freigegeben sind nur: Lesen/Ausführen der Python-Laufzeit und Schreiben im
    jeweiligen Lauf-Verzeichnis unter workspace/sandbox/runs/<id> (eine Kopie der
    Projektdateien, nie das Original).
  - Job Object: Speicherlimit, Prozesslimit, CPU-Zeitlimit, KILL_ON_JOB_CLOSE,
    UI-Beschränkungen; dazu ein Wanduhr-Timeout.

Die Isolation wird nicht behauptet, sondern per self_test() mit echten Ausbruchsversuchen
(Lesen eines Geheimnisses, Schreiben außerhalb, Netzwerk, Speicher, Laufzeit) belegt.
Schlägt der Selbsttest fehl, wird kein Code ausgeführt.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SANDBOX_DIR = ROOT / "workspace" / "sandbox"
RUNS_DIR = SANDBOX_DIR / "runs"
PROFILE = "nexus.hermes.sandbox"
SELFTEST_FILE = SANDBOX_DIR / "selftest.json"
SELFTEST_MAX_AGE = 24 * 3600
DEFAULT_MEMORY_MB = 512
DEFAULT_PROCESSES = 4
DEFAULT_CPU_SECONDS = 30
DEFAULT_WALL_SECONDS = 40
MAX_OUTPUT = 40_000

_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
_adv = ctypes.WinDLL("advapi32", use_last_error=True) if os.name == "nt" else None
_ue = ctypes.WinDLL("userenv", use_last_error=True) if os.name == "nt" else None

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
STARTF_USESTDHANDLES = 0x00000100
JobObjectBasicUIRestrictions = 4
JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_UILIMIT_ALL = 0x000000FF
WAIT_TIMEOUT = 0x00000102
HRESULT_ALREADY_EXISTS = -2147024713  # 0x800700B7


def _declare():
    """Exakte Win32-Signaturen: ohne sie kürzt ctypes 64-Bit-Handles und Zeiger."""
    if os.name != "nt":
        return
    H, P, D, B, S = wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL, ctypes.c_size_t
    sigs = {
        (_k32, "CreateJobObjectW"): (H, [P, wintypes.LPCWSTR]),
        (_k32, "SetInformationJobObject"): (B, [H, ctypes.c_int, P, D]),
        (_k32, "AssignProcessToJobObject"): (B, [H, H]),
        (_k32, "TerminateJobObject"): (B, [H, ctypes.c_uint]),
        (_k32, "InitializeProcThreadAttributeList"): (B, [P, D, D, ctypes.POINTER(S)]),
        (_k32, "UpdateProcThreadAttribute"): (B, [P, D, S, P, S, P, P]),
        (_k32, "DeleteProcThreadAttributeList"): (None, [P]),
        (_k32, "CreateProcessW"): (B, [wintypes.LPCWSTR, wintypes.LPWSTR, P, P, B, D, P, wintypes.LPCWSTR, P, P]),
        (_k32, "ResumeThread"): (D, [H]),
        (_k32, "WaitForSingleObject"): (D, [H, D]),
        (_k32, "GetExitCodeProcess"): (B, [H, ctypes.POINTER(D)]),
        (_k32, "TerminateProcess"): (B, [H, ctypes.c_uint]),
        (_k32, "CloseHandle"): (B, [H]),
        (_k32, "LocalFree"): (P, [P]),
        (_adv, "ConvertSidToStringSidW"): (B, [P, ctypes.POINTER(wintypes.LPWSTR)]),
        (_ue, "CreateAppContainerProfile"): (ctypes.c_long, [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                                            P, D, ctypes.POINTER(P)]),
        (_ue, "DeriveAppContainerSidFromAppContainerName"): (ctypes.c_long, [wintypes.LPCWSTR, ctypes.POINTER(P)]),
    }
    for (lib, name), (res, args) in sigs.items():
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = res, args


_declare()


class SandboxUnavailable(Exception):
    """Statischer Grund, warum keine geprüfte Isolation besteht."""


class SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [("AppContainerSid", ctypes.c_void_p), ("Capabilities", ctypes.c_void_p),
                ("CapabilityCount", wintypes.DWORD), ("Reserved", wintypes.DWORD)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _check(ok, code):
    if not ok:
        raise SandboxUnavailable(code + ":" + str(ctypes.get_last_error()))


def _container_sid() -> tuple[ctypes.c_void_p, str]:
    if os.name != "nt":
        raise SandboxUnavailable("APPCONTAINER_REQUIRES_WINDOWS")
    sid = ctypes.c_void_p()
    hr = _ue.CreateAppContainerProfile(PROFILE, "NEXUS Hermes Sandbox", "Isolated test runs for Hermes code",
                                       None, 0, ctypes.byref(sid))
    if hr == HRESULT_ALREADY_EXISTS:
        hr = _ue.DeriveAppContainerSidFromAppContainerName(PROFILE, ctypes.byref(sid))
    if hr != 0 or not sid:
        raise SandboxUnavailable("APPCONTAINER_PROFILE_FAILED:" + hex(hr & 0xFFFFFFFF))
    text = wintypes.LPWSTR()
    _check(_adv.ConvertSidToStringSidW(sid, ctypes.byref(text)), "SID_TO_STRING_FAILED")
    value = text.value
    _k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    return sid, value


def _grant(path: Path, sid_text: str, rights: str) -> None:
    """Exakt einen ACL-Eintrag für die Container-SID setzen (nur eigene Pfade, kein Admin)."""
    out = subprocess.run(["icacls", str(path), "/grant", "*" + sid_text + ":" + rights, "/Q"],
                         capture_output=True, text=True, timeout=120,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if out.returncode != 0:
        raise SandboxUnavailable("ACL_GRANT_FAILED:" + path.name)


def _python_runtime() -> Path:
    return Path(sys.base_prefix)


def prepare() -> str:
    """Profil anlegen, Laufzeit (nur RX) und Sandbox-Wurzel (Modify) für die Container-SID freigeben."""
    _, sid_text = _container_sid()
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    marker = SANDBOX_DIR / ".acl_ready"
    want = sid_text + "|" + str(_python_runtime())
    if not marker.is_file() or marker.read_text(encoding="utf-8") != want:
        _grant(_python_runtime(), sid_text, "(OI)(CI)RX")
        _grant(RUNS_DIR, sid_text, "(OI)(CI)M")
        marker.write_text(want, encoding="utf-8")
    return sid_text


def _job(memory_mb: int, processes: int, cpu_seconds: int):
    job = _k32.CreateJobObjectW(None, None)
    _check(job, "JOB_CREATE_FAILED")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    basic = info.BasicLimitInformation
    basic.LimitFlags = (JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                        | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
                        | JOB_OBJECT_LIMIT_PROCESS_TIME)
    basic.ActiveProcessLimit = processes
    basic.PerProcessUserTimeLimit = cpu_seconds * 10_000_000
    info.ProcessMemoryLimit = memory_mb * 1024 * 1024
    _check(_k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info),
                                        ctypes.sizeof(info)), "JOB_LIMIT_FAILED")
    ui = wintypes.DWORD(JOB_OBJECT_UILIMIT_ALL)
    _check(_k32.SetInformationJobObject(job, JobObjectBasicUIRestrictions, ctypes.byref(ui),
                                        ctypes.sizeof(ui)), "JOB_UI_LIMIT_FAILED")
    return job


def _environment(run_dir: Path) -> ctypes.Array:
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"), "PYTHONIOENCODING": "utf-8",
           "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "TEMP": str(run_dir), "TMP": str(run_dir),
           "NEXUS_SANDBOX_ACTIVE": "appcontainer",
           # Windows schreibt diese Profilvariablen für AppContainer-Prozesse um und verlangt sie
           # (sonst ERROR_ENVVAR_NOT_FOUND). Sie zeigen nur ins Lauf-Verzeichnis; keine Geheimnisse.
           "USERPROFILE": str(run_dir), "LOCALAPPDATA": str(run_dir), "APPDATA": str(run_dir),
           "HOMEDRIVE": str(run_dir)[:2], "HOMEPATH": str(run_dir)[2:],
           "PATH": str(_python_runtime()) + ";" + os.environ.get("SYSTEMROOT", r"C:\Windows") + r"\System32"}
    block = "".join(k + "=" + v + "\0" for k, v in sorted(env.items())) + "\0"
    return ctypes.create_unicode_buffer(block, len(block))


def run(argv: list[str], run_dir: Path, *, memory_mb: int = DEFAULT_MEMORY_MB,
        processes: int = DEFAULT_PROCESSES, cpu_seconds: int = DEFAULT_CPU_SECONDS,
        wall_seconds: int = DEFAULT_WALL_SECONDS) -> dict:
    """argv im AppContainer + Job Object ausführen. run_dir muss unter RUNS_DIR liegen."""
    run_dir = Path(run_dir).resolve()
    if RUNS_DIR.resolve() not in run_dir.parents:
        raise SandboxUnavailable("RUN_DIR_OUTSIDE_SANDBOX")
    sid, _ = _container_sid()
    out_path, err_path = run_dir / ".stdout", run_dir / ".stderr"
    files = [open(out_path, "wb"), open(err_path, "wb"), open(os.devnull, "rb")]
    handles = [ctypes.c_void_p(__import__("msvcrt").get_osfhandle(f.fileno())) for f in files]
    for h in handles:
        os.set_handle_inheritable(h.value, True)
    caps = SECURITY_CAPABILITIES(sid, None, 0, 0)
    size = ctypes.c_size_t()
    _k32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
    attrs = ctypes.create_string_buffer(size.value)
    _check(_k32.InitializeProcThreadAttributeList(attrs, 2, 0, ctypes.byref(size)), "ATTR_INIT_FAILED")
    handle_list = (ctypes.c_void_p * 3)(*[h.value for h in handles])
    job = proc = None
    try:
        _check(_k32.UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                                              ctypes.byref(caps), ctypes.sizeof(caps), None, None),
               "ATTR_CAPS_FAILED")
        _check(_k32.UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, ctypes.cast(handle_list, ctypes.c_void_p),
                                              ctypes.sizeof(handle_list), None, None), "ATTR_HANDLES_FAILED")
        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(si)
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        si.StartupInfo.hStdOutput, si.StartupInfo.hStdError, si.StartupInfo.hStdInput = (h.value for h in handles)
        si.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)
        pi = PROCESS_INFORMATION()
        cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        job = _job(memory_mb, processes, cpu_seconds)
        env = _environment(run_dir)
        _check(_k32.CreateProcessW(None, cmd, None, None, True,
                                   EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED | CREATE_NO_WINDOW
                                   | CREATE_UNICODE_ENVIRONMENT, ctypes.cast(env, ctypes.c_void_p), str(run_dir),
                                   ctypes.cast(ctypes.byref(si), ctypes.c_void_p), ctypes.cast(ctypes.byref(pi), ctypes.c_void_p)), "CREATE_PROCESS_FAILED")
        proc = pi.hProcess
        # Kein Zielcode läuft, bevor die Job-Grenze steht (Start suspendiert).
        if not _k32.AssignProcessToJobObject(job, proc):
            _k32.TerminateProcess(proc, 1)
            raise SandboxUnavailable("JOB_ASSIGN_FAILED")
        _k32.ResumeThread(pi.hThread)
        _k32.CloseHandle(pi.hThread)
        started = time.monotonic()
        timed_out = _k32.WaitForSingleObject(proc, int(wall_seconds * 1000)) == WAIT_TIMEOUT
        if timed_out:
            _k32.TerminateJobObject(job, 124)
            _k32.WaitForSingleObject(proc, 5000)
        code = wintypes.DWORD()
        _k32.GetExitCodeProcess(proc, ctypes.byref(code))
        duration = round(time.monotonic() - started, 3)
    finally:
        for f in files:
            f.close()
        _k32.DeleteProcThreadAttributeList(attrs)
        if proc:
            _k32.CloseHandle(proc)
        if job:
            _k32.CloseHandle(job)  # KILL_ON_JOB_CLOSE beendet verbliebene Kindprozesse
    read = lambda p: p.read_bytes()[:MAX_OUTPUT].decode("utf-8", "replace") if p.is_file() else ""
    return {"returncode": ctypes.c_int32(code.value).value, "timed_out": timed_out, "duration_s": duration,
            "stdout": read(out_path), "stderr": read(err_path), "containment": "windows_appcontainer+job_object",
            "network": "denied_no_capabilities", "filesystem": "run_dir_only",
            "limits": {"memory_mb": memory_mb, "processes": processes, "cpu_seconds": cpu_seconds,
                       "wall_seconds": wall_seconds}}


def new_run_dir() -> Path:
    prepare()
    path = RUNS_DIR / ("RUN-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    path.mkdir(parents=True)
    return path


def cleanup(run_dir: Path) -> None:
    run_dir = Path(run_dir)
    if RUNS_DIR.resolve() in run_dir.resolve().parents:
        shutil.rmtree(run_dir, ignore_errors=True)


_PROBE = r'''
import json, os, socket, sys
res = {}
def attempt(name, fn):
    try:
        fn(); res[name] = "ALLOWED"
    except BaseException as exc:
        res[name] = "DENIED:" + type(exc).__name__
attempt("read_secret", lambda: open(sys.argv[1], "rb").read(1))
attempt("read_user_profile", lambda: os.listdir(sys.argv[2]))
attempt("write_outside", lambda: open(sys.argv[3], "wb").write(b"x"))
attempt("read_nexus_source", lambda: open(sys.argv[4], "rb").read(1))
attempt("write_run_dir", lambda: open("inside.txt", "w").write("ok"))
def net(host, port):
    s = socket.create_connection((host, port), timeout=3); s.close()
attempt("net_loopback_bridge", lambda: net("127.0.0.1", 8299))
attempt("net_external", lambda: net("1.1.1.1", 443))
print(json.dumps(res))
'''


def self_test(force: bool = False) -> dict:
    """Isolation mit echten Ausbruchsversuchen belegen; Ergebnis wird befristet gespeichert."""
    if not force and SELFTEST_FILE.is_file():
        try:
            cached = json.loads(SELFTEST_FILE.read_text(encoding="utf-8"))
            if cached.get("ok") and time.time() - cached.get("ts", 0) < SELFTEST_MAX_AGE:
                return cached
        except (OSError, ValueError):
            pass
    result = {"ok": False, "ts": time.time(), "checks": {}}
    try:
        run_dir = new_run_dir()
        (run_dir / "probe.py").write_text(_PROBE, encoding="utf-8")
        outside = ROOT / "workspace" / "sandbox" / ("escape_" + uuid.uuid4().hex[:6] + ".txt")
        secret = ROOT / "workspace" / "bridge" / ".token"
        probe = run([sys.executable, "-I", "-S", "probe.py", str(secret), str(Path.home()), str(outside), str(ROOT / "nexus" / "bridge" / "auth.py")],
                    run_dir, wall_seconds=30)
        checks = json.loads(probe["stdout"].strip().splitlines()[-1]) if probe["stdout"].strip() else {}
        (run_dir / "mem.py").write_text("x = bytearray(900 * 1024 * 1024)\nprint('ALLOCATED')\n", encoding="utf-8")
        mem = run([sys.executable, "-I", "-S", "mem.py"], run_dir, memory_mb=256, wall_seconds=20)
        checks["memory_limit"] = "ENFORCED" if "ALLOCATED" not in mem["stdout"] else "NOT_ENFORCED"
        (run_dir / "loop.py").write_text("while True:\n    pass\n", encoding="utf-8")
        loop = run([sys.executable, "-I", "-S", "loop.py"], run_dir, wall_seconds=3, cpu_seconds=2)
        checks["time_limit"] = "ENFORCED" if loop["duration_s"] < 10 and loop["returncode"] != 0 else "NOT_ENFORCED"
        checks["escape_file_absent"] = not outside.exists()
        cleanup(run_dir)
        want_denied = ("read_secret", "read_user_profile", "read_nexus_source", "write_outside", "net_loopback_bridge", "net_external")
        ok = (all(str(checks.get(k, "")).startswith("DENIED") for k in want_denied)
              and checks.get("write_run_dir") == "ALLOWED" and checks["memory_limit"] == "ENFORCED"
              and checks["time_limit"] == "ENFORCED" and checks["escape_file_absent"])
        result.update(ok=ok, checks=checks, stderr=probe["stderr"][-500:])
        if outside.exists():
            outside.unlink()
    except SandboxUnavailable as exc:
        result["error"] = str(exc)
    except Exception as exc:  # fail closed
        result["error"] = "SELFTEST_ERROR:" + type(exc).__name__
    finally:
        if "run_dir" in locals():
            cleanup(run_dir)  # auch nach Fehlern keine Laufreste
    try:
        SELFTEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        SELFTEST_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except OSError:
        pass
    return result


if __name__ == "__main__":
    print(json.dumps(self_test(force=True), indent=2))
