"""NEXUS All core: Bibliothek, Download-Warteschlange (yt-dlp/ffmpeg), Range-Streaming. Alles auf G:."""
from __future__ import annotations

import json, os, re, shutil, sqlite3, subprocess, threading, time, uuid
from pathlib import Path
from typing import Any

from nexus.common.paths import WORKSPACE

LIB = WORKSPACE / "media" / "nexusall"
DIRS = {"video": LIB / "Videos", "audio": LIB / "Musik", "subs": LIB / "Untertitel", "thumbs": LIB / "Thumbs"}
STATE = WORKSPACE / "nexusall"
DB = STATE / "nexusall.db"
TMP = WORKSPACE / "tmp" / "nexusall"
TOOLS = Path(os.environ.get("NEXUS_TOOLS_DIR", r"G:\meine-ki\tools"))
YTDLP = TOOLS / "yt-dlp" / "yt-dlp.exe"
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v"}
AUDIO_EXT = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac"}
MIN_FREE_GB = 10
QUALITY = {
    "best": ["-f", "bv*+ba/b", "--merge-output-format", "mp4"],
    "1080": ["-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4"],
    "720": ["-f", "bv*[height<=720]+ba/b[height<=720]/b", "--merge-output-format", "mp4"],
    "audio": ["-x", "--audio-format", "mp3", "--audio-quality", "0"],
    "audio_m4a": ["-f", "ba[ext=m4a]/ba", "-x", "--audio-format", "m4a"],
}
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"  # NA06
TRASH = WORKSPACE / "media" / ".papierkorb"  # NA01
_PROG = re.compile(r"NXPROG\s+([\d.]+)%")
_PROG2 = re.compile(r"NXP2\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)")  # NA02
_lock = threading.Lock()
_worker: threading.Thread | None = None


def ensure_dirs() -> None:
    for d in (*DIRS.values(), STATE, TMP):
        d.mkdir(parents=True, exist_ok=True)


def ffmpeg_path() -> str | None:
    cand = [TOOLS / "ffmpeg" / "bin" / "ffmpeg.exe"]
    w = shutil.which("ffmpeg")
    if w:
        cand.append(Path(w))
    la = Path(os.environ.get("LOCALAPPDATA", ""))
    cand += list((la / "Microsoft" / "WinGet" / "Packages").glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"))
    cand += list((la / "hermes" / "tools").glob("ffmpeg-*/bin/ffmpeg.exe"))
    for c in cand:
        if c and Path(c).is_file():
            return str(c)
    return None


def _db() -> sqlite3.Connection:
    ensure_dirs()
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, url TEXT, quality TEXT, subs TEXT,
                 status TEXT, progress REAL, file TEXT, error TEXT, created REAL, updated REAL)""")
    c.execute("CREATE TABLE IF NOT EXISTS resume(rel TEXT PRIMARY KEY, pos REAL, updated REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS meta(rel TEXT PRIMARY KEY, mtime REAL, duration REAL)")  # NA03
    cols = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
    for col, typ in (("speed", "REAL"), ("eta", "REAL"), ("downloaded", "REAL"), ("total", "REAL"), ("loudnorm", "INTEGER")):  # NA02/NA06
        if col not in cols:
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
    return c


def _env() -> dict:
    env = dict(os.environ)
    TMP.mkdir(parents=True, exist_ok=True)
    env.update({"TEMP": str(TMP), "TMP": str(TMP), "PYTHONIOENCODING": "utf-8"})
    return env


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    return subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=_env(), creationflags=flags)


def _emit(kind: str, text: str, ok: bool | None = None, data: dict | None = None) -> None:
    try:
        from nexus.common import live_events as _le
        _le.emit("NEXUS All", kind, text, ok=ok, data=data)
    except Exception:
        pass


def health() -> dict:
    ensure_dirs()
    ver = None
    if YTDLP.is_file():
        try:
            ver = _run([str(YTDLP), "--version"], 60).stdout.strip() or None
        except Exception:
            ver = None
    ff = ffmpeg_path()
    free = shutil.disk_usage(str(LIB)).free / 1e9
    with _db() as c:
        q = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
    return {"success": bool(ver and ff), "ytdlp": str(YTDLP) if YTDLP.is_file() else None, "ytdlp_version": ver,
            "ffmpeg": ff, "library": str(LIB), "free_gb": round(free, 1), "queue": q}


# ---------------- Bibliothek ----------------
def _kind(p: Path) -> str | None:
    e = p.suffix.lower()
    if e == ".ogg" and any(x.lower() in ("videos", "filme") for x in p.parts):  # Ogg-Theora im Video-Ordner = Film
        return "video"
    return "video" if e in VIDEO_EXT else ("audio" if e in AUDIO_EXT else None)


def library(q: str = "", kind: str = "") -> dict:
    ensure_dirs()
    media_root = WORKSPACE / "media"
    items = []
    with _db() as c:
        resume = {r["rel"]: r["pos"] for r in c.execute("SELECT rel,pos FROM resume")}
    for p in media_root.rglob("*"):
        if not p.is_file() or p.parent == DIRS["thumbs"] or TRASH in p.parents:
            continue
        k = _kind(p)
        if not k or (kind and k != kind) or (q and q.lower() not in p.name.lower()):
            continue
        rel = p.relative_to(media_root).as_posix()
        st = p.stat()
        items.append({"id": rel, "name": p.stem, "kind": k, "size_mb": round(st.st_size / 1e6, 1),
                      "mtime": st.st_mtime, "url": "/api/nexusall/stream/" + rel,
                      "thumb": "/api/nexusall/thumb/" + rel if k == "video" else None,
                      "resume": resume.get(rel, 0), "duration": duration(p, rel), "tab": "musik" if k == "audio" else "filme"})
    items.sort(key=lambda i: i["mtime"], reverse=True)
    return {"success": True, "count": len(items), "items": items, "root": str(media_root)}


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _kill_tree(p) -> None:
    """yt-dlp.exe (onefile) startet ein Kindprozess -> ganzen Baum beenden."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            p.kill()
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def parse_progress(line: str) -> dict | None:
    """NA02: yt-dlp progress-template -> pct/speed(B/s)/eta(s)/downloaded/total."""
    m = _PROG2.search(line or "")
    if not m:
        m1 = _PROG.search(line or "")
        return {"progress": float(m1.group(1))} if m1 else None
    st, dl, tot, est, spd, eta = m.groups()
    dlb, total = _num(dl), _num(tot) or _num(est)
    out = {"speed": _num(spd), "eta": _num(eta), "downloaded": dlb, "total": total}
    if dlb is not None and total:
        out["progress"] = round(min(100.0, dlb * 100.0 / total), 1)
    if st.strip() == "finished":
        out["progress"] = 100.0
    return out


