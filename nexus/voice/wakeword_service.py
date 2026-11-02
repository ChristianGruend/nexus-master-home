r"""NEXUS Weckwort-Dienst "Hey Nexus" (heynexus-20261006).

Lokal + kostenlos: Vosk (Kaldi) mit kleinem deutschen Modell (vosk-model-small-de-0.15, Apache-2.0)
im Grammatik-Modus als Keyword-Spotter am PC-Mikro. Nach dem Weckwort wird der Befehl bis zur
Stille aufgenommen und an NEXUS geschickt (POST /api/voice/command, Server-STT faster-whisper +
lokales Vosk-Transkript als Alternative). Antworten spricht Amala (Edge-TTS via /api/voice/tts).
Bei WhatsApp: Empfaenger + Text vorlesen, erst nach gesprochenem "Ja" senden; "Nein"/"Abbrechen"
oder 15 s Stille = verwerfen.

Mikro-Schonung: Pausiert (Mikro wird freigegeben), wenn der Schalter aus ist oder die Browser-
Live-Funktion aktiv ist; ignoriert Audio, solange NEXUS spricht. Status: GET /api/voice/wakeword/state.
Nur PC-Mikro - nichts am Handy-Mikro.

Start:  python nexus\voice\wakeword_service.py   (oder start_wakeword.ps1; bewusst NICHT per -m,
        sonst laedt nexus/voice/__init__ faster-whisper/ctranslate2 mit -> +RAM)
Test:   python nexus\voice\wakeword_service.py --test-wav t.wav [--answer-wav a.wav] [--no-speak] [--realtime]
"""
from __future__ import annotations

import argparse
import base64
import collections
import ctypes
import io
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path

# RAM-Schonung: BLAS/OpenMP auf 1 Thread (spart ~350 MB Commit + 11 Threads, RSS unveraendert)
for _k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "models" / "vosk" / "vosk-model-small-de-0.15"
CFG_PATH = ROOT / "workspace" / "voice" / "wakeword.json"
LOG_PATH = ROOT / "logs" / "wakeword.log"
VERSION = "heynexus-20261006"
SR = 16000
BLOCK = 1600  # 100 ms

WAKE_GRAMMAR = [
    "hey nexus", "hallo nexus", "hi nexus", "nexus",
    # Ablenker, damit normale Sprache nicht auf "nexus" gezwungen wird
    "hey", "hallo", "hi", "next", "news", "nix", "lexus", "sexus", "jetzt", "ja", "nein", "das", "die",
    "der", "und", "ich", "du", "es", "ist", "nicht", "so", "okay", "neu", "nächste", "mal", "[unk]",
]
YESNO_GRAMMAR = ["ja", "ja senden", "senden", "jawohl", "genau", "okay", "klar", "ja bitte", "nein",
                 "nein danke", "abbrechen", "stopp", "nicht senden", "verwerfen", "[unk]"]
YES_WORDS = {"ja", "senden", "jawohl", "genau", "okay", "klar"}
NO_WORDS = {"nein", "abbrechen", "stopp", "nicht", "verwerfen"}

