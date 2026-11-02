"""008-P03: erweiterter Werkzeugkatalog (READ_ONLY, Sandbox = Bridge-ALLOWED_ROOT).

search_files: Dateinamen-/Inhaltssuche, code_analyze: Python-AST-Analyse,
system_diagnose: lokale Diagnose (CPU/RAM/Platten/Ports/Ollama) ohne Aenderung.
Alle Pfade laufen durch executors._safe_path (Traversal/Secret-Sperre bleibt).
"""
from __future__ import annotations

import ast
import fnmatch
import os
import shutil
import socket
from typing import Any

_SKIP = {".git", "__pycache__", "node_modules", "cache", "backups", "tmp", ".venv", "venv"}
_TEXT = {".py", ".md", ".txt", ".json", ".js", ".html", ".css", ".ps1", ".toml", ".ini", ".yaml", ".yml", ".jsonl"}


def exec_search_files(params: dict[str, Any], rec: Any) -> dict[str, Any]:
    from nexus.bridge.executors import _safe_path, _rel
    from nexus.bridge.errors import BridgeError, INVALID_REQUEST
    q = str(params.get("query") or "").strip()
    if not q or len(q) > 200:
        raise BridgeError(INVALID_REQUEST, "query required (1-200 chars)", http_status=400)
    root = _safe_path(str(params.get("path") or "."), must_exist=True)
    pattern = str(params.get("glob") or "*")
    content = bool(params.get("content", True))
    limit = min(int(params.get("limit") or 50), 200)
    ql = q.lower(); hits: list[dict[str, Any]] = []; scanned = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d.lower() not in _SKIP and not d.startswith(".")]
        for fn in fns:
            if not fnmatch.fnmatch(fn.lower(), pattern.lower()):
                continue
            fp = os.path.join(dp, fn)
            scanned += 1
            if scanned > 20000:
                break
            if ql in fn.lower():
                hits.append({"path": _rel(_safe_path(fp)), "match": "name"})
            elif content and os.path.splitext(fn)[1].lower() in _TEXT and os.path.getsize(fp) < 1_000_000:
                try:
                    with open(fp, encoding="utf-8", errors="ignore") as fh:
                        for n, line in enumerate(fh, 1):
                            if ql in line.lower():
                                hits.append({"path": _rel(_safe_path(fp)), "match": "content", "line": n, "text": line.strip()[:200]})
                                break
                except OSError:
                    continue
            if len(hits) >= limit:
                break
        if len(hits) >= limit or scanned > 20000:
            break
    return {"query": q, "root": _rel(root), "hits": hits, "count": len(hits), "scanned": scanned, "truncated": len(hits) >= limit}


def exec_code_analyze(params: dict[str, Any], rec: Any) -> dict[str, Any]:
    from nexus.bridge.executors import _safe_path, _rel
    from nexus.bridge.errors import BridgeError, INVALID_REQUEST
    fp = _safe_path(str(params.get("path") or ""), must_exist=True)
    if fp.suffix != ".py" or not fp.is_file():
        raise BridgeError(INVALID_REQUEST, "python file required", http_status=400)
    src = fp.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"path": _rel(fp), "syntax_ok": False, "syntax_error": f"{e.msg} line {e.lineno}", "lines": src.count("\n") + 1,
                "functions": [], "classes": [], "imports": [], "issues": [f"SyntaxError line {e.lineno}"]}
    funcs, classes, imports, issues = [], [], set(), []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(n, "end_lineno", n.lineno)
            funcs.append({"name": n.name, "line": n.lineno, "length": end - n.lineno + 1, "doc": bool(ast.get_docstring(n))})
            if end - n.lineno > 120:
                issues.append(f"lange Funktion {n.name} ({end - n.lineno + 1} Zeilen)")
        elif isinstance(n, ast.ClassDef):
            classes.append({"name": n.name, "line": n.lineno})
        elif isinstance(n, ast.Import):
            imports.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.add(n.module.split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.type is None:
            issues.append(f"nacktes except Zeile {n.lineno}")
        elif isinstance(n, ast.Call) and getattr(n.func, "id", "") in ("eval", "exec"):
            issues.append(f"{n.func.id}() Zeile {n.lineno}")
    return {"path": _rel(fp), "syntax_ok": True, "lines": src.count("\n") + 1, "functions": funcs[:300], "classes": classes,
            "imports": sorted(imports), "issues": issues[:100]}


def exec_system_diagnose(params: dict[str, Any], rec: Any) -> dict[str, Any]:
    disks = {}
    for d in ("C:\\", "G:\\"):
        try:
            u = shutil.disk_usage(d); disks[d] = {"free_gb": round(u.free / 2**30, 2), "used_pct": round(100 * u.used / u.total, 1)}
        except OSError:
            pass
    ports = {}
    for name, port in (("core", 8299), ("ollama", 11434)):
        s = socket.socket(); s.settimeout(0.5)
        ports[name] = s.connect_ex(("127.0.0.1", port)) == 0; s.close()
    mem = {}
    try:
        import ctypes
        class M(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(f, ctypes.c_ulonglong) for f in ("tp", "ap", "tf", "af", "tv", "av", "ae")]
        m = M(); m.l = ctypes.sizeof(M); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        mem = {"load_pct": int(m.load), "free_gb": round(m.ap / 2**30, 2), "total_gb": round(m.tp / 2**30, 2)}
    except Exception:
        pass
    warn = [f"{d} {v['used_pct']}% belegt" for d, v in disks.items() if v["used_pct"] > 90]
    if mem.get("load_pct", 0) > 85:
        warn.append(f"RAM {mem['load_pct']}%")
    warn += [f"{k} nicht erreichbar" for k, v in ports.items() if not v]
    return {"disks": disks, "memory": mem, "ports": ports, "cpu_count": os.cpu_count(), "warnings": warn, "healthy": not warn}


EXECUTORS = {"search_files": exec_search_files, "code_analyze": exec_code_analyze, "system_diagnose": exec_system_diagnose}
