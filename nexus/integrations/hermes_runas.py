"""Optionaler Startweg: Hermes als lokaler Benutzer nexus-hermes (Auftrag 003d, 2026-10-15).

Standard AUS. Eingeschaltet nur, wenn
  - Umgebungsvariable NEXUS_HERMES_RUN_AS=nexus-hermes gesetzt ist, oder
  - workspace/hermes_service/run_as.json {"enabled": true} enthaelt
    (optional "user", "credential_target"; Standard jeweils "nexus-hermes").
NEXUS_HERMES_RUN_AS=gustl (oder off/0) erzwingt AUS, auch wenn die Datei an ist.

Anmeldedaten kommen aus der Windows-Anmeldeinformationsverwaltung (CredReadW, Typ
CRED_TYPE_GENERIC, Ziel = credential_target). Das Passwort wird nie geloggt, ausgegeben
oder in Fehlermeldungen uebernommen und nach dem Start im Speicher ueberschrieben.
Fehlt der Eintrag oder scheitert der Start: RunAsUnavailable -> HERMES_UNAVAILABLE,
niemals Rueckfall auf den angemeldeten Benutzer.
"""
from __future__ import annotations

import ctypes, json, os, subprocess, time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "workspace" / "hermes_service" / "run_as.json"
HERMES_HOME = ROOT / "workspace" / "hermes_arbeitsbereich" / "nexus-hermes-home"
OUTPUT_DIR = ROOT / "workspace" / "hermes_out"
DEFAULT_USER = "nexus-hermes"
DEFAULT_TARGET = "nexus-hermes"


class RunAsUnavailable(Exception):
    """Grund ohne Geheimnisse."""


def settings() -> dict:
    """{'enabled', 'run_as', 'user', 'credential_target', 'switch'} - liest nur Schalter, nie Credentials."""
    cfg = {}
    try:
        if CONFIG.is_file():
            cfg = json.loads(CONFIG.read_text(encoding="utf-8")) or {}
    except Exception:
        cfg = {"_invalid": True}
    user = str(cfg.get("user") or DEFAULT_USER)
    target = str(cfg.get("credential_target") or DEFAULT_TARGET)
    env = (os.environ.get("NEXUS_HERMES_RUN_AS") or "").strip().lower()
    if env in ("gustl", "off", "0", "false", "aus"):
        enabled, switch = False, "env:off"
    elif env:
        enabled, switch = True, "env"
        if env != "1" and env != "on" and env != "true":
            user = os.environ.get("NEXUS_HERMES_RUN_AS").strip()
    else:
        enabled, switch = cfg.get("enabled") is True, ("file" if cfg.get("enabled") is True else "default_off")
    return {"enabled": enabled, "run_as": user if enabled else "gustl", "user": user,
            "credential_target": target, "switch": switch, "hermes_home": str(HERMES_HOME)}


# --- Windows-API ---------------------------------------------------------------
class CREDENTIALW(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.c_void_p),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


CRED_TYPE_GENERIC = 1
LOGON_WITH_PROFILE = 0x1
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESTDHANDLES = 0x100
WAIT_TIMEOUT = 0x102


def _api():
    if os.name != "nt":
        raise RunAsUnavailable("RUN_AS_REQUIRES_WINDOWS")
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
    adv.CredReadW.restype = wintypes.BOOL
    adv.CredFree.argtypes = [ctypes.c_void_p]
    adv.CreateProcessWithLogonW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                            wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p,
                                            wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW),
                                            ctypes.POINTER(PROCESS_INFORMATION)]
    adv.CreateProcessWithLogonW.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return adv, k32


def credential_present(target: str) -> bool:
    """Nur Existenz pruefen; Blob wird nicht gelesen."""
    adv, _ = _api()
    p = ctypes.POINTER(CREDENTIALW)()
    if not adv.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
        return False
    adv.CredFree(p)
    return True


