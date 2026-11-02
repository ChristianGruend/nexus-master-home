"""Projekt-Dateisystem unter NEXUS-ROOT mit Path-Traversal-Schutz."""
from __future__ import annotations

import os
from pathlib import Path

from nexus.common.paths import ROOT, SECRETS, ensure_dirs

_MAX_READ = 200_000


class ProjectFS:
    """Lesen/Schreiben/Listen relativ zu ROOT. Secrets-Inhalt nie dumpen."""

    def __init__(self, root: Path | None = None) -> None:
        ensure_dirs()
        self.root = (root or ROOT).resolve()
        self._secrets = SECRETS.resolve()

    def _safe(self, rel: str) -> Path | None:
        rel = (rel or "").strip().lstrip("/").replace("\\", "/")
        if not rel or ".." in rel.split("/"):
            return None
        try:
            fp = (self.root / rel).resolve()
        except Exception:
            return None
        root_s = str(self.root)
        if not (str(fp) == root_s or str(fp).startswith(root_s + os.sep)):
            return None
        return fp

    def _is_secrets(self, fp: Path) -> bool:
        try:
            sec = str(self._secrets)
            s = str(fp.resolve())
            return s == sec or s.startswith(sec + os.sep)
        except Exception:
            return False

    def list(self, sub: str = "") -> dict:
        if sub:
            base = self._safe(sub)
        else:
            base = self.root
        if base is None:
            return {"success": False, "error": "ungültiger Pfad", "items": []}
        if self._is_secrets(base):
            return {"success": False, "error": "Zugriff auf workspace/secrets verweigert", "items": []}
        if not base.exists():
            return {"success": False, "error": "Pfad nicht gefunden", "items": []}
        if not base.is_dir():
            return {"success": False, "error": "kein Verzeichnis", "items": []}
        items = []
        for p in sorted(base.iterdir()):
            if self._is_secrets(p):
                items.append({"name": p.name, "is_dir": True, "size": 0, "blocked": True})
                continue
            items.append({
                "name": p.name,
                "is_dir": p.is_dir(),
                "size": p.stat().st_size if p.is_file() else 0,
            })
        try:
            rel = "" if base == self.root else str(base.relative_to(self.root)).replace("\\", "/")
        except ValueError:
            rel = sub or ""
        return {"success": True, "path": rel, "items": items}

    def read(self, rel: str, max_bytes: int = _MAX_READ) -> dict:
        fp = self._safe(rel)
        if fp is None:
            return {"success": False, "error": "ungültiger Pfad"}
        if self._is_secrets(fp):
            return {"success": False, "error": "Secrets-Inhalt wird nicht ausgegeben"}
        if not fp.is_file():
            return {"success": False, "error": "Datei nicht gefunden"}
        data = fp.read_bytes()[:max_bytes]
        try:
            text = data.decode("utf-8")
        except Exception:
            text = data.decode("utf-8", errors="replace")
        return {"success": True, "path": rel, "content": text, "bytes": len(data)}

    def write(self, rel: str, content: str) -> dict:
        fp = self._safe(rel)
        if fp is None:
            return {"success": False, "error": "ungültiger Pfad"}
        if self._is_secrets(fp):
            return {"success": False, "error": "Schreiben in workspace/secrets verweigert"}
        try:
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content if isinstance(content, str) else str(content), encoding="utf-8")
            return {"success": True, "path": rel, "bytes": fp.stat().st_size}
        except Exception as e:
            return {"success": False, "error": str(e)}
