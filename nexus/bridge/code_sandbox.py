"""Begrenzter Python-Testexecutor und statische Code-Prüfung für den Hermes-Codebereich.

Bewusste Grenze (ehrlich, keine Falschaussagen):

  - Es gibt KEINE vollständige Datei- oder Netzwerkisolation. Python-interne
    Guards (socket-/Import-Blockade) sind reine Defense-in-depth und ausdrücklich
    KEINE Sicherheitsgrenze. Ein Prüfprogramm könnte sie prinzipiell umgehen.
  - Ausführung startet NUR, wenn vorher eine OS-Containment-Grenze etabliert
    werden konnte: unter Windows ein Job Object (Speicher-, Prozess-, Baumgrenze,
    KILL_ON_JOB_CLOSE), auf POSIX rlimits + eigene Prozessgruppe. Der Launcher
    startet blockiert (stdin-Handshake) und wird erst nach erfolgreicher
    Job-Zuordnung freigegeben; schlägt Job-Erstellung oder Zuordnung fehl, wird
    KEIN Zielcode ausgeführt.
  - Zusätzlich ist die Ausführung standardmäßig deaktiviert und muss per
    NEXUS_CODE_EXEC=1 ausdrücklich freigegeben werden. Ohne diese Freigabe bleibt
    nur Codeerstellung + statische Prüfung.
  - Ausgeführt wird ausschließlich eine .py-Datei aus
    workspace/projects/hermes/code; Arbeitsverzeichnis ist deren Projektordner.

Die statische Prüfung (check_sources) führt keinerlei Code aus: sie parst nur den
Syntaxbaum und meldet verbotene Importe/Aufrufe als Prüfergebnis.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CODE_ROOT = "workspace/projects/hermes/code"
CODE_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
# Autorisierter Entwicklungsbereich: Python + Browserprojekte (HTML/CSS/JS), keine Binärdateien.
CODE_EXT = ("py", "md", "json", "html", "css", "js")
MAX_SEGMENTS = 3
DEFAULT_TIMEOUT = 20
MAX_TIMEOUT = 60
MAX_STDOUT = 40_000
MAX_STDERR = 16_000
MAX_SOURCE = 200_000
MEMORY_LIMIT_MB = 1024
PROCESS_LIMIT = 16
EXEC_ENV = "NEXUS_CODE_EXEC"
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}
_SECRET_ENV = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "APIKEY", "CREDENTIAL", "OPENAI", "ANTHROPIC", "BRIDGE")
_KEEP_ENV = {
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "SYSTEMDRIVE",
    "HOME", "USERPROFILE", "LANG", "LC_ALL", "COMSPEC", "NUMBER_OF_PROCESSORS",
    "OS", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "PROGRAMW6432", "PYTHONIOENCODING", "PYTHONUTF8",
}
_FORBIDDEN_MODULES = {
    "socket", "selectors", "ssl", "subprocess", "multiprocessing", "ctypes",
    "urllib", "http", "ftplib", "smtplib", "telnetlib", "xmlrpc", "requests",
    "httpx", "aiohttp", "websockets", "winreg", "pyaudio",
}
_FORBIDDEN_ATTRS = {"os.system", "os.popen", "os.fork", "os.forkpty",
                    "os.execv", "os.execve", "os.execvp", "os.execvpe", "os.execl", "os.execle",
                    "os.spawnv", "os.spawnve", "os.spawnl", "os.spawnle"}
_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))

GUARD_SOURCE = """\
import os, sys, runpy

# Handshake: the parent releases this launcher only after containment (job object)
# is established. No target code runs before that single byte arrives.
try:
    os.read(0, 1)
except Exception:
    pass

_BLOCKED = "NEXUS_SANDBOX_BLOCKED: {0} is not available in the NEXUS test sandbox"

def _blocked(what):
    def _deny(*args, **kwargs):
        raise RuntimeError(_BLOCKED.format(what))
    return _deny

# Defense-in-depth only (NOT a security boundary): hide common network/process modules.
for _mod in ("subprocess", "multiprocessing", "ftplib", "smtplib", "telnetlib",
             "http.client", "urllib.request", "urllib.error", "xmlrpc.client",
             "socketserver", "socket"):
    try:
        sys.modules[_mod] = None
    except Exception:
        pass

