"""PC-Lautsprecher: Windows SAPI (Fallback). Bevorzugt russische Stimme."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


def speak(text: str, timeout: int = 90) -> dict:
    text = (text or "").strip()
    if not text:
        return {"success": False, "error": "leer"}
    cleaned = []
    skip = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            skip = not skip
            continue
        if not skip:
            cleaned.append(line)
    text = " ".join(" ".join(cleaned).split())[:1800]
    if not text:
        return {"success": False, "error": "leer"}

    if os.name != "nt":
        return {"success": False, "error": "not_windows", "reply": "speak_pc nur unter Windows"}

    tmp = Path(tempfile.gettempdir()) / "nexus_speak_pc.txt"
    try:
        tmp.write_text(text, encoding="utf-8")
    except Exception as e:
        return {"success": False, "error": str(e)}

    path = str(tmp).replace("'", "''")
    # Prefer Russian voice; warm slower rate (-2)
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Volume = 100; $s.Rate = -2; "
        "$picked = $null; "
        "foreach ($v in $s.GetInstalledVoices()) { "
        "  $n = $v.VoiceInfo.Name; $c = [string]$v.VoiceInfo.Culture; "
        "  if ($c -like 'ru*' -or $n -match 'Irina|Elena|Russian|Svetlana') { $picked = $n; break } "
        "} "
        "if ($picked) { try { $s.SelectVoice($picked) } catch {} } "
        f"$t = [IO.File]::ReadAllText('{path}'); "
        "$s.Speak($t);"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            return {
                "success": False,
                "error": (r.stderr or r.stdout or "sapi_fail")[:400],
                "returncode": r.returncode,
            }
        return {"success": True, "mode": "windows_sapi", "chars": len(text)}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "sapi_timeout"}
    except Exception as e:
        return {"success": False, "error": str(e)}