def ffprobe_path() -> str | None:
    ff = ffmpeg_path()
    if not ff:
        return None
    fp = Path(ff).with_name("ffprobe.exe" if ff.lower().endswith(".exe") else "ffprobe")
    return str(fp) if fp.is_file() else None


def duration(p: Path, rel: str) -> float | None:
    """NA03: Dauer per ffprobe, gecacht in meta (mtime-gebunden)."""
    mt = p.stat().st_mtime
    with _db() as c:
        r = c.execute("SELECT mtime,duration FROM meta WHERE rel=?", (rel,)).fetchone()
    if r and r["mtime"] == mt:
        return r["duration"]
    fpb = ffprobe_path()
    d = None
    if fpb:
        try:
            out = _run([fpb, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(p)], 30).stdout
            d = round(float(out.strip().splitlines()[0]), 1)
        except Exception:
            d = None
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO meta VALUES(?,?,?)", (rel, mt, d))
    return d


# ---------------- Papierkorb (NA01) ----------------
def _media_root() -> Path:
    return (WORKSPACE / "media").resolve()


def trash_item(rel: str) -> dict:
    fp = resolve(rel)
    if not fp or TRASH.resolve() in fp.parents:
        return {"success": False, "error": "NOT_FOUND"}
    TRASH.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = TRASH / f"{stamp}__{fp.name}"
    shutil.move(str(fp), str(dst))
    (dst.parent / (dst.name + ".origin")).write_text(rel, encoding="utf-8")
    th = DIRS["thumbs"] / (re.sub(r"[^\w.-]", "_", rel) + ".jpg")
    if th.is_file():
        th.unlink()
    _emit("library", f"In Papierkorb verschoben: {rel}", ok=True)
    return {"success": True, "trash_id": dst.name, "origin": rel}