for _name in ("system", "popen", "execv", "execve", "execvp", "execvpe", "execl",
              "execle", "spawnv", "spawnve", "spawnl", "spawnle", "fork", "forkpty"):
    if hasattr(os, _name):
        setattr(os, _name, _blocked("process"))

os.environ["NEXUS_SANDBOX_ACTIVE"] = "1"
target = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.dirname(target))
sys.argv = [target] + sys.argv[2:]
runpy.run_path(target, run_name="__main__")
"""


class SandboxError(Exception):
    """Traegt nur statische Codes, niemals rohe Prozess- oder Transportfehler."""


def code_path(rel: str) -> str | None:
    """Bereinigt einen relativen Codebereich-Pfad oder liefert None.

    Erlaubt ist nur workspace/projects/hermes/code/<...>/<name>.<py|md|json|html|css|js>,
    maximal MAX_SEGMENTS Verzeichnisebenen, keine Traversal und keine reservierten
    Windows-Geraetenamen.
    """
    if type(rel) is not str:
        return None
    value = rel.strip().replace("\\", "/")
    if not value.startswith(CODE_ROOT + "/"):
        return None
    parts = [p for p in value[len(CODE_ROOT) + 1:].split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or len(parts) > MAX_SEGMENTS + 1:
        return None
    for segment in parts[:-1]:
        if not CODE_SEGMENT.match(segment):
            return None
    stem, _, ext = parts[-1].rpartition(".")
    if ext.lower() not in CODE_EXT or not CODE_SEGMENT.match(stem) or stem.upper() in _RESERVED:
        return None
    return "/".join([CODE_ROOT, *parts])


def _inside(root: Path, rel: str) -> Path | None:
    """Aufgeloester Pfad innerhalb root ohne Symlink-Komponenten, sonst None."""
    base = root / rel
    try:
        target = base.resolve()
    except OSError:
        return None
    if os.path.normcase(str(target)) != os.path.normcase(str(base)):
        return None
    if base.is_symlink():
        return None
    from nexus.safety.protected import is_protected  # Stufe 2.1b
    if is_protected(target):
        return None
    return target


def _clean_env() -> dict:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _KEEP_ENV:
            env[key] = value
    for key in list(env):
        if any(marker in key.upper() for marker in _SECRET_ENV):
            env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["NEXUS_SANDBOX_ACTIVE"] = "1"
    return env


def execution_enabled() -> bool:
    return os.environ.get(EXEC_ENV) == "1"


# ---------------------------------------------------------------------------
# Statische Prüfung (fuehrt KEINEN Code aus)
# ---------------------------------------------------------------------------

def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _defined_names(path: Path) -> set[str]:
    """Top-Level-Namen einer lokalen .py-Datei (def/class/Zuweisungen/Importe).

    Nur der Modulkoerper zaehlt: importiert eine Datei `from x import y`, gilt y
    lokal als vorhanden. Ein Parse-Fehler liefert eine leere Menge (fehlende
    Aufloesbarkeit), niemals eine Ausnahme.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target_node in node.targets:
                if isinstance(target_node, ast.Name):
                    names.add(target_node.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
    return names


def check_sources(root, rel: str, *, siblings: set[str] | None = None) -> dict:
    """Parst eine .py-Datei des Codebereichs ohne Ausführung und meldet Struktur.

    Rueckgabe ist immer ein strukturiertes Prüfergebnis; ein Syntaxfehler wird als
    syntax='error' gemeldet, nicht als Ausnahme. Importe werden eingeordnet:

      - forbidden: verbotene Netz-/Prozessmodule und -aufrufe
      - stdlib:    Python-Standardbibliothek
      - local:     Module, die als gleichnamige Nachbardatei im Projekt vorhanden sind
      - external:  weder Standardbibliothek noch Projektmodul (Fremdbibliothek
                   oder fehlendes lokales Modul) - im Codebereich nicht erlaubt
      - missing:   referenzierte lokale Namen/Module, die nicht existieren
                   (z. B. `from rechner import mul` ohne `mul` in rechner.py)

    `siblings` kann die erlaubten lokalen Modulnamen vorgeben; sonst werden die
    .py-Nachbardateien im selben Projektordner ermittelt. Die Entscheidung darüber,
    was ein Fehler ist, trifft die Outcome-Regel.
    """
    clean = code_path(rel)
    if clean is None or not clean.lower().endswith(".py"):
        raise SandboxError("CHECK_PATH_NOT_ALLOWED")
    base = Path(root).resolve()
    target = _inside(base, clean)
    if target is None:
        raise SandboxError("CHECK_PATH_NOT_ALLOWED")
    if not target.is_file():
        raise SandboxError("CHECK_FILE_MISSING")
    raw = target.read_bytes()
    if len(raw) > MAX_SOURCE:
        raise SandboxError("CHECK_SOURCE_TOO_LARGE")
    text = raw.decode("utf-8", "replace")
    lines = text.count("\n") + 1
    blank = {"imports": [], "defs": [], "forbidden": [], "stdlib": [],
             "local": [], "external": [], "missing": []}
    try:
        tree = ast.parse(text, filename=clean)
    except SyntaxError as exc:
        return {"path": clean, "syntax": "error", "line": exc.lineno,
                "message": str(exc.msg or "syntax error"), "bytes": len(raw),
                "lines": lines, **blank}
    if siblings is None:
        siblings = {p.stem for p in target.parent.glob("*.py") if p.stem != target.stem}
    else:
        siblings = set(siblings)
    imports: set[str] = set()
    tops: set[str] = set()
    rel_tops: set[str] = set()
    from_targets: list[tuple[str, list[str]]] = []
    defs: list[str] = []
    forbidden: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
                tops.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
            if getattr(node, "level", 0):
                if node.module:
                    rel_tops.add(node.module.split(".")[0])
                else:
                    rel_tops.update(a.name for a in node.names if a.name != "*")
            elif node.module:
                top = node.module.split(".")[0]
                tops.add(top)
                from_targets.append((top, [a.name for a in node.names]))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.append(node.name)
        elif isinstance(node, ast.Call):
            full = _dotted(node.func)
            if full in _FORBIDDEN_ATTRS:
                forbidden.add(full)
            if full in ("__import__",):
                forbidden.add("__import__")
    stdlib: set[str] = set()
    local: set[str] = set()
    external: set[str] = set()
    for module in sorted(tops | rel_tops):
        if module in _FORBIDDEN_MODULES:
            forbidden.add("import " + module)
        elif module in _STDLIB or module == "__future__":
            stdlib.add(module)
        elif module in siblings:
            local.add(module)
        else:
            external.add(module)
    missing: set[str] = set()
    symbols: dict[str, set[str]] = {}
    for top, names in from_targets:
        if top not in siblings or top in _FORBIDDEN_MODULES:
            continue
        if top not in symbols:
            symbols[top] = _defined_names(target.parent / (top + ".py"))
        for name in names:
            if name != "*" and name not in symbols[top]:
                missing.add(top + "." + name)
    for module in sorted(rel_tops):
        if module not in siblings and module != "__future__":
            missing.add(module)
    return {"path": clean, "syntax": "ok", "bytes": len(raw), "lines": lines,
            "imports": sorted(imports), "defs": defs, "forbidden": sorted(forbidden),
            "stdlib": sorted(stdlib), "local": sorted(local),
            "external": sorted(external), "missing": sorted(missing)}


# ---------------------------------------------------------------------------
# OS-Containment
# ---------------------------------------------------------------------------

class _WindowsJob:
    def __init__(self, memory_mb: int, process_limit: int) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError("CreateJobObject failed")

        class _IoCounters(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                        ("WriteOperationCount", ctypes.c_ulonglong),
                        ("OtherOperationCount", ctypes.c_ulonglong),
                        ("ReadTransferCount", ctypes.c_ulonglong),
                        ("WriteTransferCount", ctypes.c_ulonglong),
                        ("OtherTransferCount", ctypes.c_ulonglong)]

        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _Basic),
                        ("IoInfo", _IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = _Extended()
        flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_mb:
            info.ProcessMemoryLimit = memory_mb * 1024 * 1024
            flags |= 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        if process_limit:
            info.BasicLimitInformation.ActiveProcessLimit = process_limit
            flags |= 0x8  # JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        info.BasicLimitInformation.LimitFlags = flags
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(handle)
            raise OSError("SetInformationJobObject failed")
        self._k = kernel32
        self._handle = handle

    def assign(self, proc) -> bool:
        try:
            handle = int(proc._handle)  # noqa: SLF001 - documented CPython attribute
        except Exception:
            return False
        return bool(self._k.AssignProcessToJobObject(self._handle, handle))

    def close(self) -> None:
        if self._handle:
            try:
                self._k.CloseHandle(self._handle)
            finally:
                self._handle = None


def _new_job():
    if os.name != "nt":
        return None
    try:
        return _WindowsJob(MEMORY_LIMIT_MB, PROCESS_LIMIT)
    except Exception:
        return None


def _posix_limits() -> None:
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_MB * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (MAX_TIMEOUT + 5, MAX_TIMEOUT + 5))
    except Exception:
        pass


