"""System Performance Monitor — echte Live-Daten, keine Fake-Zahlen."""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

from nexus.common.config import Config
from nexus.common.paths import LOGS, ensure_dirs, disk_root, IS_WINDOWS

HISTORY_FILE = LOGS / "monitor_history.jsonl"
HISTORY_MAX = 200

# Netzwerk-Rate: vorherige Counter + Zeitstempel
_net_prev: dict[str, Any] = {"ts": None, "sent": None, "recv": None}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _pct(used: float | None, total: float | None) -> float | None:
    if used is None or total is None or total <= 0:
        return None
    return round(100.0 * float(used) / float(total), 1)


def _gb(n: int | float | None) -> float | None:
    if n is None:
        return None
    return round(float(n) / (1024**3), 2)


# ── CPU ──────────────────────────────────────────────────────────────


def _cpu() -> dict[str, Any]:
    out: dict[str, Any] = {
        "percent": None,
        "count": None,
        "per_cpu": None,
        "load": None,
        "source": None,
        "note": None,
    }
    if psutil is not None:
        try:
            p = float(psutil.cpu_percent(interval=0.12))
            out["percent"] = round(p, 1)
            out["count"] = int(psutil.cpu_count() or 0) or None
            try:
                out["per_cpu"] = [round(float(x), 1) for x in psutil.cpu_percent(interval=0.05, percpu=True)]
            except Exception:
                out["per_cpu"] = None
            try:
                load = os.getloadavg()
                out["load"] = {"1": round(load[0], 2), "5": round(load[1], 2), "15": round(load[2], 2)}
            except (AttributeError, OSError):
                out["load"] = None
            out["source"] = "psutil"
            return out
        except Exception as e:
            out["note"] = f"psutil CPU: {e}"

    if os.name == "nt":
        ps = (
            "(Get-CimInstance Win32_Processor | "
            "Measure-Object -Property LoadPercentage -Average).Average"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=8,
            )
            if r.returncode == 0 and r.stdout.strip():
                out["percent"] = round(float(r.stdout.strip().replace(",", ".")), 1)
                out["count"] = os.cpu_count()
                out["source"] = "cim"
                return out
            out["note"] = (r.stderr or "cim_fail")[:200]
            out["source"] = "cim"
        except Exception as e:
            out["note"] = str(e)
            out["source"] = "cim"
        return out

    # Linux /proc/stat
    def _read_stat() -> tuple[int, int] | None:
        try:
            with open("/proc/stat", "r", encoding="utf-8") as f:
                line = f.readline()
            if not line.startswith("cpu "):
                return None
            parts = [int(x) for x in line.split()[1:]]
            if len(parts) < 4:
                return None
            idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
            return idle, sum(parts)
        except Exception:
            return None

    a = _read_stat()
    if a:
        time.sleep(0.12)
        b = _read_stat()
        if b and (b[1] - a[1]) > 0:
            pct = round(100.0 * (1.0 - ((b[0] - a[0]) / (b[1] - a[1]))), 1)
            out["percent"] = max(0.0, min(100.0, pct))
            out["count"] = os.cpu_count()
            out["source"] = "proc"
            try:
                load = os.getloadavg()
                out["load"] = {"1": round(load[0], 2), "5": round(load[1], 2), "15": round(load[2], 2)}
            except Exception:
                pass
            return out

    try:
        load1, load5, load15 = os.getloadavg()
        n = os.cpu_count() or 1
        out["percent"] = round(min(100.0, 100.0 * float(load1) / float(n)), 1)
        out["count"] = n
        out["load"] = {"1": round(load1, 2), "5": round(load5, 2), "15": round(load15, 2)}
        out["source"] = "loadavg"
        out["note"] = "Approx. aus loadavg (kein präzises Sample)"
    except Exception as e:
        out["note"] = str(e)
        out["source"] = "none"
    return out


# ── RAM ──────────────────────────────────────────────────────────────