DEFAULT_CFG = {"enabled": True, "device": None, "allow_bare_nexus": True, "beep": True,
               "server": "http://127.0.0.1:8299", "silence_s": 0.8, "max_command_s": 12,
               "answer_timeout_s": 15, "ffplay": ""}


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 1_000_000:
            LOG_PATH.replace(LOG_PATH.with_suffix(".log.1"))
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    try:
        cfg.update(json.loads(CFG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_default_cfg() -> None:
    if not CFG_PATH.exists():
        CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CFG_PATH.write_text(json.dumps(DEFAULT_CFG, indent=2), encoding="utf-8")
    else:  # fehlende Schluessel ergaenzen, vorhandene (z. B. enabled) behalten
        try:
            cur = json.loads(CFG_PATH.read_text(encoding="utf-8"))
            changed = False
            for k, v in DEFAULT_CFG.items():
                if k not in cur:
                    cur[k] = v
                    changed = True
            if changed:
                CFG_PATH.write_text(json.dumps(cur, indent=2), encoding="utf-8")
        except Exception:
            pass


# ------------------------------------------------------------------ HTTP
class Server:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def get(self, path: str, timeout: float = 3) -> dict | None:
        try:
            with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    def post(self, path: str, payload: dict, timeout: float = 30) -> tuple[str, bytes | dict | None]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ct = r.headers.get("Content-Type") or ""
                body = r.read()
                if "json" in ct:
                    return "json", json.loads(body.decode("utf-8"))
                return "bytes", body
        except Exception as e:
            return "error", {"error": type(e).__name__}


# ------------------------------------------------------------------ audio sources
class MicSource:
    def __init__(self, device=None):
        self.device = device
        self.q: queue.Queue = queue.Queue(maxsize=300)
        self.stream = None
        self.rate = SR
        self._resample = None

    def start(self) -> None:
        if self.stream is not None:
            return
        import sounddevice as sd

        def cb(indata, frames, t, status):
            x = indata[:, 0].copy()
            try:
                self.q.put_nowait(x)
            except queue.Full:
                try:
                    self.q.get_nowait()
                    self.q.put_nowait(x)
                except Exception:
                    pass
        try:
            self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=BLOCK,
                                         device=self.device, callback=cb)
            self.rate = SR
        except Exception:
            info = sd.query_devices(self.device, "input")
            self.rate = int(info["default_samplerate"])
            from math import gcd
            g = gcd(SR, self.rate)
            up, down = SR // g, self.rate // g
            from scipy.signal import resample_poly

            def cb2(indata, frames, t, status):
                y = resample_poly(indata[:, 0].astype(np.float32), up, down)
                cb(np.clip(y, -32768, 32767).astype(np.int16)[:, None], frames, t, status)
            self.stream = sd.InputStream(samplerate=self.rate, channels=1, dtype="int16",
                                         blocksize=int(self.rate / 10), device=self.device, callback=cb2)
        self.stream.start()
        log(f"Mikro geoeffnet (rate={self.rate}, device={self.device})")

    def stop(self) -> None:
        if self.stream is None:
            return
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        self.stream = None
        self.flush()
        log("Mikro freigegeben")

    def flush(self) -> None:
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    def read(self, timeout: float = 0.5):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def active(self) -> bool:
        return self.stream is not None


class WavSource(MicSource):
    """Speist WAV-Dateien (16 kHz mono int16) wie ein Mikro ein - fuer ehrliche Tests ohne echtes Mikro."""

    def __init__(self, files: list[str], realtime: bool = True, tail_s: float = 20.0):
        super().__init__()
        self.files = list(files)
        self.realtime = realtime
        self.tail_s = tail_s
        self.feed_log: list[tuple[str, float, float]] = []
        self._next = threading.Event()
        self.done = False

    def _load(self, f: str) -> np.ndarray:
        with wave.open(f, "rb") as w:
            assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2, "16k mono s16 noetig"
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)

    def feed_next(self) -> None:
        self._next.set()

    def start(self) -> None:
        if self.stream is not None:
            return
        self.stream = "wav"

        def run():
            for i, f in enumerate(self.files):
                if i > 0:
                    # bis zur naechsten Antwort: Raumstille einspeisen
                    t_wait = time.time() + 60
                    while not self._next.is_set() and time.time() < t_wait:
                        self.q.put(np.zeros(BLOCK, dtype=np.int16))
                        time.sleep(BLOCK / SR if self.realtime else 0.002)
                    self._next.clear()
                    time.sleep(0.3)
                x = self._load(f)
                t0 = time.time()
                for k in range(0, len(x), BLOCK):
                    blk = x[k:k + BLOCK]
                    if len(blk) < BLOCK:
                        blk = np.pad(blk, (0, BLOCK - len(blk)))
                    self.q.put(blk)
                    if self.realtime:
                        time.sleep(max(0.0, t0 + (k + BLOCK) / SR - time.time()))
                self.feed_log.append((Path(f).name, t0, time.time()))
            # danach Stille (wie ein ruhiger Raum)
            end = time.time() + self.tail_s
            while time.time() < end:
                self.q.put(np.zeros(BLOCK, dtype=np.int16))
                time.sleep(BLOCK / SR if self.realtime else 0.001)
            self.done = True
        threading.Thread(target=run, daemon=True).start()

    def stop(self) -> None:
        self.stream = None