def trash_list() -> dict:
    TRASH.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(TRASH.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.suffix == ".origin" or not p.is_file():
            continue
        o = p.with_name(p.name + ".origin")
        items.append({"trash_id": p.name, "origin": o.read_text(encoding="utf-8") if o.is_file() else None,
                      "size_mb": round(p.stat().st_size / 1e6, 1), "kind": _kind(p)})
    return {"success": True, "count": len(items), "items": items, "dir": str(TRASH)}


def _trash_path(tid: str) -> Path | None:
    if not tid or "/" in tid or "\\" in tid or ".." in tid:
        return None
    p = TRASH / tid
    return p if p.is_file() else None


def trash_restore(tid: str) -> dict:
    p = _trash_path(tid)
    if not p:
        return {"success": False, "error": "NOT_FOUND"}
    o = p.with_name(p.name + ".origin")
    rel = o.read_text(encoding="utf-8") if o.is_file() else ("nexusall/Videos/" + p.name.split("__", 1)[-1])
    dst = (_media_root() / rel).resolve()
    if _media_root() not in dst.parents:
        return {"success": False, "error": "BAD_ORIGIN"}
    if dst.exists():
        dst = dst.with_name(dst.stem + "_wiederhergestellt" + dst.suffix)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(dst))
    if o.is_file():
        o.unlink()
    return {"success": True, "restored": dst.relative_to(_media_root()).as_posix()}


def trash_purge(tid: str) -> dict:
    """Endgueltig loeschen - nur aus dem Papierkorb, nur nach zweiter Bestaetigung in der UI."""
    p = _trash_path(tid)
    if not p:
        return {"success": False, "error": "NOT_FOUND"}
    p.unlink()
    o = p.with_name(p.name + ".origin")
    if o.is_file():
        o.unlink()
    return {"success": True, "purged": tid}


def library_stats() -> dict:
    """NA07 (manuell nachgebaut, Loop-Version zaehlte falschen Ordner): Anzahl/MB/Dauer je Tab."""
    out = {"filme": {"count": 0, "size_mb": 0.0, "duration_s": 0.0}, "musik": {"count": 0, "size_mb": 0.0, "duration_s": 0.0}}
    for it in library().get("items", []):
        s = out[it.get("tab") or "filme"]
        s["count"] += 1
        s["size_mb"] = round(s["size_mb"] + float(it.get("size_mb") or 0), 1)
        s["duration_s"] = round(s["duration_s"] + float(it.get("duration") or 0), 1)
    out["trash"] = trash_list().get("count", 0)
    return {"success": True, **out}


def remove_job(jid: str) -> dict:
    j = job(jid)
    if not j:
        return {"success": False, "error": "NOT_FOUND"}
    if j["status"] in ("queued", "running"):
        return {"success": False, "error": "JOB_ACTIVE"}
    with _db() as c:
        c.execute("DELETE FROM jobs WHERE id=?", (jid,))
    return {"success": True, "removed": jid}


def resolve(rel: str) -> Path | None:
    root = (WORKSPACE / "media").resolve()
    fp = (root / rel).resolve()
    if not str(fp).startswith(str(root)) or not fp.is_file():
        return None
    return fp


def thumb(rel: str) -> Path | None:
    fp = resolve(rel)
    if not fp:
        return None
    out = DIRS["thumbs"] / (re.sub(r"[^\w.-]", "_", rel) + ".jpg")
    if not out.is_file():
        ff = ffmpeg_path()
        if not ff:
            return None
        try:
            _run([ff, "-y", "-ss", "3", "-i", str(fp), "-frames:v", "1", "-vf", "scale=320:-1", str(out)], 60)
        except Exception:
            return None
    return out if out.is_file() else None


def save_resume(rel: str, pos: float) -> dict:
    if not resolve(rel):
        return {"success": False, "error": "NOT_FOUND"}
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO resume VALUES(?,?,?)", (rel, float(pos), time.time()))
    return {"success": True, "id": rel, "pos": float(pos)}


# ---------------- Downloader ----------------
def _valid_url(url: str) -> bool:
    return bool(re.match(r"^https?://[^\s]+$", url or ""))


_MEDIA_EXT = {"mp4", "webm", "mkv", "mov", "m4a", "mp3", "ogg", "ogv", "opus", "flac", "wav", "avi", "m4v", "aac", "ts", "flv", "3gp"}


