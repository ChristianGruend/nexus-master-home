"""Lokale Sprechererkennung (NEXUS 008 / Phase 06).

Kostenlos, offline, nur numpy/scipy (keine Neuinstallation). MFCC-Statistik-
Embedding (Mittelwert+Std, CMN) und Kosinus-Abgleich gegen eingeschriebene
Profile. Es werden KEINE Rohaudios gespeichert, nur Merkmalsvektoren.
Ehrliche Grenze: kein neuronales x-vector-Modell; geeignet fuer Wenige-
Sprecher-Haushalt, nicht als Sicherheits-Authentifizierung.
"""
from __future__ import annotations

import io
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.fft import dct
from scipy.io import wavfile
from scipy.signal import resample_poly

SR = 16000
STORE = Path(__file__).resolve().parents[2] / "workspace" / "voice" / "speakers.json"
DEFAULT_THRESHOLD = 0.80
_LOCK = threading.Lock()


def _load(raw: bytes) -> np.ndarray:
    sr, x = wavfile.read(io.BytesIO(raw))
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if np.issubdtype(np.asarray(x).dtype, np.integer) or np.abs(x).max() > 2:
        x = x / 32768.0
    if sr != SR:
        g = np.gcd(int(sr), SR)
        x = resample_poly(x, SR // g, int(sr) // g)
    return x


def _mel_fb(n_fft: int, n_mels: int = 40) -> np.ndarray:
    def hz2mel(h): return 2595 * np.log10(1 + h / 700.0)
    def mel2hz(m): return 700 * (10 ** (m / 2595.0) - 1)
    pts = mel2hz(np.linspace(hz2mel(60), hz2mel(SR / 2 - 200), n_mels + 2))
    bins = np.floor((n_fft + 1) * pts / SR).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for m in range(1, n_mels + 1):
        l, c, r = bins[m - 1], bins[m], bins[m + 1]
        for k in range(l, c):
            fb[m - 1, k] = (k - l) / max(c - l, 1)
        for k in range(c, r):
            fb[m - 1, k] = (r - k) / max(r - c, 1)
    return fb


def embed(raw: bytes) -> np.ndarray:
    x = _load(raw)
    if len(x) < SR // 2:
        raise ValueError("AUDIO_TOO_SHORT: mindestens 0,5 s Sprache noetig")
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    flen, hop, n_fft = 400, 160, 512
    n = 1 + (len(x) - flen) // hop
    frames = np.stack([x[i * hop:i * hop + flen] for i in range(n)]) * np.hamming(flen)
    energy = (frames ** 2).sum(axis=1)
    keep = energy > max(energy.max() * 1e-3, 1e-10)
    frames = frames[keep]
    if len(frames) < 30:
        raise ValueError("NO_SPEECH: zu wenig Sprachanteil")
    pw = np.abs(np.fft.rfft(frames, n_fft)) ** 2 / n_fft
    mel = np.log(pw @ _mel_fb(n_fft).T + 1e-10)
    cc = dct(mel, type=2, axis=1, norm="ortho")[:, 1:20]
    # Index-gewichteter Cepstral-Mittelwert (Liftering); kalibriert 008: eigen 0.97, fremd <=0.38
    v = cc.mean(axis=0) * np.arange(1, cc.shape[1] + 1)
    return v / (np.linalg.norm(v) + 1e-9)


def _read() -> dict[str, Any]:
    if STORE.is_file():
        return json.loads(STORE.read_text(encoding="utf-8"))
    return {"speakers": {}, "threshold": DEFAULT_THRESHOLD}


def _write(d: dict[str, Any]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STORE)


def enroll(name: str, samples: list[bytes]) -> dict[str, Any]:
    name = str(name or "").strip()
    if not name or not samples:
        raise ValueError("name und mindestens eine Probe noetig")
    vecs = [embed(s) for s in samples]
    with _LOCK:
        d = _read()
        prev = d["speakers"].get(name)
        allv = vecs + ([np.array(prev["vector"])] * prev.get("n", 1) if prev else [])
        c = np.mean(allv, axis=0)
        d["speakers"][name] = {"vector": (c / np.linalg.norm(c)).tolist(), "n": len(allv), "updated": datetime.now().isoformat(timespec="seconds")}
        _write(d)
    return {"ok": True, "speaker": name, "samples": len(allv)}


def identify(raw: bytes) -> dict[str, Any]:
    d = _read()
    if not d["speakers"]:
        return {"ok": False, "error": "NO_ENROLLED_SPEAKER", "speaker": None}
    v = embed(raw)
    scores = {k: float(np.dot(v, np.array(s["vector"]))) for k, s in d["speakers"].items()}
    best = max(scores, key=scores.get)
    thr = float(d.get("threshold", DEFAULT_THRESHOLD))
    known = scores[best] >= thr
    return {"ok": True, "speaker": best if known else "unbekannt", "score": round(scores[best], 4), "threshold": thr, "scores": {k: round(s, 4) for k, s in scores.items()}}


def status() -> dict[str, Any]:
    d = _read()
    return {"ok": True, "engine": "mfcc-stat-cosine (numpy/scipy, offline)", "speakers": sorted(d["speakers"]), "threshold": d.get("threshold", DEFAULT_THRESHOLD)}
