"""Report unverified Hermes quarantine candidates without touching their files.

Path differences and filename patterns prove no authorship. Current callers
supply no trustworthy process-bound creation receipts, so ALL candidates are
report-only. Request IDs correlate reports; they are not provenance. There is
no automatic move API or caller-supplied flag that can promote a candidate.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QROOT = ROOT / "workspace" / "hermes_quarantine"
QLOG = ROOT / "workspace" / "hermes_service" / "quarantine.log"
TEST_PATTERNS = ("qa_*", "decoy*", "*_hermes_test*", "zk_brk*", "qa-*")
SENSITIVE_PARTS = {".hermes", "bridge", "secrets", "secure", "certs", ".ssh", ".gnupg", "credentials"}
SENSITIVE_NAMES = re.compile(r"(?i)(^\.env(\..*)?$|token|secret|passw|\.pem$|\.key$|\.pfx$|\.p12$|id_rsa|id_ed25519|\.kdbx$)")


def is_sensitive(path: str) -> bool:
    parts = [x.lower() for x in str(path).replace("/", "\\").split("\\") if x]
    return any(x in SENSITIVE_PARTS for x in parts) or bool(parts and SENSITIVE_NAMES.search(parts[-1]))


def is_test_file(path: str) -> bool:
    name = os.path.basename(str(path)).lower()
    return any(fnmatch.fnmatch(name, pat) for pat in TEST_PATTERNS)


def _log(entry: dict) -> bool:
    """Write metadata only to the project's workspace, never to a candidate."""
    try:
        if not QLOG.resolve().is_relative_to((ROOT / "workspace").resolve()):
            return False
        QLOG.parent.mkdir(parents=True, exist_ok=True)
        with open(QLOG, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def report_candidates(paths, rid: str, reason: str) -> dict:
    """Unproven files stay put, even if their name/reason claims ownership."""
    reported, skipped, failed, entries = [], [], [], []
    for raw in paths or []:
        # No stat, resolve, hash, content read, rename or copy of a candidate.
        source = str(raw)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "rid": str(rid),
                 "reason": str(reason), "original_path": source,
                 "origin": "unverified", "action": "report_only"}
        if is_sensitive(source):
            skipped.append(source)
            entry["result"] = "skipped_sensitive"
        else:
            reported.append(source)
            entry["result"] = "reported_unproven_origin"
        written = _log(entry)
        entries.append({**entry, "log_written": written})
        if not written:
            failed.append(source)
    return {"mode": "report_only", "quarantine_dir": None, "moved": [],
            "reported": reported, "skipped": skipped, "failed": failed,
            "entries": entries}


def quarantine(paths, rid: str, reason: str) -> dict:
    """Backward-compatible safe entry point for already-loaded callers."""
    return report_candidates(paths, rid, reason)


def sweep_test_files(root, rid: str = "sweep") -> dict:
    """Filename patterns discover candidates, never establish job ownership."""
    hits = []
    candidate_root = Path(root)
    if candidate_root.is_dir():
        for directory, dirs, files in os.walk(candidate_root):
            dirs[:] = [name for name in dirs
                       if not is_sensitive(os.path.join(directory, name))
                       and not Path(directory, name).is_symlink()
                       and not getattr(os.path, "isjunction", lambda path: False)(os.path.join(directory, name))]
            hits.extend(os.path.join(directory, name) for name in files if is_test_file(name))
    return report_candidates(hits, rid, "filename_pattern_only")