def _read_credential(target: str, default_user: str):
    adv, _ = _api()
    p = ctypes.POINTER(CREDENTIALW)()
    if not adv.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
        raise RunAsUnavailable(f"CREDENTIAL_MISSING: Eintrag '{target}' (generisch) nicht gefunden")
    try:
        c = p.contents
        user = c.UserName or default_user
        size = c.CredentialBlobSize
        if not size or not c.CredentialBlob:
            raise RunAsUnavailable(f"CREDENTIAL_EMPTY: Eintrag '{target}' ohne Passwort")
        raw = ctypes.string_at(c.CredentialBlob, size)
        pw = raw.decode("utf-16-le")  # cmdkey / Credential Manager speichern UTF-16
        ctypes.memset(c.CredentialBlob, 0, size)
        return user, pw
    finally:
        adv.CredFree(p)


def _env_block(env: dict) -> ctypes.Array:
    block = "".join(f"{k}={v}\0" for k, v in sorted(env.items())) + "\0"
    return ctypes.create_unicode_buffer(block, len(block))


def _simple_as(adv, k32, acct, domain, pw, cmdline_s, env, cwd, timeout):
    """Kurzer Hilfsprozess als nexus-hermes (mkdir/robocopy), ohne Ausgabe. Gibt Exitcode zurueck."""
    si = STARTUPINFOW(); si.cb = ctypes.sizeof(si)
    pi = PROCESS_INFORMATION()
    buf = ctypes.create_unicode_buffer(cmdline_s)
    envb = _env_block(env)
    ok = adv.CreateProcessWithLogonW(acct, domain, pw, LOGON_WITH_PROFILE, None, buf,
                                     CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, ctypes.cast(envb, ctypes.c_void_p),
                                     cwd, ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        return -1
    try:
        if k32.WaitForSingleObject(pi.hProcess, int(timeout * 1000)) == WAIT_TIMEOUT:
            k32.TerminateProcess(pi.hProcess, 1); return -2
        c = wintypes.DWORD(); k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(c)); return c.value
    finally:
        k32.CloseHandle(pi.hThread); k32.CloseHandle(pi.hProcess)


import re as _re
_DRIVE = _re.compile(r"(?i)(?<![A-Za-z])[a-z]:[\\/]")

def _scan_root():
    r = set()
    try: r |= {"C:\\" + x for x in os.listdir("C:\\")}
    except OSError: pass
    for base in ("C:\\ProgramData", "C:\\Users\\Public"):
        for dp, dns, fns in os.walk(base, onerror=lambda e: None):
            for n in dns + fns:
                r.add(os.path.join(dp, n))
    return r

def _scan_profile(adv, k32, acct, domain, pw, env, sysroot, user, tag):
    out = HERMES_HOME / "tmp" / f"prof_{tag}.txt"
    try: out.unlink()
    except OSError: pass
    _simple_as(adv, k32, acct, domain, pw, f'cmd.exe /c dir /s /b /a "C:\\Users\\{user}" > "{out}" 2>nul', env, sysroot, 120)
    try: lines = out.read_text("mbcs", "replace").splitlines()
    except OSError: return None
    skip = ("\\appdata\\roaming", "\\appdata\\locallow", "\\hermes_out", "\\ntuser", "\\hermes_tasks")
    return {l for l in lines if l and not any(x in l.lower() for x in skip) and ("\\appdata\\local\\" not in l.lower() or "\\appdata\\local\\temp" in l.lower())}


