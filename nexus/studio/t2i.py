"""Studio Text-zu-Bild (ST05, 2026-10-29).

Echter, lokaler, kostenloser Weg: vorhandenes CPU-Skript G:\\meine-ki\\t2i\\gen_cpu.py
(sd-turbo, ca. 24 s/Bild) im eigenen Python-3.12-venv als Subprozess. Kein ComfyUI,
kein DirectML (ONNX/DML schlug fehl), keine Installation, kein Download (HF offline).
Fehlt venv/Skript -> ehrlicher Fehler, nichts wird erfunden.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from nexus.common.paths import WORKSPACE

T2I_ROOT = Path(os.environ.get("NEXUS_T2I_ROOT", r"G:\meine-ki\t2i"))
T2I_PY = T2I_ROOT / "venv" / "Scripts" / "python.exe"
T2I_SCRIPT = T2I_ROOT / "gen_cpu.py"
OUT_DIR = WORKSPACE / "studio" / "t2i"
DEFAULT_TIMEOUT = 300
MAX_PROMPT = 500


def feasibility() -> dict[str, Any]:
    """Prueft nur Vorhandensein (kein Start): venv-Python + gen_cpu.py + HF-Cache."""
    missing = [str(p) for p in (T2I_PY, T2I_SCRIPT) if not p.is_file()]
    return {"ok": not missing, "python": str(T2I_PY), "script": str(T2I_SCRIPT),
            "hf_home": str(T2I_ROOT / "hf"), "missing": missing, "device": "cpu",
            "engine": "sd-turbo via diffusers (gen_cpu.py)"}


def _slug(prompt: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (prompt or "").lower()).strip("-")
    return (s[:40] or "bild")


def generate(prompt: str, *, width: int = 512, height: int = 512, steps: int = 4, seed: int | None = None,
             timeout: int = DEFAULT_TIMEOUT, out_dir: Path | None = None) -> dict[str, Any]:
    prompt = (prompt or "").strip()
    if not prompt:
        return {"success": False, "error": "EMPTY_PROMPT"}
    prompt = prompt[:MAX_PROMPT]
    feas = feasibility()
    if not feas["ok"]:
        return {"success": False, "error": "T2I_ENV_MISSING", "missing": feas["missing"]}
    width = max(256, min(1024, int(width) // 64 * 64))
    height = max(256, min(1024, int(height) // 64 * 64))
    steps = max(1, min(8, int(steps)))
    seed = int(seed if seed is not None else int(time.time()) % 100000)
    od = Path(out_dir or OUT_DIR)
    od.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = od / f"t2i_{stamp}_{_slug(prompt)}.png"
    job = od / f"job_{stamp}.json"
    job.write_text(json.dumps([{"prompt": prompt, "out": str(out), "w": width, "h": height,
                                "steps": steps, "seed": seed}], ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, HF_HOME=str(T2I_ROOT / "hf"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    t0 = time.time()
    try:
        proc = subprocess.run([str(T2I_PY), str(T2I_SCRIPT), str(job)], capture_output=True, text=True,
                              timeout=timeout, env=env, cwd=str(T2I_ROOT),
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "T2I_TIMEOUT", "timeout_s": timeout}
    except OSError as e:
        return {"success": False, "error": "T2I_START_FAILED", "detail": str(e)}
    finally:
        try:
            job.unlink()
        except OSError:
            pass
    total = round(time.time() - t0, 1)
    info: dict[str, Any] = {}
    for line in (proc.stdout or "").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("ok"):
            info = obj
    if proc.returncode != 0 or not info or not out.is_file() or out.stat().st_size == 0:
        return {"success": False, "error": "T2I_FAILED", "returncode": proc.returncode,
                "stderr": (proc.stderr or "")[-800:], "total_s": total}
    return {"success": True, "path": str(out), "bytes": out.stat().st_size, "width": width, "height": height,
            "steps": steps, "seed": seed, "gen_s": info.get("s"), "total_s": total, "device": "cpu",
            "engine": "sd-turbo (gen_cpu.py)"}
