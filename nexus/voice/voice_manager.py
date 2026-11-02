from __future__ import annotations
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from nexus.common.paths import WORKSPACE, ensure_dirs
from . import tts as tts_mod
from . import stt as stt_mod
from . import speak_pc as speak_pc_mod
from nexus.common.response import envelope, attach_ok

_TTS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="nexus_tts")
_SPEAK_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nexus_speak")

class VoiceManager:
    def __init__(self) -> None:
        ensure_dirs()
        self.out_dir = WORKSPACE / "media" / "tts"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._speak_fut = None

    def status(self) -> dict:
        return envelope(
            True,
            tts={"edge_tts": tts_mod.available()},
            stt=stt_mod.status(),
            speak_pc=True,
        )

    def _cleanup_tts(self, max_age_sec: int = 600) -> None:
        now = time.time()
        try:
            for f in self.out_dir.glob("tts_*.mp3"):
                try:
                    if now - f.stat().st_mtime > max_age_sec:
                        f.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    def tts(self, text: str, **voice_opts) -> dict:
        # voicefix-20261006: eigene Datei pro Anfrage statt gemeinsamer last.mp3
        # (ThreadingHTTPServer + 2 TTS-Worker -> parallele Anfragen ueberschrieben sich gegenseitig)
        self._cleanup_tts()
        out = self.out_dir / f"tts_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.mp3"
        fut = _TTS_POOL.submit(tts_mod.synthesize, text, out, **voice_opts)
        try:
            return attach_ok(fut.result(timeout=14))
        except FuturesTimeout:
            return envelope(False, error="tts_timeout", reply="TTS Timeout")
        except Exception as e:
            return envelope(False, error=str(e))

    def speak_pc(self, text: str, wait: bool = True) -> dict:
        """Spricht auf PC-Lautsprechern (Windows SAPI). Default wait=True (sync bis fertig)."""
        # Vorheriges Sprechen abbrechen geht mit SAPI schlecht â€” queue one
        fut = _SPEAK_POOL.submit(speak_pc_mod.speak, text)
        if wait:
            try:
                return attach_ok(fut.result(timeout=90))
            except FuturesTimeout:
                return envelope(False, error="speak_timeout")
        self._speak_fut = fut
        return envelope(True, mode="windows_sapi_async", queued=True)

