"""Gemeinsame Test-Einstellungen (20261031): Tests schreiben NIE in das echte NEXUS-Terminal-Log,
die Freigaben oder das Audit von Eddie, sondern in ein temporaeres Verzeichnis."""
import pytest


@pytest.fixture(autouse=True)
def _nexus_terminal_tmp(tmp_path_factory, monkeypatch):
    try:
        from nexus.terminal import service
    except Exception:
        yield
        return
    d = tmp_path_factory.mktemp("nexus_terminal")
    monkeypatch.setattr(service, "TERM_DIR", d)
    monkeypatch.setattr(service, "LOG_FILE", d / "terminal.jsonl")
    yield