# ------------------------------------------------------------------ playback
class Player:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.tmpdir = Path(tempfile.gettempdir()) / "nexus_wakeword"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.beep_path = self.tmpdir / "beep.wav"
        if not self.beep_path.exists():
            t = np.arange(int(0.12 * SR)) / SR
            tone = (np.sin(2 * np.pi * 880 * t) * np.minimum(1, (0.12 - t) * 40) * np.minimum(1, t * 200) * 4000).astype(np.int16)
            with wave.open(str(self.beep_path), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(tone.tobytes())

    def beep(self) -> None:
        try:
            import winsound
            winsound.PlaySound(str(self.beep_path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:
            pass

    def play_mp3(self, data: bytes) -> bool:
        p = self.tmpdir / f"say_{int(time.time() * 1000)}.mp3"
        p.write_bytes(data)
        ok = False
        try:
            winmm = ctypes.windll.winmm
            buf = ctypes.create_unicode_buffer(256)
            alias = "nxwake" + str(int(time.time() * 1000) % 100000)
            if winmm.mciSendStringW(f'open "{p}" type mpegvideo alias {alias}', buf, 255, 0) == 0:
                ok = winmm.mciSendStringW(f"play {alias} wait", buf, 255, 0) == 0
                winmm.mciSendStringW(f"close {alias}", buf, 255, 0)
        except Exception:
            ok = False
        if not ok and self.cfg.get("ffplay") and Path(self.cfg["ffplay"]).is_file():
            try:
                subprocess.run([self.cfg["ffplay"], "-nodisp", "-autoexit", "-loglevel", "quiet", str(p)],
                               timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                ok = True
            except Exception:
                ok = False
        try:
            p.unlink()
        except Exception:
            pass
        return ok


# ------------------------------------------------------------------ service
def to_wav_bytes(x: np.ndarray) -> bytes:
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(x.astype(np.int16).tobytes())
    return bio.getvalue()


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float32) ** 2))) if len(x) else 0.0