def media_check(url: str) -> dict:
    """yt-dlp --simulate: nur echte Medien-URLs zulassen (keine Such-/Fehlerseiten)."""
    if not YTDLP.is_file():
        return {"ok": False, "detail": "yt-dlp fehlt"}
    try:
        r = _run([str(YTDLP), "--simulate", "--no-playlist", "--no-warnings", "--playlist-items", "1",
                  "--print", "NXCHK %(extractor)s|%(ext)s", url], 60)
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"[:300]}
    lines = [l[6:] for l in (r.stdout or "").splitlines() if l.startswith("NXCHK ")]
    if r.returncode != 0 or not lines:
        return {"ok": False, "detail": (r.stderr or r.stdout or "").strip()[-300:]}
    extractor, _, ext = lines[0].partition("|")
    if ext.lower() not in _MEDIA_EXT:
        return {"ok": False, "detail": f"{extractor}: {ext}"}
    return {"ok": True, "extractor": extractor, "ext": ext}


def enqueue(url: str, quality: str = "best", subs: str = "", playlist: bool = False, loudnorm: bool = False) -> dict:
    url = (url or "").strip()
    if not _valid_url(url):
        return {"success": False, "error": "INVALID_URL"}
    if quality not in QUALITY:
        return {"success": False, "error": "INVALID_QUALITY", "allowed": list(QUALITY)}
    chk = media_check(url)
    if not chk.get("ok"):
        _emit("download", f"Keine Mediendatei auf dieser Seite: {url}", ok=False)
        return {"success": False, "error": "NO_MEDIA", "message": "Keine Mediendatei auf dieser Seite", "detail": chk.get("detail", "")}
    jid = uuid.uuid4().hex[:12]
    with _db() as c:
        c.execute("INSERT INTO jobs(id,url,quality,subs,status,progress,file,error,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (jid, url, quality, subs or "", "queued", 0.0, None, None, time.time(), time.time()))
        c.execute("UPDATE jobs SET loudnorm=? WHERE id=?", (1 if loudnorm else 0, jid))
    _emit("download", f"Download eingereiht: {url} ({quality})", data={"job": jid})
    start_worker()
    return {"success": True, "job_id": jid, "status": "queued"}


def queue() -> dict:
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 100")]
    return {"success": True, "jobs": rows}


def job(jid: str) -> dict | None:
    with _db() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    return dict(r) if r else None


def _set(jid: str, **kw) -> None:
    kw["updated"] = time.time()
    with _db() as c:
        c.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in kw) + " WHERE id=?", (*kw.values(), jid))


def cancel(jid: str) -> dict:
    j = job(jid)
    if not j:
        return {"success": False, "error": "NOT_FOUND"}
    if j["status"] in ("queued", "running"):
        _set(jid, status="cancelled")
        return {"success": True, "status": "cancelled"}
    return {"success": False, "error": "NOT_CANCELLABLE", "status": j["status"]}


def retry(jid: str) -> dict:
    j = job(jid)
    if not j:
        return {"success": False, "error": "NOT_FOUND"}
    _set(jid, status="queued", progress=0.0, error=None, speed=None, eta=None)
    start_worker()
    return {"success": True, "status": "queued"}


def _build_cmd(j: dict) -> list[str]:
    ff = ffmpeg_path()
    dest = DIRS["audio"] if str(j["quality"]).startswith("audio") else DIRS["video"]
    cmd = [str(YTDLP), "--newline", "--progress", "--no-color", "--restrict-filenames", "--no-playlist",
           "-P", str(dest), "-P", "temp:" + str(TMP), "-o", "%(title).100B [%(id)s].%(ext)s",
           "--progress-template", "download:NXP2|%(progress.status)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s",
           "--print", "after_move:NXFILE %(filepath)s", "--no-mtime", *QUALITY[j["quality"]]]
    if ff:
        cmd += ["--ffmpeg-location", str(Path(ff).parent)]
    if j.get("loudnorm") and str(j["quality"]).startswith("audio"):  # NA06
        cmd += ["--postprocessor-args", "ExtractAudio:-af " + LOUDNORM]
    if j.get("subs"):
        cmd += ["--write-subs", "--write-auto-subs", "--sub-langs", j["subs"], "--convert-subs", "srt"]
    return cmd + ["--", j["url"]]