def _posix_containment_available() -> bool:
    try:
        import resource  # noqa: F401
        return hasattr(os, "killpg") and hasattr(os, "setsid")
    except Exception:
        return False


def _kill_tree(proc, job) -> None:
    if job is not None:
        job.close()  # KILL_ON_JOB_CLOSE terminates every process in the job
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, check=False)
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    else:
        try:
            proc.wait(timeout=10)
        except Exception:
            pass


def _appcontainer_ready() -> bool:
    if os.name != "nt":
        return False
    try:
        from nexus.bridge.appcontainer import self_test
        return bool(self_test().get("ok"))
    except Exception:
        return False


def copy_project(source: Path, dest: Path, *, max_files: int = 40, max_bytes: int = 2_000_000) -> list[str]:
    """Nur reguläre, erlaubte Projektdateien (keine Links) in das Lauf-Verzeichnis kopieren."""
    copied, total = [], 0
    for item in sorted(source.rglob("*")):
        if item.is_symlink() or not item.is_file():
            continue
        from nexus.safety.protected import is_protected  # Stufe 2.1b
        if is_protected(item):
            continue
        rel = item.relative_to(source).as_posix()
        if rel.startswith(".") or item.suffix.lower().lstrip(".") not in CODE_EXT:
            continue
        total += item.stat().st_size
        if len(copied) >= max_files or total > max_bytes:
            raise SandboxError("PROJECT_TOO_LARGE")
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
        copied.append(rel)
    return copied