def _ram() -> dict[str, Any]:
    out: dict[str, Any] = {
        "used": None, "total": None, "percent": None, "available": None,
        "used_gb": None, "total_gb": None, "available_gb": None,
        "source": None, "note": None,
    }
    if psutil is not None:
        try:
            m = psutil.virtual_memory()
            out.update({
                "used": int(m.used),
                "total": int(m.total),
                "percent": round(float(m.percent), 1),
                "available": int(m.available),
                "used_gb": _gb(m.used),
                "total_gb": _gb(m.total),
                "available_gb": _gb(m.available),
                "source": "psutil",
            })
            return out
        except Exception as e:
            out["note"] = f"psutil RAM: {e}"

    if os.name == "nt":
        ps = (
            "$o=Get-CimInstance Win32_OperatingSystem; "
            "$total=[int64]$o.TotalVisibleMemorySize*1024; "
            "$free=[int64]$o.FreePhysicalMemory*1024; "
            "$used=$total-$free; "
            "Write-Output \"$used|$total|$free\""
        )
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=8,
            )
            if r.returncode == 0 and "|" in r.stdout:
                parts = r.stdout.strip().split("|")
                used, total, free = int(parts[0]), int(parts[1]), int(parts[2])
                out.update({
                    "used": used, "total": total, "available": free,
                    "percent": _pct(used, total),
                    "used_gb": _gb(used), "total_gb": _gb(total), "available_gb": _gb(free),
                    "source": "cim",
                })
                return out
            out["note"] = (r.stderr or "cim_fail")[:200]
            out["source"] = "cim"
        except Exception as e:
            out["note"] = str(e)
            out["source"] = "cim"
        return out

    # Linux /proc/meminfo
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                parts = v.strip().split()
                if parts:
                    info[k] = int(parts[0]) * 1024
    except Exception as e:
        out["note"] = str(e)
        out["source"] = "proc"
        return out
    total = info.get("MemTotal") or 0
    available = info.get("MemAvailable")
    if available is None:
        available = (info.get("MemFree") or 0) + (info.get("Buffers") or 0) + (info.get("Cached") or 0)
    used = max(0, total - available)
    out.update({
        "used": int(used), "total": int(total), "available": int(available),
        "percent": _pct(used, total),
        "used_gb": _gb(used), "total_gb": _gb(total), "available_gb": _gb(available),
        "source": "proc",
    })
    return out


# ── Disks ────────────────────────────────────────────────────────────


def _disks() -> list[dict[str, Any]]:
    disks: list[dict[str, Any]] = []
    if psutil is not None:
        try:
            parts = psutil.disk_partitions(all=False)
            seen: set[str] = set()
            for p in parts:
                mp = p.mountpoint
                if not mp or mp in seen:
                    continue
                # Skip special FS
                fstype = (p.fstype or "").lower()
                if fstype in ("tmpfs", "devtmpfs", "squashfs", "overlay", "proc", "sysfs", "cgroup", "cgroup2"):
                    continue
                if os.name != "nt" and not mp.startswith("/"):
                    continue
                try:
                    u = psutil.disk_usage(mp)
                except Exception:
                    continue
                seen.add(mp)
                disks.append({
                    "mount": mp,
                    "device": getattr(p, "device", None),
                    "fstype": p.fstype or None,
                    "used": int(u.used),
                    "total": int(u.total),
                    "free": int(u.free),
                    "percent": round(float(u.percent), 1),
                    "used_gb": _gb(u.used),
                    "total_gb": _gb(u.total),
                    "free_gb": _gb(u.free),
                    "source": "psutil",
                })
            if disks:
                return disks
        except Exception:
            pass

    # Fallback: root / C:
    path = disk_root()
    try:
        u = shutil.disk_usage(path)
        disks.append({
            "mount": path,
            "device": None,
            "fstype": None,
            "used": int(u.used),
            "total": int(u.total),
            "free": int(u.free),
            "percent": _pct(u.used, u.total),
            "used_gb": _gb(u.used),
            "total_gb": _gb(u.total),
            "free_gb": _gb(u.free),
            "source": "shutil",
        })
    except Exception as e:
        disks.append({
            "mount": path, "used": None, "total": None, "free": None,
            "percent": None, "note": str(e), "source": "none",
        })
    return disks


