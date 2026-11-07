"""Ziel-Zuordnung (Stufe 2.1c): wo wirkt ein Werkzeug?

  core    Status, Memory, Chat, Web-Suche (nur Nexus-Prozess selbst)
  runner  Datei-, Code-, Git- und Shell-Werkzeuge (Arbeitskopie/Runner)
  host    Desktop, Audio, adb/Android, Dienste, Prozesse, Apps, Zwischenablage, Screenshot

Reihenfolge: 1. explizite Tabelle TARGET_BY_NAME, 2. Praefix-Regeln (HOST_PREFIXES vor RUNNER_PREFIXES), 3. core.
Dazu die Stufen-Tabellen fuer die Master-Werkzeuge (nur dort ist die Stufe nicht anderweitig hinterlegt).
"""
from __future__ import annotations

HOST_PREFIXES = ("android_", "termux_", "service_", "nexus_start", "nexus_stop", "nexus_restart",
                 "mouse_", "keyboard_", "pc_")
RUNNER_PREFIXES = ("git_", "python_", "shell_", "code_")
RUNNER_SUFFIXES = ("_file",)

TARGET_BY_NAME: dict[str, str] = {
    # --- runner: Dateien / Code / Git / Shell ---
    "list_files": "runner", "search_files": "runner", "search_log": "runner", "list_dir": "runner",
    "create_dir": "runner", "patch_file": "runner", "write_note": "runner", "delete_temp": "runner",
    "sandbox_write": "runner", "sandbox_delete": "runner", "project_write": "runner",
    "shell": "runner", "powershell": "runner", "run_script": "runner", "github": "runner",
    "powershell_registered_action": "runner", "hermes_run_code_tests": "runner",
    "hermes_prepare_nexus_patch": "runner", "hermes_test_nexus_patch": "runner",
    "nexus_apply_patch": "runner", "nexus_rollback_patch": "runner", "hermes_publish_preview": "runner",
    # --- host: Desktop / Audio / Android / Dienste / Prozesse / Apps ---
    "list_dir_os": "host", "open_url": "host", "open_path": "host", "launch_app": "host",
    "play_media": "host", "close_opened": "host", "close_url": "host", "youtube_play": "host",
    "volume": "host", "media_key": "host", "clipboard": "host",
    "screenshot": "host", "screen_read": "host", "screen_size": "host", "pointer_position": "host",
    "whatsapp_read": "host", "whatsapp_send": "host", "whatsapp_open": "host",
    "restart_nexus_bridge_exact": "host", "install_krita_portable": "host", "krita_gui_test": "host",
    "deploy_phase_a_control": "host", "provision_bridge_token_to_phone": "host",
    "nexus_control_status": "core",  # Status trotz Praefix nexus_ -> core
}


def target_for(name: str) -> str:
    n = str(name or "").strip().lower()
    t = TARGET_BY_NAME.get(n)
    if t:
        return t
    if n.startswith(HOST_PREFIXES):
        return "host"
    if n.startswith(RUNNER_PREFIXES) or n.endswith(RUNNER_SUFFIXES):
        return "runner"
    return "core"


# Master-Werkzeuge (nexus.master.tools.KNOWN_TOOLS), Stufe nach Code-Lektuere:
# PRIVILEGED = laeuft ueber guard.authorize (Shell/github dynamisch bis PRIVILEGED) bzw. force_approval
# (http_request, whatsapp_*, sensible Desktop-Eingaben, mouse_move gemaess Bridge). READ_ONLY = reines Lesen.
MASTER_PRIVILEGED = frozenset({
    "powershell", "shell", "run_script", "github", "http_request",
    "whatsapp_send", "whatsapp_open",
    "mouse_move", "mouse_click", "keyboard_type", "keyboard_hotkey",
})
MASTER_READ_ONLY = frozenset({
    "read_file", "list_dir", "list_dir_os", "status", "system_monitor", "web_search", "youtube_search",
    "screenshot", "screen_read", "screen_size", "pointer_position", "calendar_list", "whatsapp_read",
})
