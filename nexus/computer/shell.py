"""Echte Shell-/PowerShell-Ausführung im NEXUS-Projektroot."""
from __future__ import annotations

import os
import platform
import re
import shlex
import subprocess
from pathlib import Path

from nexus.common.config import Config
from nexus.common.paths import ROOT, SCRIPTS_DIR, IS_WINDOWS, which_shell_kind

# Destruktive / systemweite Muster — blockieren
_BLOCK_PATTERNS = [
    re.compile(r"\bformat\s+[a-z]:", re.I),
    re.compile(r"\bFormat-Volume\b", re.I),
    re.compile(r"\b(shutdown|Restart-Computer|Stop-Computer)\b", re.I),
    re.compile(r"\b(rm\s+-rf\s+/|del\s+/[sf].*[a-z]:\\|Remove-Item\s+.*-[Rr].*[C-Z]:\\)", re.I),
    re.compile(r"\b(mkfs\.|dd\s+if=.*of=/dev/)", re.I),
    re.compile(r"\b(cipher\s+/w:|Clear-Disk)\b", re.I),
    re.compile(r":\s*\(\)\s*\{\s*:\|:\s*&\s*\}\s*;", re.I),  # fork bomb
]


def _clamp_timeout(timeout: int | float | str | None, default: int | None = None) -> int:
    """Timeout in Sekunden; Default SHELL_TIMEOUT, Cap SHELL_TIMEOUT_MAX (~900)."""
    base = default if default is not None else Config.SHELL_TIMEOUT
    if timeout is None or timeout == "":
        to = int(base)
    else:
        try:
            to = int(float(timeout))
        except (TypeError, ValueError):
            to = int(base)
    to = max(1, to)
    cap = int(getattr(Config, "SHELL_TIMEOUT_MAX", 900) or 900)
    return min(to, cap)


def _trim_output(text: str, max_chars: int, tail_lines: int) -> str:
    """Bei Überlänge letzte N Zeilen behalten (npm/vite-Logs)."""
    text = text or ""
    if len(text) <= max_chars:
        return text
    lines = text.splitlines()
    if len(lines) > tail_lines:
        kept = "\n".join(lines[-tail_lines:])
    else:
        kept = text[-max_chars:]
    omitted = len(text) - len(kept)
    return f"[… stdout gekürzt, {omitted} Zeichen weggelassen — letzte {tail_lines} Zeilen …]\n{kept}"


