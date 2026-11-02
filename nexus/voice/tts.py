from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from nexus.common.paths import APEX

DEFAULT_VOICES = (
    "de-DE-AmalaNeural",  # warm German
)
DEFAULT_VOICE = DEFAULT_VOICES[0]
DEFAULT_RATE = "-4%"
DEFAULT_PITCH = "-2Hz"
DEFAULT_VOLUME = "+0%"
TTS_TIMEOUT_SEC = 18


def _load_cfg() -> dict:
    cfg_path = APEX / "voice_config.json"
    try:
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def available() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:
        return False


def _humanize(text: str) -> str:
    """Light cleanup so speech sounds less staccato / robotic."""
    t = (text or "").strip()
    if not t:
        return ""
    # collapse whitespace / newlines
    t = re.sub(r"\s+", " ", t)
    # soften common chat markers
    t = t.replace("**", "").replace("__", "").replace("`", "")
    # ensure sentence-ending punctuation has a space after (helps break splitting)
    t = re.sub(r"([.!?…])([A-Za-zÄÖÜäöüßА-Яа-я])", r"\1 \2", t)
    return t.strip()


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", text)
    return [p.strip() for p in parts if p and p.strip()]


def _build_ssml(text: str, voice: str, rate: str, pitch: str, volume: str) -> str:
    """SSML with prosody + short break after each sentence for a warmer cadence."""
    sentences = _split_sentences(text) or [text]
    chunks: list[str] = []
    for i, sent in enumerate(sentences):
        chunks.append(xml_escape(sent))
        if i < len(sentences) - 1:
            chunks.append('<break time="180ms"/>')
    body = " ".join(chunks)
    return (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="de-DE">'
        f'<voice name="{xml_escape(voice)}">'
        f'<prosody rate="{xml_escape(rate)}" pitch="{xml_escape(pitch)}" volume="{xml_escape(volume)}">'
        f"{body}"
        f"</prosody></voice></speak>"
    )



# nexus_tts_local_fallback (Nexus-Media 16.10.2026): ohne Internet -> Windows SAPI (Hedda de-DE) -> mp3 via ffmpeg.
# Spielt nichts ab, schreibt nur die Datei. Erzwingen zum Test: NEXUS_TTS_FORCE_LOCAL=1
def _find_ffmpeg():
    import shutil, glob, os
    f = shutil.which("ffmpeg")
    if f:
        return f
    pat = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe")
    hits = sorted(glob.glob(pat))
    return hits[-1] if hits else None


def _synthesize_local(text: str, out_path: Path) -> dict:
    import os, subprocess, tempfile, uuid as _uuid
    if os.name != "nt":
        return {"success": False, "error": "local_tts_not_windows"}
    ff = _find_ffmpeg()
    if not ff:
        return {"success": False, "error": "local_tts_no_ffmpeg"}
    tmpdir = Path(tempfile.gettempdir())
    tag = _uuid.uuid4().hex[:8]
    txt = tmpdir / f"nexus_tts_local_{tag}.txt"
    wav = tmpdir / f"nexus_tts_local_{tag}.wav"
    try:
        txt.write_text(text[:1800], encoding="utf-8")
        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$v = $s.GetInstalledVoices() | Where-Object { [string]$_.VoiceInfo.Culture -like 'de*' } | Select-Object -First 1; "
            "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; $s.Rate = 0; "
            f"$s.SetOutputToWaveFile('{str(wav)}'); "
            f"$s.Speak([IO.File]::ReadAllText('{str(txt)}')); $s.Dispose()"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or not wav.exists():
            return {"success": False, "error": "local_sapi_failed"}
        r2 = subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(wav), "-codec:a", "libmp3lame", "-b:a", "64k", str(out_path)],
                            capture_output=True, text=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r2.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            return {"success": False, "error": "local_ffmpeg_failed"}
        return {"success": True, "path": str(out_path), "bytes": out_path.stat().st_size,
                "voice": "windows_sapi_de", "mode": "local_offline"}
    except Exception as e:
        return {"success": False, "error": f"local_tts_error: {e}"}
    finally:
        for f in (txt, wav):
            try:
                f.unlink()
            except Exception:
                pass


# F-07 (Fusion 24.10.2026): Offline-first. Schnelle Internetpruefung; ohne Netz zuerst Windows-SAPI,
# damit nicht jede Edge-Stimme erst in den Timeout laeuft. Online bleibt der bisherige Edge-Weg unveraendert.
_ONLINE_CACHE = {"ts": 0.0, "ok": True}