class WakewordService:
    def __init__(self, source: MicSource, cfg: dict, speak: bool = True, test: bool = False):
        from vosk import KaldiRecognizer, Model, SetLogLevel
        SetLogLevel(-1)
        self.KR = KaldiRecognizer
        t0 = time.time()
        self.model = Model(str(MODEL_DIR))
        log(f"Vosk-Modell geladen in {time.time() - t0:.2f}s")
        self.cfg = cfg
        self.src = source
        self.srv = Server(cfg.get("server") or DEFAULT_CFG["server"])
        self.player = Player(cfg)
        self.speak_enabled = speak
        self.test = test
        self.ring: collections.deque = collections.deque(maxlen=int(10 * SR / BLOCK))  # 10 s
        self.samples = 0          # gesamt eingespeiste Samples
        self.rec_base = 0         # Sample-Index, ab dem der aktuelle Erkenner laeuft
        self.rec = None
        self.state = {"enabled": True, "paused": False, "release_mic": False, "reason": "init", "server": False}
        self._last_poll = 0.0
        self._last_hb = 0.0
        self.last_wake = None
        self.quiet_blocks = 0
        self.gated = False
        self.floor = 200.0
        self.metrics: dict = {}
        self.stop_flag = False
        try:
            import psutil
            self.proc = psutil.Process()
            self.proc.cpu_percent(None)
        except Exception:
            self.proc = None
        self.new_recognizer()

    # -- helpers
    def new_recognizer(self) -> None:
        self.rec = self.KR(self.model, SR, json.dumps(WAKE_GRAMMAR, ensure_ascii=False))
        self.rec.SetWords(True)
        self.rec_base = self.samples

    def push_ring(self, blk: np.ndarray) -> None:
        self.ring.append((self.samples, blk))
        self.samples += len(blk)

    def ring_slice(self, start_sample: int) -> np.ndarray:
        parts = [b for (s, b) in self.ring if s + len(b) > start_sample]
        if not parts:
            return np.zeros(0, dtype=np.int16)
        first_s = next(s for (s, b) in self.ring if s + len(b) > start_sample)
        x = np.concatenate(parts)
        return x[max(0, start_sample - first_s):]

    def update_floor(self) -> None:
        vals = [rms(b) for (_, b) in list(self.ring)[-50:]]
        if vals:
            self.floor = float(np.percentile(vals, 20))

    def voice_thr(self) -> float:
        return max(self.floor * 3.0, 300.0)

    def poll_state(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_poll < 1.5:
            return
        self._last_poll = now
        st = self.srv.get("/api/voice/wakeword/state", timeout=2)
        if st is None:
            self.state = {"enabled": False, "paused": True, "release_mic": True, "reason": "server_offline", "server": False}
        else:
            self.state = {"enabled": st.get("enabled", True), "paused": st.get("paused", False),
                          "release_mic": st.get("release_mic", False), "reason": st.get("reason"), "server": True}
        if not self.test and now - self._last_hb > 10:
            self._last_hb = now
            self.heartbeat()

    def heartbeat(self) -> None:
        rss = cpu = None
        if self.proc is not None:
            try:
                rss = round(self.proc.memory_info().rss / 1048576, 1)
                import psutil
                cpu = round(self.proc.cpu_percent(None) / max(1, psutil.cpu_count() or 1), 2)
            except Exception:
                pass
        state = "paused:" + str(self.state.get("reason")) if self.state.get("paused") else ("listening" if self.src.active else "idle")
        self.srv.post("/api/voice/wakeword/heartbeat", {"pid": os.getpid(), "rss_mb": rss, "cpu_pct": cpu,
                                                        "state": state, "engine": "vosk-small-de-0.15 grammar",
                                                        "mic": self.src.active, "last_wake": self.last_wake,
                                                        "version": VERSION}, timeout=3)

    def say(self, text: str) -> None:
        if not text:
            return
        t0 = time.time()
        kind, data = self.srv.post("/api/voice/tts", {"text": text, "source": "wakeword"}, timeout=30)
        self.metrics.setdefault("tts_fetch_ms", []).append(int((time.time() - t0) * 1000))
        if kind != "bytes" or not data:
            log(f"TTS fehlgeschlagen ({kind})")
            return
        if self.speak_enabled:
            self.player.play_mp3(data)  # blockiert; Mikro-Audio waehrenddessen verwerfen
        if not self.test:
            self.src.flush()

    def read_block(self, timeout: float = 0.5):
        blk = self.src.read(timeout)
        if blk is not None:
            self.push_ring(blk)
        return blk

    def capture_until_silence(self, voice_started: bool, wait_voice_s: float, silence_s: float, max_s: float,
                              feed=None) -> tuple[np.ndarray, bool]:
        """Nimmt ab jetzt auf, bis nach Sprache `silence_s` Stille kommt. Beruecksichtigt Stille, die schon im Ring liegt."""
        thr = self.voice_thr()
        out = []
        t_start = time.time()
        quiet_s = 0.0
        # schon vorhandene Stille am Ring-Ende mitzaehlen
        for (_, b) in reversed(list(self.ring)):
            if rms(b) < thr:
                quiet_s += len(b) / SR
            else:
                break
        heard = voice_started
        got = 0.0
        while True:
            if heard and quiet_s >= silence_s:
                break
            if not heard and got >= wait_voice_s:
                break
            if got >= max_s:
                break
            blk = self.read_block(1.0)
            if blk is None:
                if time.time() - t_start > max_s + 2:
                    break
                continue
            out.append(blk)
            got += len(blk) / SR
            if feed is not None:
                feed.AcceptWaveform(blk.tobytes())
            if rms(blk) >= thr:
                heard = True
                quiet_s = 0.0
            else:
                quiet_s += len(blk) / SR
        return (np.concatenate(out) if out else np.zeros(0, dtype=np.int16)), heard

    def vosk_free(self, x: np.ndarray) -> str:
        if len(x) == 0:
            return ""
        r = self.KR(self.model, SR)
        r.AcceptWaveform(x.astype(np.int16).tobytes())
        return json.loads(r.FinalResult()).get("text", "")

    def listen_yesno(self, timeout_s: float) -> str:
        r = self.KR(self.model, SR, json.dumps(YESNO_GRAMMAR, ensure_ascii=False))
        r.SetWords(True)
        end = time.time() + timeout_s
        while time.time() < end:
            blk = self.read_block(0.5)
            if blk is None:
                continue
            if r.AcceptWaveform(blk.tobytes()):
                words = set(json.loads(r.Result()).get("text", "").split())
                if words & NO_WORDS:
                    return "nein"
                if words & YES_WORDS:
                    return "ja"
        words = set(json.loads(r.FinalResult()).get("text", "").split())
        if words & NO_WORDS:
            return "nein"
        if words & YES_WORDS:
            return "ja"
        return "timeout"

    def listen_free(self, timeout_s: float) -> str:
        x, heard = self.capture_until_silence(False, timeout_s, max(1.0, float(self.cfg.get("silence_s", 0.8))), 20)
        if not heard:
            return "timeout"
        kind, resp = self.srv.post("/api/voice/stt", {"audio_b64": base64.b64encode(to_wav_bytes(x)).decode(),
                                                      "filename": "answer.wav", "language": "de"}, timeout=30)
        if kind == "json" and resp.get("success") and (resp.get("text") or "").strip():
            return resp["text"].strip()
        return self.vosk_free(x) or "timeout"

    # -- core
    def is_wake(self, res: dict) -> tuple[int, int] | None:
        words = res.get("result") or []
        for i, w in enumerate(words):
            if w.get("word") != "nexus":
                continue
            if i > 0 and words[i - 1].get("word") in ("hey", "hallo", "hi"):
                return i - 1, i
            if i == 0 and self.cfg.get("allow_bare_nexus", True) and float(w.get("conf", 0)) >= 0.9:
                return i, i
        return None

    def handle_wake(self, res: dict, idx: tuple[int, int], t_detect: float) -> None:
        words = res["result"]
        w_start = float(words[idx[0]]["start"])
        n_end = float(words[idx[1]]["end"])
        u_end = float(words[-1]["end"])
        self.last_wake = time.strftime("%H:%M:%S")
        log(f"Weckwort erkannt ({' '.join(w['word'] for w in words)})")
        if self.cfg.get("beep", True) and self.speak_enabled:
            self.player.beep()
        start_sample = self.rec_base + int(max(0.0, w_start - 0.3) * SR)
        in_utt = (u_end - n_end) > 0.45
        self.update_floor()
        # lokales Vosk-Volltranskript (Alternative zu Whisper) schon waehrend der Aufnahme mitlaufen lassen
        free = self.KR(self.model, SR)
        free.AcceptWaveform(self.ring_slice(start_sample).astype(np.int16).tobytes())
        tail, heard = self.capture_until_silence(in_utt, 4.0, float(self.cfg.get("silence_s", 0.8)),
                                                 float(self.cfg.get("max_command_s", 12)), feed=free)
        audio = self.ring_slice(start_sample)
        t_cap = time.time()
        self.metrics["capture_s"] = round(len(audio) / SR, 2)
        self.metrics["t_wake_to_capture_ms"] = int((t_cap - t_detect) * 1000)
        alt = json.loads(free.FinalResult()).get("text", "")
        self.process_command(audio, t_detect, retry=True, alt=alt)

    def process_command(self, audio: np.ndarray, t_detect: float, retry: bool, alt: str | None = None) -> None:
        t1 = time.time()
        if alt is None:
            alt = self.vosk_free(audio)
        self.metrics["vosk_free_ms"] = int((time.time() - t1) * 1000)
        t2 = time.time()
        kind, resp = self.srv.post("/api/voice/command", {
            "audio_b64": base64.b64encode(to_wav_bytes(audio)).decode(), "filename": "cmd.wav",
            "alt_texts": [alt] if alt else [], "source": "wakeword", "chat_fallback": True, "llm_fallback": True,
        }, timeout=120)
        self.metrics["command_ms"] = int((time.time() - t2) * 1000)
        self.metrics["t_wake_to_action_ms"] = int((time.time() - t_detect) * 1000)
        self.metrics.setdefault("t_action_abs", time.time())
        if kind != "json":
            log(f"Befehl fehlgeschlagen: {resp}")
            self.say("NEXUS ist gerade nicht erreichbar.")
            return
        self.metrics["intent"] = resp.get("intent")
        self.metrics["result_success"] = resp.get("success")
        if self.test:
            self.metrics["heard"] = resp.get("heard")
            self.metrics["alt"] = alt
            self.metrics["reply"] = resp.get("reply")
        log(f"Befehl: intent={resp.get('intent')} ok={resp.get('success')} via={resp.get('via')} "
            f"server_ms={resp.get('ms')} gesamt_ms={self.metrics['t_wake_to_action_ms']}")
        if resp.get("listen_again") and retry:
            self.say(resp.get("reply") or "Ja?")
            x, heard = self.capture_until_silence(False, 6.0, float(self.cfg.get("silence_s", 0.8)), 12)
            if heard:
                self.process_command(x, time.time(), retry=False)
            return
        self.say(resp.get("reply") or "")
        self.confirm_loop(resp)

    def confirm_loop(self, resp: dict) -> None:
        guard = 0
        while resp.get("confirm") and guard < 5:
            guard += 1
            c = resp["confirm"]
            to = float(self.cfg.get("answer_timeout_s", 15))
            if self.test and isinstance(self.src, WavSource):
                self.src.feed_next()
            if c.get("kind") == "confirm_send":
                ans = self.listen_yesno(to)
            else:
                ans = self.listen_free(to)
            log(f"Antwort auf {c.get('kind')}: {ans if c.get('kind') == 'confirm_send' else ('timeout' if ans == 'timeout' else 'text')}")
            self.metrics.setdefault("answers", []).append(ans if c.get("kind") == "confirm_send" else "text")
            kind, resp = self.srv.post("/api/voice/confirm", {"id": c["id"], "answer": ans}, timeout=60)
            if kind != "json":
                self.say("Bestätigung fehlgeschlagen. Nichts gesendet.")
                return
            self.metrics["confirm_result"] = resp.get("result")
            self.metrics["confirm_reply"] = resp.get("reply")
            self.say(resp.get("reply") or "")

    def step(self) -> None:
        blk = self.read_block(0.5)
        if blk is None:
            return
        if self.state.get("paused"):
            return  # NEXUS spricht -> Audio verwerfen
        lvl = rms(blk)
        if lvl < max(self.floor * 2.0, 120.0):
            self.quiet_blocks += 1
        else:
            self.quiet_blocks = 0
            if self.gated:  # nach Stille wieder einsteigen: kurzes Pre-Roll mitgeben
                self.gated = False
                self.new_recognizer()
                pre = list(self.ring)[-4:-1]
                if pre:
                    self.rec_base = pre[0][0]
                    for (_, b) in pre:
                        self.rec.AcceptWaveform(b.tobytes())
        if self.quiet_blocks > 20 and not self.gated:
            # >2 s Stille: Erkenner abschliessen und pausieren (spart CPU im Leerlauf)
            res = json.loads(self.rec.FinalResult())
            self.gated = True
            self.update_floor()
            if res.get("result"):
                idx = self.is_wake(res)
                if idx:
                    self.handle_wake(res, idx, time.time())
                    self.new_recognizer()
            return
        if self.gated:
            return
        if self.rec.AcceptWaveform(blk.tobytes()):
            res = json.loads(self.rec.Result())
            idx = self.is_wake(res)
            if idx:
                self.handle_wake(res, idx, time.time())
                self.new_recognizer()
                self.gated = False
                self.quiet_blocks = 0

    def run(self, max_seconds: float | None = None) -> None:
        t_end = time.time() + max_seconds if max_seconds else None
        log(f"Weckwort-Dienst {VERSION} laeuft (pid {os.getpid()})")
        while not self.stop_flag:
            if t_end and time.time() > t_end:
                break
            if not self.test:
                self.poll_state()
                if not self.state.get("enabled") or self.state.get("release_mic"):
                    if self.src.active:
                        self.src.stop()
                    time.sleep(1.0)
                    continue
            if not self.src.active:
                self.src.start()
                self.new_recognizer()
            try:
                self.step()
            except Exception as e:
                log(f"Fehler in step: {type(e).__name__}: {e}")
                time.sleep(0.5)
            if self.test and isinstance(self.src, WavSource) and self.src.done:
                break


def single_instance() -> bool:
    try:
        k32 = ctypes.windll.kernel32
        h = k32.CreateMutexW(None, False, "Local\\NexusHeyNexusWakeword")
        globals()["_MUTEX"] = h
        return k32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-wav")
    ap.add_argument("--answer-wav", action="append", default=[])
    ap.add_argument("--no-speak", action="store_true")
    ap.add_argument("--realtime", action="store_true")
    ap.add_argument("--list-devices", action="store_true")
    args = ap.parse_args()
    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return 0
    save_default_cfg()
    cfg = load_cfg()
    if args.test_wav:
        src = WavSource([args.test_wav] + args.answer_wav, realtime=args.realtime, tail_s=8)
        svc = WakewordService(src, cfg, speak=not args.no_speak, test=True)
        t0 = time.time()
        svc.run(max_seconds=120)
        svc.metrics["wall_s"] = round(time.time() - t0, 2)
        if src.feed_log:
            name, fs, fe = src.feed_log[0]
            # Testdateien haben 1.5 s Stille am Ende -> Sprachende = Feed-Ende - 1.5 s
            if "t_action_abs" in svc.metrics:
                svc.metrics["speech_end_to_action_ms"] = int((svc.metrics["t_action_abs"] - (fe - 1.5)) * 1000)
        svc.metrics.pop("t_action_abs", None)
        if svc.proc is not None:
            svc.metrics["rss_mb"] = round(svc.proc.memory_info().rss / 1048576, 1)
        print("TEST_METRICS " + json.dumps(svc.metrics, ensure_ascii=False))
        return 0
    if not single_instance():
        log("Dienst laeuft bereits - Abbruch")
        return 1
    svc = WakewordService(MicSource(cfg.get("device")), cfg, speak=True)
    try:
        svc.run()
    except KeyboardInterrupt:
        pass
    finally:
        svc.src.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