def _disk_primary(disks: list[dict[str, Any]]) -> dict[str, Any]:
    """Kompatibel zu altem metrics()['disk'] — primäre Partition."""
    if not disks:
        return {"path": None, "used": None, "total": None, "percent": None, "source": "none"}
    prefer = disk_root()
    chosen = disks[0]
    for d in disks:
        if d.get("mount") == prefer:
            chosen = d
            break
    return {
        "path": chosen.get("mount"),
        "used": chosen.get("used"),
        "total": chosen.get("total"),
        "percent": chosen.get("percent"),
        "used_gb": chosen.get("used_gb"),
        "total_gb": chosen.get("total_gb"),
        "free_gb": chosen.get("free_gb"),
        "free": chosen.get("free"),
        "source": chosen.get("source"),
        "note": chosen.get("note"),
    }


# ── GPU ──────────────────────────────────────────────────────────────


def _gpu_nvidia() -> dict[str, Any] | None:
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=6,
        )
    except FileNotFoundError:
        return None
    except Exception as e:
        return {"available": False, "note": f"nvidia-smi Fehler: {e}", "gpus": [], "source": "nvidia-smi"}
    if r.returncode != 0:
        return None
    gpus = []
    for line in (r.stdout or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            mem_used = int(float(parts[2])) * 1024 * 1024
            mem_total = int(float(parts[3])) * 1024 * 1024
            util = float(parts[1])
            temp = float(parts[4]) if parts[4] not in ("", "[N/A]", "N/A") else None
        except Exception:
            continue
        gpus.append({
            "name": parts[0],
            "percent": round(util, 1),
            "mem_used": mem_used,
            "mem_total": mem_total,
            "mem_percent": _pct(mem_used, mem_total),
            "mem_used_gb": _gb(mem_used),
            "mem_total_gb": _gb(mem_total),
            "temp_c": temp,
        })
    if not gpus:
        return None
    return {"available": True, "source": "nvidia-smi", "gpus": gpus, "note": None}


def _gpu_windows_cim() -> dict[str, Any] | None:
    if os.name != "nt":
        return None
    ps = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object -First 3 Name,AdapterRAM | "
        "ForEach-Object { \"$($_.Name)|$($_.AdapterRAM)\" }"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:
        return None
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None
    gpus = []
    for line in r.stdout.strip().splitlines():
        if "|" not in line:
            continue
        name, ram_s = line.split("|", 1)
        try:
            ram = int(ram_s) if ram_s and ram_s.lower() not in ("", "none") else None
        except Exception:
            ram = None
        is_amd = "amd" in name.lower() or "radeon" in name.lower()
        note = "Auslastung via CIM nicht verfügbar"
        if is_amd:
            note = "AMD: Auslastung ohne nvidia-smi / ADL nicht verfügbar"
        gpus.append({
            "name": name.strip(),
            "percent": None,
            "mem_used": None,
            "mem_total": ram,
            "mem_total_gb": _gb(ram) if ram and ram > 0 else None,
            "mem_percent": None,
            "note": note,
        })
    if not gpus:
        return None
    return {
        "available": True,
        "source": "cim",
        "gpus": gpus,
        "note": "Nur GPU-Namen/VRAM (keine Live-Auslastung ohne nvidia-smi)",
    }


def _gpu() -> dict[str, Any]:
    nv = _gpu_nvidia()
    if nv and nv.get("available"):
        return nv
    wmi = _gpu_windows_cim()
    if wmi and wmi.get("available"):
        return wmi
    note = "Keine GPU-Metriken (nvidia-smi nicht gefunden"
    if os.name == "nt":
        note += "; CIM ohne Live-Auslastung"
    note += "). AMD: Util oft nicht verfügbar."
    return {"available": False, "gpus": [], "note": note, "source": "none", "percent": None}


# ── Network ──────────────────────────────────────────────────────────


def _network() -> dict[str, Any]:
    global _net_prev
    out: dict[str, Any] = {
        "bytes_sent": None,
        "bytes_recv": None,
        "bytes_sent_rate": None,
        "bytes_recv_rate": None,
        "interfaces": [],
        "source": None,
        "note": None,
    }
    if psutil is not None:
        try:
            io = psutil.net_io_counters()
            sent, recv = int(io.bytes_sent), int(io.bytes_recv)
            now = time.time()
            out["bytes_sent"] = sent
            out["bytes_recv"] = recv
            prev_ts = _net_prev.get("ts")
            if prev_ts is not None and _net_prev.get("sent") is not None:
                dt = max(0.001, now - float(prev_ts))
                out["bytes_sent_rate"] = round((sent - int(_net_prev["sent"])) / dt, 1)
                out["bytes_recv_rate"] = round((recv - int(_net_prev["recv"])) / dt, 1)
            _net_prev = {"ts": now, "sent": sent, "recv": recv}
            ifaces = []
            try:
                addrs = psutil.net_if_addrs()
                stats = psutil.net_if_stats()
                for name, alist in addrs.items():
                    st = stats.get(name)
                    ipv4 = None
                    for a in alist:
                        if getattr(a, "family", None) == socket.AF_INET:
                            ipv4 = a.address
                            break
                    ifaces.append({
                        "name": name,
                        "up": bool(st.isup) if st else None,
                        "speed_mbps": getattr(st, "speed", None) if st else None,
                        "ipv4": ipv4,
                    })
            except Exception:
                pass
            out["interfaces"] = ifaces
            out["source"] = "psutil"
            return out
        except Exception as e:
            out["note"] = str(e)

    # Fallback: /proc/net/dev counters (kein Rate ohne Prev)
    try:
        with open("/proc/net/dev", "r", encoding="utf-8") as f:
            lines = f.readlines()[2:]
        total_recv = total_sent = 0
        ifaces = []
        for line in lines:
            if ":" not in line:
                continue
            name, rest = line.split(":", 1)
            name = name.strip()
            cols = rest.split()
            if len(cols) < 9:
                continue
            rcv, snt = int(cols[0]), int(cols[8])
            total_recv += rcv
            total_sent += snt
            ifaces.append({"name": name, "bytes_recv": rcv, "bytes_sent": snt})
        now = time.time()
        out["bytes_sent"] = total_sent
        out["bytes_recv"] = total_recv
        out["interfaces"] = ifaces[:12]
        prev_ts = _net_prev.get("ts")
        if prev_ts is not None and _net_prev.get("sent") is not None:
            dt = max(0.001, now - float(prev_ts))
            out["bytes_sent_rate"] = round((total_sent - int(_net_prev["sent"])) / dt, 1)
            out["bytes_recv_rate"] = round((total_recv - int(_net_prev["recv"])) / dt, 1)
        _net_prev = {"ts": now, "sent": total_sent, "recv": total_recv}
        out["source"] = "proc"
    except Exception as e:
        out["note"] = out.get("note") or str(e)
        out["source"] = "none"
    return out


# ── Processes ────────────────────────────────────────────────────────


def _processes() -> dict[str, Any]:
    top_cpu: list[dict[str, Any]] = []
    top_ram: list[dict[str, Any]] = []
    if psutil is None:
        return {
            "top_cpu": [],
            "top_ram": [],
            "source": "none",
            "note": "psutil nicht installiert — Prozessliste nicht verfügbar",
        }
    try:
        procs: list[dict[str, Any]] = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "memory_info"]):
            try:
                info = p.info
                mem = info.get("memory_info")
                rss = int(mem.rss) if mem is not None else None
                procs.append({
                    "name": info.get("name") or "?",
                    "pid": info.get("pid"),
                    "cpu_percent": round(float(info.get("cpu_percent") or 0.0), 1),
                    "memory_percent": round(float(info.get("memory_percent") or 0.0), 1),
                    "rss": rss,
                    "rss_mb": round(rss / (1024**2), 1) if rss is not None else None,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        # cpu_percent oft 0 beim ersten Scan — kurzer zweiter Pass
        if procs and all(p["cpu_percent"] == 0.0 for p in procs[:20]):
            time.sleep(0.08)
            procs = []
            for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "memory_info"]):
                try:
                    info = p.info
                    mem = info.get("memory_info")
                    rss = int(mem.rss) if mem is not None else None
                    procs.append({
                        "name": info.get("name") or "?",
                        "pid": info.get("pid"),
                        "cpu_percent": round(float(info.get("cpu_percent") or 0.0), 1),
                        "memory_percent": round(float(info.get("memory_percent") or 0.0), 1),
                        "rss": rss,
                        "rss_mb": round(rss / (1024**2), 1) if rss is not None else None,
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        by_cpu = sorted(procs, key=lambda x: x["cpu_percent"], reverse=True)[:8]
        by_ram = sorted(procs, key=lambda x: x["memory_percent"], reverse=True)[:8]
        return {"top_cpu": by_cpu, "top_ram": by_ram, "source": "psutil", "note": None}
    except Exception as e:
        return {"top_cpu": [], "top_ram": [], "source": "psutil", "note": str(e)}


# ── Ollama ───────────────────────────────────────────────────────────


def _ollama() -> dict[str, Any]:
    try:
        from nexus.common.ollama_client import OllamaClient
        client = OllamaClient()
        ping = client.ping()
        preferred = None
        if ping.get("reachable"):
            # 2026-10-28: echtes Chat-Modell aus AI-Layer (ai_models.json), nicht Env-Default
            try:
                from nexus.common import ai_layer
                preferred = ai_layer.resolve_chat_model(client)
            except Exception:
                preferred = client.pick_model()
        return {
            "reachable": bool(ping.get("reachable")),
            "ok": bool(ping.get("ok")),
            "models": ping.get("models") or [],
            "preferred": preferred,
            "label": ("Hermes (" + preferred + ")") if preferred else None,
            "error": ping.get("error"),
            "url": client.base_url,
        }
    except Exception as e:
        return {
            "reachable": False, "ok": False, "models": [], "preferred": None,
            "error": str(e), "url": getattr(Config, "OLLAMA_URL", None),
        }


# ── NEXUS self ───────────────────────────────────────────────────────


def _nexus_self() -> dict[str, Any]:
    port = Config.PORT
    out: dict[str, Any] = {
        "port": port,
        "listening": None,
        "pid": os.getpid(),
        "rss": None,
        "rss_mb": None,
        "uptime_s": None,
        "note": None,
    }
    # Port check
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            out["listening"] = True
    except OSError:
        out["listening"] = False
        out["note"] = f"Port {port} lokal nicht erreichbar (Server evtl. nicht gestartet)"

    if psutil is not None:
        try:
            proc = psutil.Process(os.getpid())
            mi = proc.memory_info()
            out["rss"] = int(mi.rss)
            out["rss_mb"] = round(mi.rss / (1024**2), 1)
            out["uptime_s"] = round(time.time() - proc.create_time(), 1)
        except Exception as e:
            out["note"] = (out.get("note") or "") + f" Process: {e}"
    return out


# ── System-Uptime ────────────────────────────────────────────────────


def _uptime_human(up: float) -> str:
    d, rem = divmod(int(up), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d > 0:
        return f"{d}d {h}h {m}m"
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


def _system_uptime() -> dict[str, Any]:
    out: dict[str, Any] = {
        "boot_time": None, "uptime_s": None, "human": None,
        "source": None, "note": None,
    }
    boot: float | None = None
    if psutil is not None:
        try:
            boot = float(psutil.boot_time())
            out["source"] = "psutil"
        except Exception as e:
            out["note"] = f"psutil boot_time: {e}"
    if boot is None and os.name == "nt":
        ps = "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime"
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, timeout=8)
            if r.returncode == 0 and (r.stdout or "").strip():
                boot = datetime.fromisoformat(r.stdout.strip()).timestamp()
                out["source"] = "cim"
        except Exception as e:
            out["note"] = (out.get("note") or "") + f" cim: {e}"
    if boot is None:
        return out
    try:
        up = max(0.0, time.time() - boot)
        out["boot_time"] = datetime.fromtimestamp(boot).astimezone().isoformat(timespec="seconds")
        out["uptime_s"] = round(up)
        out["human"] = _uptime_human(up)
    except Exception as e:
        out["note"] = (out.get("note") or "") + f" fmt: {e}"
    return out


# ── Alerts ───────────────────────────────────────────────────────────


def _alerts(data: dict[str, Any]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    cpu_p = (data.get("cpu") or {}).get("percent")
    if cpu_p is not None and cpu_p > 90:
        alerts.append({"level": "crit", "code": "cpu_high", "message_de": f"CPU-Auslastung kritisch: {cpu_p}%"})
    elif cpu_p is not None and cpu_p > 80:
        alerts.append({"level": "warn", "code": "cpu_warn", "message_de": f"CPU-Auslastung hoch: {cpu_p}%"})

    ram_p = (data.get("ram") or {}).get("percent")
    if ram_p is not None and ram_p > 90:
        alerts.append({"level": "crit", "code": "ram_high", "message_de": f"RAM-Auslastung kritisch: {ram_p}%"})
    elif ram_p is not None and ram_p > 80:
        alerts.append({"level": "warn", "code": "ram_warn", "message_de": f"RAM-Auslastung hoch: {ram_p}%"})

    for d in data.get("disks") or []:
        dp = d.get("percent")
        mount = d.get("mount") or "?"
        if dp is not None and dp > 95:
            alerts.append({"level": "crit", "code": "disk_full", "message_de": f"Festplatte {mount} fast voll: {dp}%"})
        elif dp is not None and dp > 90:
            alerts.append({"level": "warn", "code": "disk_warn", "message_de": f"Festplatte {mount} knapp: {dp}%"})

    oll = data.get("ollama") or {}
    if not oll.get("reachable"):
        alerts.append({
            "level": "warn",
            "code": "ollama_down",
            "message_de": f"Ollama nicht erreichbar ({oll.get('url') or 'unbekannt'})",
        })

    nx = data.get("nexus") or {}
    if nx.get("listening") is False:
        alerts.append({
            "level": "info",
            "code": "nexus_port",
            "message_de": f"NEXUS-Port {nx.get('port')} lokal nicht erreichbar",
        })
    return alerts


# ── History ──────────────────────────────────────────────────────────


def _history_append(sample: dict[str, Any]) -> None:
    try:
        ensure_dirs()
        slim = {
            "timestamp": sample.get("timestamp"),
            "cpu": (sample.get("cpu") or {}).get("percent"),
            "ram": (sample.get("ram") or {}).get("percent"),
            "disk": (sample.get("disk") or {}).get("percent"),
            "gpu": None,
            "net_sent_rate": (sample.get("network") or {}).get("bytes_sent_rate"),
            "net_recv_rate": (sample.get("network") or {}).get("bytes_recv_rate"),
        }
        gpus = (sample.get("gpu") or {}).get("gpus") or []
        if gpus and gpus[0].get("percent") is not None:
            slim["gpu"] = gpus[0]["percent"]
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(slim, ensure_ascii=False) + "\n")
        # Truncate to last HISTORY_MAX lines
        try:
            lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
            if len(lines) > HISTORY_MAX:
                HISTORY_FILE.write_text("\n".join(lines[-HISTORY_MAX:]) + "\n", encoding="utf-8")
        except Exception:
            pass
    except Exception:
        pass


def history_read(n: int = 60) -> list[dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []
    try:
        lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines[-max(1, n):]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception:
        return []


# ── SystemMonitor ────────────────────────────────────────────────────


class SystemMonitor:
    """Hauptsammler für Systemleistung — echte Werte, null wenn unbekannt."""

    def collect(self, *, write_history: bool = True) -> dict[str, Any]:
        cpu = _cpu()
        ram = _ram()
        disks = _disks()
        disk = _disk_primary(disks)
        gpu = _gpu()
        # Convenience: top-level gpu percent for rings
        gpu_pct = None
        if gpu.get("gpus"):
            gpu_pct = gpu["gpus"][0].get("percent")
        gpu = dict(gpu)
        gpu["percent"] = gpu_pct

        network = _network()
        processes = _processes()
        ollama = _ollama()
        nexus = _nexus_self()
        plat = {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "node": platform.node(),
        }
        data: dict[str, Any] = {
            "success": True,
            "timestamp": _now_iso(),
            "cpu": cpu,
            "ram": ram,
            "disk": disk,
            "disks": disks,
            "gpu": gpu,
            "network": network,
"processes": processes,
        "ollama": ollama,
        "nexus": nexus,
        "uptime": _system_uptime(),
        "platform": plat,
            "psutil": psutil is not None,
            "alerts": [],
        }
        data["alerts"] = _alerts(data)
        if write_history:
            _history_append(data)
        return data

    def snapshot(self) -> dict[str, Any]:
        return self.collect()

    def summary_de(self, data: dict[str, Any] | None = None) -> str:
        m = data or self.collect(write_history=False)
        parts: list[str] = []
        cpu_p = (m.get("cpu") or {}).get("percent")
        if cpu_p is not None:
            parts.append(f"CPU {cpu_p}%")
        ram = m.get("ram") or {}
        if ram.get("percent") is not None:
            avail = ram.get("available_gb")
            extra = f", frei {avail} GB" if avail is not None else ""
            parts.append(f"RAM {ram.get('used_gb')}/{ram.get('total_gb')} GB ({ram.get('percent')}%{extra})")
        disk = m.get("disk") or {}
        if disk.get("percent") is not None:
            parts.append(f"Disk {disk.get('used_gb')}/{disk.get('total_gb')} GB ({disk.get('percent')}%)")
        gpu = m.get("gpu") or {}
        if gpu.get("available") and gpu.get("gpus"):
            g0 = gpu["gpus"][0]
            if g0.get("percent") is not None:
                parts.append(f"GPU {g0.get('name', '')} {g0.get('percent')}%")
            else:
                parts.append(f"GPU {g0.get('name', 'ok')} (Auslastung n/a)")
        else:
            parts.append("GPU n/a")
        oll = m.get("ollama") or {}
        if oll.get("reachable"):
            pref = oll.get("preferred") or "?"
            parts.append(f"Ollama an ({pref})")
        else:
            parts.append("Ollama aus")
        alerts = m.get("alerts") or []
        if alerts:
            parts.append(f"{len(alerts)} Alert(s)")
        return " · ".join(parts) if parts else "Keine Metriken verfügbar"

    def answer_query(self, q: str) -> str:
        q_low = (q or "").lower().strip()
        m = self.collect(write_history=False)

        if any(k in q_low for k in ("prozess", "belastet", "top ", "was lastet", "was frisst")):
            procs = m.get("processes") or {}
            lines = ["Top-Prozesse (CPU):"]
            for p in (procs.get("top_cpu") or [])[:8]:
                lines.append(f"  · {p.get('name')} (PID {p.get('pid')}): CPU {p.get('cpu_percent')}%, RAM {p.get('memory_percent')}%")
            lines.append("Top-Prozesse (RAM):")
            for p in (procs.get("top_ram") or [])[:5]:
                lines.append(f"  · {p.get('name')} (PID {p.get('pid')}): RAM {p.get('memory_percent')}% ({p.get('rss_mb')} MB)")
            if procs.get("note"):
                lines.append(f"Hinweis: {procs['note']}")
            return "\n".join(lines)

        if "ollama" in q_low:
            o = m.get("ollama") or {}
            if o.get("reachable"):
                models = ", ".join(o.get("models") or []) or "(keine)"
                return f"Ollama erreichbar unter {o.get('url')}. Modell: {o.get('preferred') or '?'}. Modelle: {models}"
            return f"Ollama nicht erreichbar ({o.get('url')}). Fehler: {o.get('error') or 'unbekannt'}"

        if any(k in q_low for k in ("netzwerk", "network", "bandbreite", "netz ")):
            n = m.get("network") or {}
            sent_r = n.get("bytes_sent_rate")
            recv_r = n.get("bytes_recv_rate")
            def _rate(v):
                if v is None:
                    return "n/a (erstes Sample)"
                if abs(v) >= 1024 * 1024:
                    return f"{v / (1024*1024):.2f} MB/s"
                if abs(v) >= 1024:
                    return f"{v / 1024:.1f} KB/s"
                return f"{v:.0f} B/s"
            ifaces = n.get("interfaces") or []
            up = [i["name"] for i in ifaces if i.get("up")]
            return (
                f"Netzwerk: ↑ {_rate(sent_r)} · ↓ {_rate(recv_r)}. "
                f"Interfaces up: {', '.join(up[:6]) or 'n/a'}."
            )

        if any(k in q_low for k in ("alert", "warnung", "warnungen", "alarm")):
            alerts = m.get("alerts") or []
            if not alerts:
                return "Keine aktuellen Alerts — System im grünen Bereich."
            return "Alerts:\n" + "\n".join(f"  [{a['level']}] {a['message_de']}" for a in alerts)

        if any(k in q_low for k in ("gpu", "grafik", "vram")):
            g = m.get("gpu") or {}
            if g.get("available") and g.get("gpus"):
                lines = []
                for g0 in g["gpus"]:
                    if g0.get("percent") is not None:
                        lines.append(f"GPU {g0.get('name')}: {g0.get('percent')}%")
                    else:
                        lines.append(f"GPU {g0.get('name')}: Auslastung n/a — {g0.get('note') or g.get('note') or ''}")
                    if g0.get("mem_total_gb") or g0.get("mem_total"):
                        mt = g0.get("mem_total_gb") or _gb(g0.get("mem_total"))
                        mu = g0.get("mem_used_gb")
                        if mu is not None:
                            lines.append(f"  VRAM {mu}/{mt} GB")
                        elif mt:
                            lines.append(f"  VRAM total {mt} GB")
                return "\n".join(lines)
            return g.get("note") or "GPU-Metriken nicht verfügbar."

        if any(k in q_low for k in ("festplatte", "disk", "speicherplatz", "laufwerk")):
            lines = ["Festplatten:"]
            for d in m.get("disks") or []:
                if d.get("percent") is not None:
                    lines.append(
                        f"  · {d.get('mount')}: {d.get('used_gb')}/{d.get('total_gb')} GB "
                        f"({d.get('percent')}%, frei {d.get('free_gb')} GB)"
                    )
                else:
                    lines.append(f"  · {d.get('mount')}: n/a — {d.get('note') or ''}")
            return "\n".join(lines)

        if any(k in q_low for k in ("ram", "arbeitsspeicher", "speicher frei", "memory")):
            ram = m.get("ram") or {}
            if ram.get("percent") is None:
                return f"RAM nicht verfügbar. {ram.get('note') or ''}"
            return (
                f"RAM: {ram.get('used_gb')}/{ram.get('total_gb')} GB ({ram.get('percent')}%), "
                f"frei {ram.get('available_gb')} GB."
            )

        if any(k in q_low for k in ("cpu", "prozessor", "auslastung")):
            cpu = m.get("cpu") or {}
            if cpu.get("percent") is None:
                return f"CPU nicht verfügbar. {cpu.get('note') or ''}"
            load = cpu.get("load")
            load_s = ""
            if load:
                load_s = f" Load: {load.get('1')}/{load.get('5')}/{load.get('15')}"
            return f"CPU: {cpu.get('percent')}% · {cpu.get('count') or '?'} Kerne.{load_s}"

        if any(k in q_low for k in ("systemlaufzeit", "laufzeit", "uptime", "bootzeit", "laeuft seit", "seit wann")):
            up = m.get("uptime") or {}
            if up.get("uptime_s") is None:
                return f"Systemlaufzeit nicht verfügbar. {up.get('note') or 'unbekannt'}"
            return f"System läuft seit {up.get('boot_time')} — Uptime {up.get('human')} ({up.get('uptime_s')} s)."

        if any(k in q_low for k in ("nexus", "port")):
            nx = m.get("nexus") or {}
            listen = "ja" if nx.get("listening") else ("nein" if nx.get("listening") is False else "?")
            up = nx.get("uptime_s")
            up_s = f"{int(up)} s" if up is not None else "n/a"
            return (
                f"NEXUS PID {nx.get('pid')}, Port {nx.get('port')} erreichbar: {listen}, "
                f"RSS {nx.get('rss_mb')} MB, Uptime {up_s}."
            )

        # Default: full summary
        return self.summary_de(m)


# Singleton helpers
_monitor: SystemMonitor | None = None


def get_monitor() -> SystemMonitor:
    global _monitor
    if _monitor is None:
        _monitor = SystemMonitor()
    return _monitor


def collect(**kwargs: Any) -> dict[str, Any]:
    return get_monitor().collect(**kwargs)


def summary_de(data: dict[str, Any] | None = None) -> str:
    return get_monitor().summary_de(data)


def answer_query(q: str) -> str:
    return get_monitor().answer_query(q)


__all__ = [
    "SystemMonitor",
    "get_monitor",
    "collect",
    "summary_de",
    "answer_query",
    "history_read",
    "HISTORY_FILE",
]