class ShellExecutor:
    """Führt Befehle wirklich aus. cwd bleibt unter ROOT."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or ROOT).resolve()

    def _resolve_cwd(self, cwd: str | Path | None) -> Path | None:
        if cwd is None or cwd == "":
            return self.root
        try:
            p = Path(cwd)
            if not p.is_absolute():
                p = (self.root / p).resolve()
            else:
                p = p.resolve()
        except Exception:
            return None
        root_s = str(self.root)
        if not (str(p) == root_s or str(p).startswith(root_s + os.sep)):
            return None
        if not p.is_dir():
            return None
        return p

    def _resolve_script(self, script: str | Path) -> Path | None:
        """Skript unter ROOT auflösen (relativ, Scripts-Dir, oder absolut in ROOT)."""
        raw = (str(script) if script is not None else "").strip()
        if not raw:
            return None
        try:
            p = Path(raw)
            if not p.is_absolute():
                # 1) wie angegeben relativ zum Root
                cand = (self.root / p).resolve()
                if cand.is_file():
                    p = cand
                else:
                    # 2) nur Dateiname → workspace/scripts/
                    by_name = (SCRIPTS_DIR / p.name).resolve()
                    if by_name.is_file():
                        p = by_name
                    else:
                        p = cand
            else:
                p = p.resolve()
        except Exception:
            return None
        root_s = str(self.root)
        if not (str(p) == root_s or str(p).startswith(root_s + os.sep)):
            return None
        if not p.is_file():
            return None
        return p

    def _blocked(self, command: str) -> str | None:
        cmd = command or ""
        for pat in _BLOCK_PATTERNS:
            if pat.search(cmd):
                return "Befehl blockiert (gefährliche Systemaktion)."
        # Wipe außerhalb Projekt: absolute Pfade außerhalb ROOT löschen
        low = cmd.lower()
        if any(x in low for x in ("rm -rf /", "rm -rf /*", "del /s /q c:\\", "rd /s /q c:\\")):
            return "Befehl blockiert (Wipe außerhalb Projekt)."
        return None

    def run(
        self,
        command: str,
        shell: str = "auto",
        timeout: int | None = None,
        cwd: str | Path | None = None,
        *,
        build_mode: bool = False,
    ) -> dict:
        """NEXUS-Chat-Werkzeug: laeuft ueber das NEXUS-Terminal (agent=NEXUS).

        Terminal = Ausfuehrungsgrenze: C:/destruktiv gesperrt, Rechtestufe aus Konfiguration,
        Erfolgspruefung, Audit. Modus 'approval' (Standard): jeder Befehl braucht Eddies Freigabe
        im Terminal-UI und laeuft danach live; bis dahin REGISTERED_ACTION_REQUIRED (nicht ausgefuehrt).
        """
        base = {"success": False, "status": "BLOCKED", "returncode": None, "stdout": "", "stderr": "", "via": "nexus-terminal"}
        if not str(command or "").strip():
            return {**base, "error": "command_missing", "reply": "Kein Befehl."}
        work = self._resolve_cwd(cwd)
        if work is None:
            return {**base, "error": "cwd_outside_root", "reply": "Arbeitsordner ausserhalb des Projekts."}
        try:
            from nexus.terminal import guard as _tg, service as _term
            sh = (shell or "auto").lower()
            tshell = "cmd" if sh == "cmd" else ("python" if sh == "python" else "session")
            to = _clamp_timeout(timeout, Config.SHELL_TIMEOUT_BUILD if build_mode and hasattr(Config, "SHELL_TIMEOUT_BUILD") else None)
            r = _term.execute(command, agent="NEXUS", shell=tshell, cwd=str(work), timeout=to, wait=True,
                              force_approval=_tg.chat_shell_mode() == "approval")
        except Exception as exc:
            return {**base, "error": "terminal_unavailable", "stderr": f"{type(exc).__name__}: {exc}"}
        if r.get("status") == "APPROVAL_REQUIRED":
            msg = ("Freigabe noetig: NEXUS-Terminal -> 'Freigeben' (approval_id " + str(r.get("approval_id")) +
                   "). Nach Freigabe laeuft der Befehl live im Terminal.")
            return {**base, "error": "REGISTERED_ACTION_REQUIRED", "stderr": msg, "reply": msg,
                    "approval_id": r.get("approval_id"), "terminal_run": r.get("run"), "verdict": "FEHLER",
                    "reasons": ["APPROVAL_REQUIRED"]}
        ok = bool(r.get("ok"))
        return {
            "success": ok, "status": r.get("status"), "returncode": r.get("returncode"),
            "stdout": r.get("stdout") or "", "stderr": r.get("stderr") or "",
            "error": None if ok else (r.get("error") or ",".join(r.get("reasons") or []) or "shell_failed"),
            "shell": r.get("shell") or tshell, "cwd": str(work), "timeout_s": to, "via": "nexus-terminal",
            "terminal_run": r.get("run"), "verdict": r.get("verdict"), "reasons": r.get("reasons"),
        }

    def run_script(
        self,
        script: str | Path,
        args: list | str | None = None,
        timeout: int | None = None,
        cwd: str | Path | None = None,
    ) -> dict:
        p = self._resolve_script(script)
        if p is None:
            return {"success": False, "status": "BLOCKED", "error": "script_not_found", "returncode": None,
                    "stdout": "", "stderr": f"Skript nicht gefunden: {script}", "script": str(script)}
        if isinstance(args, str):
            try:
                args = shlex.split(args)
            except ValueError:
                args = args.split()
        q = " ".join("'" + str(a).replace("'", "''") + "'" for a in (args or []))
        lit = "'" + str(p).replace("'", "''") + "'"
        cmd = ("python " + lit if p.suffix.lower() == ".py" else "& " + lit) + (" " + q if q else "")
        r = self.run(cmd, shell="powershell", timeout=timeout, cwd=cwd, build_mode=True)
        r["script"] = str(p)
        return r

    def _exec(self, argv: list, to: int, work: Path, kind: str) -> dict:
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=to,
                cwd=str(work),
            )
            # Preserve executor evidence byte-for-text as returned by subprocess.
            # Callers may format around it, but must not truncate or rewrite stdout/stderr.
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            return {
                "success": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "error": None if proc.returncode == 0 else (stderr.strip() or f"exit {proc.returncode}"),
                "shell": kind,
                "cwd": str(work),
                "timeout_s": to,
            }
        except subprocess.TimeoutExpired as e:
            partial_out = ""
            if e.stdout:
                partial_out = e.stdout if isinstance(e.stdout, str) else e.stdout.decode("utf-8", "replace")
            return {
                "success": False,
                "returncode": -1,
                "stdout": partial_out,
                "stderr": f"Timeout nach {to}s",
                "error": "timeout",
                "shell": kind,
                "cwd": str(work),
                "timeout_s": to,
            }
        except FileNotFoundError as e:
            return {
                "success": False,
                "returncode": -1,
                "stdout": "",
                "stderr": str(e),
                "error": "shell_not_found",
            }
        except Exception as e:
            return {
                "success": False,
                "returncode": -1,
                "stdout": "",
                "stderr": str(e),
                "error": str(e),
            }


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)
