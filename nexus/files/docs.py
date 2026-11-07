from __future__ import annotations
import os
from pathlib import Path
from nexus.common.paths import FILES_DIR, ensure_dirs
from nexus.common.response import envelope


class FileDocs:
    """Sandbox unter workspace/files — Path-Traversal gesperrt."""

    def __init__(self) -> None:
        ensure_dirs()
        self.root = FILES_DIR.resolve()

    def _safe(self, rel: str) -> Path | None:
        rel = (rel or "").strip().lstrip("/").replace("\\", "/")
        if ".." in rel.split("/"):
            return None
        try:
            fp = (self.root / rel).resolve()
        except Exception:
            return None
        root_s = str(self.root)
        fp_s = str(fp)
        # Match project_fs / bridge: require root or root+sep (blocks root_evil prefix)
        if not (fp_s == root_s or fp_s.startswith(root_s + os.sep)):
            return None
        return fp

    def _protected(self, fp: Path | None) -> bool:
        """Stufe 2.1b: geschuetzte Pfade (Secrets, Policy, Tokens) nie ausliefern/aendern."""
        from nexus.safety.protected import is_protected
        return fp is not None and is_protected(fp)

    @staticmethod
    def _denied(rel: str) -> dict:
        return envelope(False, error="PROTECTED_PATH", message="geschuetzter Pfad", details={"path": rel})

    def list(self, sub: str = "") -> dict:
        if sub:
            base = self._safe(sub)
            if base is None:
                return envelope(False, error="ungueltiger_pfad", message="ungueltiger Pfad", details={"path": sub}, items=[])
        else:
            base = self.root
        if self._protected(base):
            return {**self._denied(sub), "items": []}
        if not base.exists():
            return envelope(False, error="pfad_nicht_gefunden", message="Pfad nicht gefunden", details={"path": sub or ""}, items=[])
        if not base.is_dir():
            return envelope(False, error="kein_verzeichnis", message="kein Verzeichnis", details={"path": sub or ""}, items=[])
        items = []
        for p in sorted(base.iterdir()):
            if self._protected(p):
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
        return envelope(True, path=rel, items=items)

    def read(self, rel: str, max_bytes: int = 200_000) -> dict:
        fp = self._safe(rel)
        if fp is None:
            return envelope(False, error="ungueltiger_pfad", message="ungueltiger Pfad", details={"path": rel})
        if self._protected(fp):
            return self._denied(rel)
        if not fp.is_file():
            return envelope(False, error="datei_nicht_gefunden", message="Datei nicht gefunden", details={"path": rel})
        data = fp.read_bytes()[:max_bytes]
        try:
            text = data.decode("utf-8")
        except Exception:
            text = data.decode("utf-8", errors="replace")
        return envelope(True, path=rel, content=text, bytes=len(data))

    def write(self, rel: str, content: str) -> dict:
        fp = self._safe(rel)
        if fp is None:
            return envelope(False, error="ungueltiger_pfad", message="ungueltiger Pfad", details={"path": rel})
        if self._protected(fp):
            return self._denied(rel)
        try:
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content if isinstance(content, str) else str(content), encoding="utf-8")
            return envelope(True, path=rel, bytes=fp.stat().st_size)
        except Exception as e:
            return envelope(False, error="write_failed", message="Schreiben fehlgeschlagen", details={"path": rel, "reason": str(e)})