def _online(timeout: float = 1.5) -> bool:
    import os as _o, time as _t, socket as _s
    if _o.environ.get("NEXUS_TTS_FORCE_OFFLINE") == "1":
        return False
    now = _t.time()
    if now - _ONLINE_CACHE["ts"] < 20:
        return _ONLINE_CACHE["ok"]
    ok = False
    try:
        with _s.create_connection(("speech.platform.bing.com", 443), timeout=timeout):
            ok = True
    except Exception:
        try:
            with _s.create_connection(("1.1.1.1", 443), timeout=timeout):
                ok = True
        except Exception:
            ok = False
    _ONLINE_CACHE.update(ts=now, ok=ok)
    return ok


def synthesize(text: str, out_path: Path, voice: str | None = None, *, voices: list[str] | None = None,
               rate: str | None = None, pitch: str | None = None, volume: str | None = None) -> dict:
    """voices (Liste) ersetzt die Stimmen-Reihenfolge vollständig (z. B. nur Männerstimmen für Hermes);
    rate/pitch überschreiben die Konfiguration für genau diesen Aufruf."""
    text = _humanize(text)
    if not text:
        return {"success": False, "error": "leer"}
    import os as _os
    if _os.environ.get("NEXUS_TTS_FORCE_LOCAL") == "1":
        return _synthesize_local(text, out_path)
    try:
        import edge_tts
    except Exception:
        loc = _synthesize_local(text, out_path)
        if loc.get("success"):
            return loc
        return {
            "success": False,
            "stub": True,
            "error": "edge_tts_missing",
            "reply": "TTS-Stub: edge-tts nicht installiert.",
        }

    # F-07 offline-first: ohne Internet direkt SAPI; nur wenn SAPI scheitert, Edge trotzdem versuchen.
    if not _online():
        loc = _synthesize_local(text, out_path)
        if loc.get("success"):
            loc["mode"] = "local_offline_first"
            return loc

    cfg = _load_cfg()
    rate = str(rate or cfg.get("tts_rate") or DEFAULT_RATE)
    pitch = str(pitch or cfg.get("tts_pitch") or DEFAULT_PITCH)
    volume = str(volume or cfg.get("tts_volume") or DEFAULT_VOLUME)

    fixed = list(voices or [])
    voices = []
    if fixed:
        voices.extend(fixed)
    else:
        if voice:
            voices.append(voice)
        pref = cfg.get("tts_voice")
        if pref:
            voices.append(str(pref))
        for v in cfg.get("fallback_voices") or []:
            voices.append(str(v))
        voices.extend(DEFAULT_VOICES)

    seen = set()
    ordered = []
    for v in voices:
        if v and v not in seen:
            seen.add(v)
            ordered.append(v)

    # voicefix-20261006: KEIN SSML mehr. edge-tts >= 5 escaped eigenes SSML -> der Markup-Text
    # ("speak version 1.0 xmlns ...") wurde mitgesprochen (+~25 s Audio, +~4 s Synthese pro Antwort).
    # rate/pitch/volume gehen direkt an Communicate; Satzpausen setzt Edge selbst.
    if not re.fullmatch(r"[+-]\d+%", rate):
        rate = DEFAULT_RATE
    if not re.fullmatch(r"[+-]\d+Hz", pitch):
        pitch = DEFAULT_PITCH
    if not re.fullmatch(r"[+-]\d+%", volume):
        volume = DEFAULT_VOLUME

    last_err = None
    for v in ordered:
        async def _run_plain(vv=v):
            communicate = edge_tts.Communicate(
                text, vv, rate=rate, pitch=pitch, volume=volume
            )
            await asyncio.wait_for(communicate.save(str(out_path)), timeout=TTS_TIMEOUT_SEC)

        try:
            asyncio.run(_run_plain())
            if out_path.exists() and out_path.stat().st_size > 0:
                return {
                    "success": True,
                    "path": str(out_path),
                    "bytes": out_path.stat().st_size,
                    "voice": v,
                    "rate": rate,
                    "pitch": pitch,
                    "volume": volume,
                    "mode": "plain",
                }
            last_err = "empty_mp3"
        except Exception as e:
            last_err = e
            continue
    loc = _synthesize_local(text, out_path)
    if loc.get("success"):
        loc["edge_error"] = str(last_err or "")[:200]
        return loc
    return {"success": False, "error": str(last_err or "tts_failed"), "local_error": loc.get("error")}