def _process(j: dict) -> None:
    jid = j["id"]
    if shutil.disk_usage(str(LIB)).free / 1e9 < MIN_FREE_GB:
        _set(jid, status="failed", error="DISK_LOW")
        return
    if not YTDLP.is_file():
        _set(jid, status="failed", error="YTDLP_MISSING")
        return
    _set(jid, status="running", progress=0.0)
    _emit("download", f"Download läuft: {j['url']}", data={"job": jid})
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    p = subprocess.Popen(_build_cmd(j), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", env=_env(), creationflags=flags)
    files, tail, last = [], [], 0.0  # last = Zeitpunkt letztes Update
    for line in p.stdout:  # type: ignore[union-attr]
        line = line.strip()
        tail = (tail + [line])[-15:]
        pr = parse_progress(line)
        if pr:
            now = time.time()
            if now - last >= 0.7 or (pr.get("progress") or 0) >= 100:  # NA02: live, max ~1.4 Updates/s
                last = now
                _set(jid, **{k: v for k, v in pr.items() if v is not None})
        if line.startswith("NXFILE "):
            files.append(line[7:].strip())
        cur = job(jid)
        if cur and cur["status"] == "cancelled":
            _kill_tree(p)
            _emit("download", f"Download abgebrochen: {jid}", ok=False)
            return
    rc = p.wait()
    real = [f for f in files if Path(f).is_file()]
    if rc == 0 and real:
        rel = Path(real[-1]).resolve().relative_to((WORKSPACE / "media").resolve()).as_posix()
        _set(jid, status="done", progress=100.0, file=rel)
        _emit("download", f"Download fertig: {rel}", ok=True, data={"job": jid, "file": rel})
    else:
        err = next((t for t in reversed(tail) if "ERROR" in t), None) or f"rc={rc}, keine Datei"
        _set(jid, status="failed", error=err[:500])
        _emit("download", f"Download fehlgeschlagen: {err[:200]}", ok=False, data={"job": jid})


def _loop() -> None:
    global _worker
    while True:
        with _db() as c:
            r = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
        if not r:
            break
        try:
            _process(dict(r))
        except Exception as e:  # pragma: no cover
            _set(r["id"], status="failed", error=f"{type(e).__name__}: {e}"[:500])
    with _lock:
        _worker = None


def start_worker() -> None:
    global _worker
    with _lock:
        if _worker and _worker.is_alive():
            return
        # verwaiste running-Jobs nach Neustart wieder einreihen
        with _db() as c:
            c.execute("UPDATE jobs SET status='queued' WHERE status='running'")
        _worker = threading.Thread(target=_loop, name="nexusall-dl", daemon=True)
        _worker.start()


def probe(url: str) -> dict:
    if not _valid_url(url):
        return {"success": False, "error": "INVALID_URL"}
    try:
        r = _run([str(YTDLP), "-J", "--no-playlist", "--", url], 90)
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "TIMEOUT"}
    if r.returncode != 0:
        return {"success": False, "error": (r.stderr or "").strip()[-400:] or "PROBE_FAILED"}
    d = json.loads(r.stdout)
    fm = [{"id": f.get("format_id"), "ext": f.get("ext"), "height": f.get("height"), "note": f.get("format_note")}
          for f in d.get("formats") or []]
    return {"success": True, "title": d.get("title"), "duration": d.get("duration"),
            "extractor": d.get("extractor"), "formats": fm[-30:]}


def open_app_window(url: str) -> dict:
    """Seiten, die iframe blocken: Edge-App-Fenster (eigenes Profil auf G:)."""
    if not _valid_url(url):
        return {"success": False, "error": "INVALID_URL"}
    cands = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
             Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe"]
    exe = next((c for c in cands if c.is_file()), None)
    if not exe:
        return {"success": False, "error": "EDGE_MISSING"}
    prof = STATE / "edge_profile"
    prof.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen([str(exe), f"--app={url}", f"--user-data-dir={prof}", "--no-first-run"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"success": True, "pid": p.pid, "url": url, "mode": "edge_app_window"}


# ---------------- Sprachbefehle ----------------
def voice_intent(text: str) -> dict | None:
    t = (text or "").lower()
    url = re.search(r"https?://\S+", text or "")
    if re.search(r"(lade|download).*(nur.*ton|audio|musik)", t):
        return {"action": "download", "quality": "audio", "url": url.group(0) if url else None}
    if re.search(r"\b(lade|download)\b.*(video|runter|herunter)", t):
        return {"action": "download", "quality": "best", "url": url.group(0) if url else None}
    m = re.search(r"\bspiel(e)?\s+(.+)", t)
    if m and "nexus all" not in m.group(2):
        return {"action": "play", "query": m.group(2).strip()}
    if "meine downloads" in t or "nexus all" in t:
        return {"action": "open"}
    return None
