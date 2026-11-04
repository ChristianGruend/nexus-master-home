"""Erfahrungsgedaechtnis: workspace/swarm/experience.jsonl mit Bewertung + Aehnlichkeitssuche."""
from __future__ import annotations
import json, re, time, threading
from pathlib import Path

_lock = threading.Lock()


def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-zA-Z0-9äöüß_]{3,}", (s or "").lower())}


def add(path: Path, rec: dict) -> dict:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def similar(path: Path, task: str, k: int = 3) -> list:
    q = _words(task)
    if not q:
        return []
    scored = []
    for r in load(path):
        w = _words(r.get("task", ""))
        if not w:
            continue
        sim = len(q & w) / len(q | w)
        if sim >= 0.2:
            scored.append((sim + 0.1 * float(r.get("score", 0)), sim, r))
    scored.sort(key=lambda x: -x[0])
    return [{**r, "similarity": round(s, 2)} for _, s, r in scored[:k]]