def run(cmd: list[str], *, cwd: str, timeout: int, base_env: dict, user: str, credential_target: str):
    """Wie subprocess.run(capture_output=True, text=True), aber als anderer Benutzer.
    Gibt subprocess.CompletedProcess zurueck oder wirft RunAsUnavailable / subprocess.TimeoutExpired."""
    adv, k32 = _api()
    breakout = None
    acct, pw = _read_credential(credential_target, user)
    domain = "."
    if "\\" in acct:
        domain, acct = acct.split("\\", 1)
    HERMES_HOME.mkdir(parents=True, exist_ok=True)
    redirect = Path(cwd).resolve() == (ROOT / "workspace" / "hermes_tasks").resolve()
    real_cwd = str(OUTPUT_DIR) if redirect else str(cwd)
    if redirect and (not OUTPUT_DIR.is_dir() or OUTPUT_DIR.is_symlink() or OUTPUT_DIR.is_junction()):
        raise RunAsUnavailable("HERMES_OUTPUT_DIRECTORY_UNAVAILABLE")
    tmp = HERMES_HOME / "tmp"; tmp.mkdir(exist_ok=True)
    sysroot = os.environ.get("SYSTEMROOT", r"C:\Windows")
    env = {k: v for k, v in base_env.items() if k in ("PYTHONIOENCODING", "PYTHONUTF8", "NUMBER_OF_PROCESSORS",
                                                   "PROCESSOR_ARCHITECTURE", "OS", "PATHEXT", "COMSPEC")}
    env.update({"SYSTEMROOT": sysroot, "WINDIR": sysroot, "HERMES_HOME": str(HERMES_HOME),
                "TEMP": str(tmp), "TMP": str(tmp), "TMPDIR": str(tmp), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "TERMINAL_CWD": real_cwd,
                "USERPROFILE": rf"C:\Users\{user}", "HOME": rf"C:\Users\{user}", "HOMEDRIVE": "C:", "HOMEPATH": rf"\Users\{user}", "USERNAME": user,
                "APPDATA": rf"C:\Users\{user}\AppData\Roaming", "LOCALAPPDATA": rf"C:\Users\{user}\AppData\Local",
                "PATH": str(Path(cmd[0]).parent) + ";" + sysroot + r"\System32;" + sysroot})
    if redirect:
        # C: programs remain usable; all mutable runtime locations live on G:.
        env.update({"HOME": str(HERMES_HOME), "USERPROFILE": str(HERMES_HOME),
                    "HOMEDRIVE": HERMES_HOME.drive, "HOMEPATH": str(HERMES_HOME)[2:],
                    "APPDATA": str(HERMES_HOME / "runtime" / "roaming"),
                    "LOCALAPPDATA": str(HERMES_HOME / "runtime" / "local"),
                    "XDG_CACHE_HOME": str(HERMES_HOME / "cache"),
                    "XDG_STATE_HOME": str(HERMES_HOME / "runtime" / "state"),
                    "XDG_CONFIG_HOME": str(HERMES_HOME / "runtime" / "config"),
                    "UV_CACHE_DIR": str(HERMES_HOME / "cache" / "uv"),
                    "PIP_CACHE_DIR": str(HERMES_HOME / "cache" / "pip")})
        install_base = Path(cmd[0]).parent.parent
        python_candidates = sorted((install_base / "tools").glob("python-*/python.exe"))
        if not python_candidates or not (install_base / "hermes-agent" / "hermes_bootstrap.py").is_file():
            raise RunAsUnavailable("HERMES_INSTALLED_RUNTIME_UNAVAILABLE")
        adapter = ROOT / "workspace" / "hermes_service" / "queries" / "_nexus_g_cli.py"
        adapter.write_bytes((ROOT / "nexus" / "integrations" / "hermes_g_cli.py").read_bytes())
        env["NEXUS_HERMES_INSTALL"] = str(install_base / "hermes-agent")
        cmd = [str(python_candidates[-1]), "-B", str(adapter), *cmd[1:]]
    runs = ROOT / "workspace" / "hermes_service" / "runas_io"; runs.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    out_p, err_p = runs / f"{stamp}.out", runs / f"{stamp}.err"
    import msvcrt
    fo, fe, fi = open(out_p, "wb"), open(err_p, "wb"), open(os.devnull, "rb")
    try:
        hs = [msvcrt.get_osfhandle(f.fileno()) for f in (fi, fo, fe)]
        for h in hs:
            os.set_handle_inheritable(h, True)
        si = STARTUPINFOW(); si.cb = ctypes.sizeof(si); si.dwFlags = STARTF_USESTDHANDLES
        si.hStdInput, si.hStdOutput, si.hStdError = hs
        pi = PROCESS_INFORMATION()
        if redirect:
            cmd = [real_cwd if c == str(cwd) else c for c in cmd]
            pre = str(cwd).rstrip("\\") + "\\"
            cmd = [c.replace(pre, "").replace(pre.replace("\\", "/"), "") if " " in c else c for c in cmd]
            bad = [c for c in cmd if " " in c and _DRIVE.search(c)]
            if bad:
                raise RunAsUnavailable("HERMES_PATH_REJECTED: absolute Laufwerkspfade im Auftrag sind nicht erlaubt, nur Dateinamen relativ zum Arbeitsordner")
            _hint = "[NEXUS] Arbeitsordner ist " + real_cwd + ". Lege ALLE Dateien nur dort an; verwende relative Dateinamen, keine anderen Ordner. Auftrag: "
            _pi = max((i for i, c in enumerate(cmd) if " " in c), key=lambda i: len(cmd[i]), default=None)
            if _pi is not None: cmd[_pi] = _hint + cmd[_pi]
            root0 = _scan_root(); prof0 = _scan_profile(adv, k32, acct, domain, pw, env, sysroot, user, "before")
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(cmd))
        envb = _env_block(env)
        ok = adv.CreateProcessWithLogonW(acct, domain, pw, LOGON_WITH_PROFILE, None, cmdline,
                                         CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, ctypes.cast(envb, ctypes.c_void_p),
                                         real_cwd, ctypes.byref(si), ctypes.byref(pi))
        err = ctypes.get_last_error()
        if not ok:
            raise RunAsUnavailable(f"RUN_AS_START_FAILED: winerror={err}")
        try:
            w = k32.WaitForSingleObject(pi.hProcess, int(timeout * 1000))
            if w == WAIT_TIMEOUT:
                k32.TerminateProcess(pi.hProcess, 1)
                raise subprocess.TimeoutExpired(cmd, timeout)
            code = wintypes.DWORD()
            k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        finally:
            k32.CloseHandle(pi.hThread); k32.CloseHandle(pi.hProcess)
        if redirect:
            rc_cp = _simple_as(adv, k32, acct, domain, pw, f'robocopy "{real_cwd}" "{cwd}" /E /COPY:DAT /DCOPY:DA /XJ /MAX:10485760 /R:0 /W:0 /NP /NJH /NJS', env, sysroot, 120)
            if rc_cp < 0 or rc_cp >= 8:
                code.value = code.value or 98
                with open(err_p, "ab") as copy_error:
                    copy_error.write(f"\nHERMES_OUT_COPY_FAILED rc={rc_cp}\n".encode("utf-8"))
            new = sorted(_scan_root() - root0)
            prof1 = _scan_profile(adv, k32, acct, domain, pw, env, sysroot, user, "after")
            if prof0 is not None and prof1 is not None:
                new += sorted(prof1 - prof0)
            if new:
                import json as _j
                with open(ROOT / "workspace" / "hermes_service" / "breakout.log", "a", encoding="utf-8") as bl:
                    bl.write(_j.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "user": user,
                                      "request_ref": stamp, "new_paths": new[:50],
                                      "origin": "unverified", "action": "report_only"}, ensure_ascii=False) + "\n")
                breakout = new
                try:  # A path difference proves no authorship. Keep alarm, report only.
                    from nexus.integrations import hermes_quarantine as _hq
                    _hq.report_candidates(new[:50], stamp, "unattributed_path_difference")
                except Exception:
                    pass
    finally:
        pw = None
        for f in (fo, fe, fi):
            f.close()
    rd = lambda p: p.read_bytes().decode("utf-8", "replace") if p.is_file() else ""
    so, se = rd(out_p), rd(err_p)
    for p in (out_p, err_p):
        try: p.unlink()
        except OSError: pass
    if breakout:
        return subprocess.CompletedProcess(cmd, 97, "", "HERMES_UNATTRIBUTED_PATHS: Herkunft nicht belegt; nur gemeldet: " + "; ".join(breakout[:10]))
    return subprocess.CompletedProcess(cmd, ctypes.c_int32(code.value).value, so, se)