def _run_isolated(root, rel: str, *, timeout: int | None = None) -> dict:
    from nexus.bridge import appcontainer

    clean = code_path(rel)
    if clean is None or not clean.lower().endswith(".py"):
        raise SandboxError("TEST_PATH_NOT_ALLOWED")
    base = Path(root).resolve()
    target = _inside(base, clean)
    if target is None or not target.is_file():
        raise SandboxError("TEST_FILE_MISSING")
    seconds = DEFAULT_TIMEOUT if timeout is None else int(timeout)
    if not 1 <= seconds <= MAX_TIMEOUT:
        raise SandboxError("TEST_TIMEOUT_NOT_ALLOWED")
    run_dir = appcontainer.new_run_dir()
    try:
        copied = copy_project(target.parent, run_dir)
        # -E -s -S: keine Umgebungs-/Site-Pfade; Skriptordner bleibt importierbar (kein -I/-P).
        out = appcontainer.run([sys.executable, "-E", "-s", "-S", "-B", target.name], run_dir,
                               cpu_seconds=seconds, wall_seconds=seconds + 5)
    except appcontainer.SandboxUnavailable as exc:
        raise SandboxError("SANDBOX_UNAVAILABLE:" + str(exc).split(":")[0]) from None
    finally:
        appcontainer.cleanup(run_dir)
    stderr = "\n".join(l for l in out["stderr"].splitlines() if not l.startswith("Failed to find real location"))
    returncode = out["returncode"] if not out["timed_out"] else -1
    return {
        "path": clean, "returncode": int(returncode), "stdout": out["stdout"][:MAX_STDOUT],
        "stderr": stderr[:MAX_STDERR], "truncated_stdout": len(out["stdout"]) > MAX_STDOUT,
        "truncated_stderr": len(stderr) > MAX_STDERR, "timeout": seconds, "timed_out": out["timed_out"],
        "sandbox": True, "containment": "windows_appcontainer", "job_object": True, "env_sanitized": True,
        "network_guard": "os_appcontainer_no_capabilities", "filesystem_isolation": "appcontainer_run_copy",
        "memory_limit_mb": out["limits"]["memory_mb"], "process_limit": out["limits"]["processes"],
        "files": copied, "python": Path(sys.executable).name, "duration_s": out["duration_s"],
    }


