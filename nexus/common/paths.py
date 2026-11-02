"""Relative Pfade — Projektroot = nexus-ai/ (parents[2] von common/).

Plattform-Abstraktion (Phase 11): ROOT via NEXUS_ROOT oder Dateilage;
keine hardcodierten G:\\-Pfade. Windows bleibt Host, ist aber keine dauerhafte Voraussetzung.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

def _detect_root() -> Path:
    env = (os.environ.get("NEXUS_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # nexus/common/paths.py -> parents[2] = project root
    return Path(__file__).resolve().parents[2]


ROOT = _detect_root()
WORKSPACE = ROOT / "workspace"
APEX = WORKSPACE / "apex"
CHAT_DIR = WORKSPACE / "chat"
MEMORY_DIR = WORKSPACE / "memory"
PROJECTS_DIR = WORKSPACE / "projects"
TASKS_DIR = WORKSPACE / "tasks"
MEDIA_DIR = WORKSPACE / "media"
DOWNLOADS_DIR = WORKSPACE / "downloads"
FILES_DIR = WORKSPACE / "files"
SCREENS_DIR = WORKSPACE / "screens"
LOGS = WORKSPACE / "logs"
BRIDGE_LOGS = ROOT / "logs" / "bridge"  # gateway/bridge JSONL (portable)
SECRETS = WORKSPACE / "secrets"  # nie Secrets in Code; Ordner optional
SCRIPTS_DIR = WORKSPACE / "scripts"
MASTER_PROMPT = APEX / "master_prompt_runtime.txt"
STATIC_DIR = ROOT / "nexus" / "chat" / "static"

# --- Platform helpers (cheap; no new deps) ---
IS_WINDOWS = sys.platform.startswith("win") or os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")
IS_DARWIN = sys.platform.startswith("darwin")
IS_POSIX = os.name == "posix"
PLATFORM_NAME = platform.system() or ("Windows" if IS_WINDOWS else "unknown")


def disk_root() -> str:
    """Mount/path for disk_usage — portable, no hardcoded drive letters in callers."""
    if IS_WINDOWS:
        # Prefer system drive if set; else C:\\
        sysdrive = (os.environ.get("SystemDrive") or "C:").rstrip("\\\\/")
        return sysdrive + "\\"
    return "/"


def which_shell_kind() -> str:
    """Default interactive shell kind for run_command when caller omits kind."""
    return "powershell" if IS_WINDOWS else "bash"


def ensure_dirs() -> None:
    for d in (
        WORKSPACE, APEX, CHAT_DIR, MEMORY_DIR, PROJECTS_DIR, TASKS_DIR,
        MEDIA_DIR, DOWNLOADS_DIR, FILES_DIR, SCREENS_DIR, LOGS, BRIDGE_LOGS,
        SECRETS, SCRIPTS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