def run_code_tests(root, rel: str, *, timeout: int | None = None, python: str | None = None,
                   allow_exec: bool | None = None) -> dict:
    """Fuehrt genau eine .py-Datei des Codebereichs aus - nur mit OS-Containment.

    Standardmäßig deaktiviert (NEXUS_CODE_EXEC != 1). Ohne etablierte
    Containment-Grenze wird nichts gestartet, sondern SandboxError ausgelöst.
    """
    if allow_exec is None and python is None:
        # Bevorzugt: belegte OS-Isolation (AppContainer + Job Object). Sie braucht keine
        # globale NEXUS_CODE_EXEC-Freigabe, weil Datei-, Netzwerk-, Speicher- und Zeitgrenzen
        # vor jeder Nutzung per Ausbruchsversuch nachgewiesen sind.
        isolated = _appcontainer_ready()
        if isolated:
            return _run_isolated(root, rel, timeout=timeout)
    if allow_exec is None:
        allow_exec = execution_enabled()
    if not allow_exec:
        raise SandboxError("CODE_EXECUTION_DISABLED")
    clean = code_path(rel)
    if clean is None or not clean.lower().endswith(".py"):
        raise SandboxError("TEST_PATH_NOT_ALLOWED")
    base = Path(root).resolve()
    target = _inside(base, clean)
    if target is None:
        raise SandboxError("TEST_PATH_NOT_ALLOWED")
    if not target.is_file():
        raise SandboxError("TEST_FILE_MISSING")
    seconds = DEFAULT_TIMEOUT if timeout is None else int(timeout)
    if not 1 <= seconds <= MAX_TIMEOUT:
        raise SandboxError("TEST_TIMEOUT_NOT_ALLOWED")
    exe = python or os.environ.get("PYTHON_EXE") or sys.executable
    if not exe or not Path(exe).is_file():
        raise SandboxError("PYTHON_UNAVAILABLE")

    workdir = Path(tempfile.mkdtemp(prefix="nexus-sandbox-"))
    proc = None
    job = None
    try:
        launcher = workdir / "guard_launcher.py"
        launcher.write_text(GUARD_SOURCE, encoding="utf-8")
        argv = [str(exe), "-I", "-B", str(launcher), str(target)]
        popen_kwargs: dict = {}
        if os.name == "nt":
            creationflags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
            job = _new_job()
            if job is None:
                raise SandboxError("SANDBOX_UNAVAILABLE")
            popen_kwargs["creationflags"] = creationflags
            containment = "windows_job_object"
        else:
            if not _posix_containment_available():
                raise SandboxError("SANDBOX_UNAVAILABLE")
            popen_kwargs["start_new_session"] = True
            popen_kwargs["preexec_fn"] = _posix_limits
            containment = "posix_rlimit"

        proc = subprocess.Popen(
            argv, cwd=str(target.parent), env=_clean_env(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", **popen_kwargs)
        # Release the launcher only after containment is established.
        if job is not None and not job.assign(proc):
            raise SandboxError("SANDBOX_ASSIGN_FAILED")
        try:
            proc.stdin.write("1")
            proc.stdin.flush()
            proc.stdin.close()
        except Exception:
            pass  # process already gone; communicate() still yields the error

        timed_out = False
        try:
            out, err = proc.communicate(timeout=seconds + 2)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc, job)
            job = None
            try:
                out, err = proc.communicate(timeout=10)
            except Exception:
                out, err = "", ""
    except SandboxError:
        if proc is not None:
            _kill_tree(proc, job)
        raise
    except Exception:
        if proc is not None:
            _kill_tree(proc, job)
        raise SandboxError("SANDBOX_START_FAILED") from None
    finally:
        if job is not None:
            job.close()
        shutil.rmtree(workdir, ignore_errors=True)

    out = out or ""
    err = err or ""
    returncode = proc.returncode if proc.returncode is not None else -1
    if timed_out and returncode == 0:
        returncode = -1  # a job-killed process must never look like a clean exit
    return {
        "path": clean,
        "returncode": int(returncode),
        "stdout": out[:MAX_STDOUT],
        "stderr": err[:MAX_STDERR],
        "truncated_stdout": len(out) > MAX_STDOUT,
        "truncated_stderr": len(err) > MAX_STDERR,
        "timeout": seconds,
        "timed_out": timed_out,
        "sandbox": True,
        "containment": containment,
        "job_object": containment == "windows_job_object",
        "env_sanitized": True,
        "network_guard": "best_effort_inprocess",
        "filesystem_isolation": "none",
        "memory_limit_mb": MEMORY_LIMIT_MB,
        "process_limit": PROCESS_LIMIT,
        "python": Path(exe).name,
    }
