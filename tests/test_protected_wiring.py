"""Stufe 2.1b: Agent-Dateiwerkzeuge lehnen geschuetzte Pfade ab (Secrets, Policy, Tokens).

Die Schutzliste wird per monkeypatch auf ein tmp-Verzeichnis gelegt; so kann ein
Verdrahtungsfehler nie echte Dateien des Projekts beruehren.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from nexus.safety import protected as P


@pytest.fixture
def env(tmp_path, monkeypatch):
    """tmp-Projekt mit workspace/secrets, workspace/terminal/* und einer normalen Datei."""
    root = Path(os.path.realpath(tmp_path))
    ws = root / "workspace"
    (ws / "secrets").mkdir(parents=True)
    (ws / "terminal").mkdir()
    (ws / "files").mkdir()
    (root / "nexus").mkdir()
    (ws / "secrets" / "api_token.txt").write_text("GEHEIM-NEEDLE", encoding="utf-8")
    (ws / "secrets" / "notes.txt").write_text("GEHEIM-NEEDLE", encoding="utf-8")
    (ws / "terminal" / "ui_secret.txt").write_text("GEHEIM-NEEDLE", encoding="utf-8")
    (ws / "terminal" / "permissions.json").write_text('{"orig": true}', encoding="utf-8")
    (ws / "files" / "ok.txt").write_text("normal NEEDLE", encoding="utf-8")
    (root / "nexus" / "ok.py").write_text("X = 1\n", encoding="utf-8")
    (root / ".env").write_text("K=V", encoding="utf-8")
    monkeypatch.setattr(P, "_protected_dirs", lambda: [ws / "secrets", ws / "bridge" / "approvals", root / ".git"])
    monkeypatch.setattr(P, "_protected_files", lambda: [
        ws / "terminal" / "permissions.json", ws / "terminal" / "approvals.json",
        ws / "terminal" / "ui_secret.txt", ws / "terminal" / "audit.jsonl", ws / "bridge" / ".token"])
    return root, ws


PERM_ORIG = '{"orig": true}'


# ---------------------------------------------------------------- Hermes (hermes_tools)
def test_hermes_tools(env, monkeypatch):
    import nexus.integrations.hermes_tools as ht
    root, ws = env
    monkeypatch.setattr(ht, "ROOT", root)
    monkeypatch.setattr(ht, "WS", ws)
    wd = ws / "terminal"  # Arbeitsverzeichnis = Policy-Ordner: Schreiben dort muss trotzdem scheitern
    with pytest.raises(P.ProtectedPathError):
        ht.run_tool("read_file", {"path": str(ws / "secrets" / "notes.txt")}, wd)
    with pytest.raises(PermissionError):
        ht.run_tool("read_file", {"path": str(ws / "secrets" / "api_token.txt")}, wd)
    with pytest.raises(PermissionError):
        ht.run_tool("read_file", {"path": str(ws / "terminal" / "ui_secret.txt")}, wd)
    with pytest.raises(P.ProtectedPathError):
        ht.run_tool("write_file", {"path": "permissions.json", "content": "{}"}, wd)
    assert (ws / "terminal" / "permissions.json").read_text(encoding="utf-8") == PERM_ORIG
    # Symlink auf geschuetzte Datei wird per realpath erkannt
    link = ws / "files" / "link.txt"
    try:
        link.symlink_to(ws / "secrets" / "notes.txt")
    except OSError:
        link = None
    if link:
        with pytest.raises(P.ProtectedPathError):
            ht.run_tool("read_file", {"path": str(link)}, ws / "files")
    # normaler Pfad geht weiter; Listen/Suchen blenden Geschuetztes aus
    assert ht.run_tool("read_file", {"path": str(ws / "files" / "ok.txt")}, ws / "files") == "normal NEEDLE"
    ht.run_tool("write_file", {"path": "neu.txt", "content": "x"}, ws / "files")
    listing = ht.run_tool("list_directory", {"path": str(ws)}, ws / "files")
    assert "files/" in listing and "secrets" not in listing
    lt = ht.run_tool("list_files", {"path": str(ws / "terminal")}, ws / "files")
    assert "permissions.json" not in lt and "ui_secret.txt" not in lt
    hits = ht.run_tool("search_files", {"path": str(ws), "pattern": "*"}, ws / "files")
    assert "ok.txt" in hits and "notes.txt" not in hits and "permissions.json" not in hits


def test_hermes_project_write_blocked(env, monkeypatch):
    import nexus.integrations.hermes_tools as ht
    root, ws = env
    monkeypatch.setattr(ht, "ROOT", root)
    monkeypatch.setattr(ht, "WS", ws)
    locked = root / "nexus" / "locked"
    locked.mkdir()
    monkeypatch.setattr(P, "_protected_dirs", lambda: [locked])
    with pytest.raises(P.ProtectedPathError):
        ht.run_tool("project_write_request", {"path": "nexus/locked/x.py", "content": "x", "reason": "r"}, ws)


# ---------------------------------------------------------------- Bridge-Executors
def _bridge(env, monkeypatch):
    import nexus.bridge.executors as ex
    root, ws = env
    monkeypatch.setattr(ex, "ALLOWED_ROOT", root)
    monkeypatch.setattr(ex, "BACKUP_DIR", root / "bk")
    return ex


def _code(excinfo) -> str:
    return excinfo.value.code


def test_bridge_read_write_list(env, monkeypatch):
    ex = _bridge(env, monkeypatch)
    root, ws = env
    from nexus.bridge.errors import BridgeError
    for rel in ("workspace/terminal/ui_secret.txt", "workspace/secrets/api_token.txt", "workspace/secrets/notes.txt", ".env"):
        with pytest.raises(BridgeError):
            ex.exec_read_file({"path": rel}, None)
    # echter Schutz-Code (nicht der alte Secret-Name-Filter): Datei ohne "secret/token" im Namen
    with pytest.raises(BridgeError) as e:
        ex.exec_read_file({"path": "workspace/terminal/permissions.json"}, None)
    assert _code(e) == "PROTECTED_PATH" and e.value.http_status == 403
    for fn, params in (
        (ex.exec_write_file, {"path": "workspace/terminal/permissions.json", "content": "{}"}),
        (ex.exec_create_file, {"path": "workspace/terminal/approvals.json", "content": "{}"}),
        (ex.exec_patch_file, {"path": "workspace/terminal/permissions.json", "old_text": "orig", "new_text": "x"}),
        (ex.exec_delete_file, {"path": "workspace/terminal/permissions.json", "confirm": True}),
        (ex.exec_move_file, {"src": "workspace/terminal/permissions.json", "dst": "workspace/files/p.json"}),
        (ex.exec_copy_file, {"path": "workspace/terminal/permissions.json", "destination": "workspace/files/p.json"}),
        (ex.exec_move_file, {"src": "workspace/files/ok.txt", "dst": "workspace/terminal/permissions.json"}),
    ):
        with pytest.raises(BridgeError) as e:
            fn(params, None)
        assert _code(e) == "PROTECTED_PATH", fn.__name__
    assert (ws / "terminal" / "permissions.json").read_text(encoding="utf-8") == PERM_ORIG
    assert not (ws / "files" / "p.json").exists()
    # ganze Baeume mit geschuetztem Inhalt: weder loeschen noch kopieren noch verschieben
    for fn, params in (
        (ex.exec_delete_file, {"path": "workspace/terminal", "confirm": True, "recursive": True}),
        (ex.exec_copy_file, {"path": "workspace/terminal", "destination": "workspace/files/t"}),
        (ex.exec_move_file, {"src": "workspace/terminal", "dst": "workspace/files/t"}),
    ):
        with pytest.raises(BridgeError) as e:
            fn(params, None)
        assert _code(e) == "PROTECTED_PATH"
    assert (ws / "terminal" / "ui_secret.txt").exists()
    # normaler Pfad geht weiter
    assert ex.exec_read_file({"path": "workspace/files/ok.txt"}, None)["content"] == "normal NEEDLE"
    assert ex.exec_write_file({"path": "workspace/files/n.txt", "content": "a"}, None)["bytes"] == 1
    # Liste blendet Geschuetztes aus
    names = {x["name"] for x in ex.exec_list_files({"path": "workspace/terminal"}, None)["entries"]}
    assert "permissions.json" not in names and "ui_secret.txt" not in names
    names = {x["name"] for x in ex.exec_list_files({"path": "."}, None)["entries"]}
    assert ".env" not in names and "workspace" in names


def test_bridge_search_files_skips_protected(env, monkeypatch):
    _bridge(env, monkeypatch)
    from nexus.bridge import tools_008
    root, ws = env
    (root / "visible").mkdir()
    (root / "visible" / ".env.local").write_text("NEEDLE", encoding="utf-8")
    (root / "visible" / "a.txt").write_text("NEEDLE", encoding="utf-8")
    (root / "visible" / "id_rsa").write_text("NEEDLE", encoding="utf-8")
    r = tools_008.exec_search_files({"query": "needle", "path": "."}, None)
    paths = {h["path"].replace("\\", "/") for h in r["hits"]}
    assert "workspace/files/ok.txt" in paths and "visible/a.txt" in paths
    assert not any("secrets" in p or "ui_secret" in p or "permissions" in p or ".env" in p or "id_rsa" in p for p in paths)


def test_bridge_code_analyze_protected(env, monkeypatch):
    _bridge(env, monkeypatch)
    from nexus.bridge import tools_008
    from nexus.bridge.errors import BridgeError
    root, ws = env
    with pytest.raises(BridgeError):
        tools_008.exec_code_analyze({"path": "workspace/secrets/notes.txt"}, None)


def test_bridge_search_log_protected(env, monkeypatch):
    ex = _bridge(env, monkeypatch)
    from nexus.bridge.errors import BridgeError
    with pytest.raises(BridgeError):
        ex.exec_search_log({"path": "workspace/terminal/audit.jsonl", "pattern": "x"}, None)


# ---------------------------------------------------------------- Dev-Executors (Patches)
def test_dev_patch_targets(env, monkeypatch):
    import nexus.bridge.dev_executors as de
    import nexus.safety.paths as sp
    root, ws = env
    monkeypatch.setattr(de, "ROOT", root)
    monkeypatch.setattr(sp, "ROOT", root)
    locked = root / "nexus" / "locked"
    locked.mkdir()
    (locked / "x.py").write_text("x=1\n", encoding="utf-8")
    monkeypatch.setattr(P, "_protected_dirs", lambda: [locked])
    assert de.nexus_target_allowed("nexus/locked/x.py") is None
    assert de.nexus_target_allowed("nexus/ok.py") == "nexus/ok.py"


# ---------------------------------------------------------------- Code-Sandbox
def test_code_sandbox_inside_and_copy(env, monkeypatch):
    from nexus.bridge import code_sandbox as cs
    root, ws = env
    proj = root / "workspace" / "projects" / "hermes" / "code" / "p"
    proj.mkdir(parents=True)
    (proj / "ok.py").write_text("a = 1\n", encoding="utf-8")
    (proj / "x.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(P, "_protected_dirs", lambda: [])
    monkeypatch.setattr(P, "_protected_files", lambda: [proj / "x.json"])
    assert cs._inside(root, "workspace/projects/hermes/code/p/ok.py") is not None
    assert cs._inside(root, "workspace/projects/hermes/code/p/x.json") is None
    dest = root / "run"
    dest.mkdir()
    assert cs.copy_project(proj, dest) == ["ok.py"]


# ---------------------------------------------------------------- AETHER
def test_aether_tools(env, monkeypatch):
    import nexus.integrations.aether_supervisor as S
    root, ws = env
    monkeypatch.setattr(S, "ROOT", root)
    monkeypatch.setattr(S._safe_path, "__defaults__", (root,))  # base=ROOT ist Default-Argument
    monkeypatch.setattr(S, "SECRETS_DIR", ws / "secrets")
    sb = ws / "sandbox"
    sb.mkdir()
    monkeypatch.setattr(S, "SANDBOX", sb)
    with pytest.raises(PermissionError):
        S.t_read_file("workspace/secrets/notes.txt")
    with pytest.raises(PermissionError):
        S.t_read_file("workspace/terminal/permissions.json")
    with pytest.raises(PermissionError):
        S.t_project_write("workspace/terminal/permissions.json", "{}")
    assert (ws / "terminal" / "permissions.json").read_text(encoding="utf-8") == PERM_ORIG
    assert "normal" in S.t_read_file("workspace/files/ok.txt")["content"]
    items = S.t_list_files("workspace/terminal")["items"]
    assert "permissions.json" not in items and "ui_secret.txt" not in items
    hits = S.t_search_files("needle", "workspace")["hits"]
    assert any("ok.txt" in h for h in hits)
    assert not any("secrets" in h or "ui_secret" in h or "permissions" in h for h in hits)
    # Sandbox: Symlink-freie Pfade, aber geschuetztes Ziel wird abgelehnt
    monkeypatch.setattr(P, "_protected_files", lambda: [sb / "locked.json"])
    with pytest.raises(PermissionError):
        S.t_sandbox_write("locked.json", "{}")
    assert S.t_sandbox_write("frei.txt", "x")["written"]


# ---------------------------------------------------------------- Master-Tools: ProjectFS
def test_project_fs(env):
    from nexus.computer.project_fs import ProjectFS
    root, ws = env
    fs = ProjectFS(root)
    for rel in ("workspace/terminal/ui_secret.txt", "workspace/terminal/permissions.json", ".env"):
        assert fs.read(rel)["success"] is False
    assert fs.write("workspace/terminal/permissions.json", "{}")["success"] is False
    assert (ws / "terminal" / "permissions.json").read_text(encoding="utf-8") == PERM_ORIG
    assert fs.list("workspace/secrets")["success"] is False
    names = {i["name"] for i in fs.list("workspace/terminal")["items"]}
    assert "permissions.json" not in names and "ui_secret.txt" not in names
    assert fs.read("workspace/files/ok.txt")["content"] == "normal NEEDLE"
    assert fs.write("workspace/files/w.txt", "ok")["success"] is True


def test_master_run_one_routes_through_project_fs(env):
    """Master-Tools read_file/write_file/list_dir laufen ueber ProjectFS."""
    from nexus.computer.project_fs import ProjectFS
    from nexus.master.tools import run_one

    class M:
        project_fs = ProjectFS(env[0])

    assert run_one({"name": "read_file", "args": {"path": "workspace/terminal/permissions.json"}}, M())["success"] is False
    assert run_one({"name": "write_file", "args": {"path": "workspace/terminal/permissions.json", "content": "{}"}}, M())["success"] is False
    assert run_one({"name": "read_file", "args": {"path": "workspace/files/ok.txt"}}, M())["success"] is True


# ---------------------------------------------------------------- OS-Aktionen / Screenshot / Skripte
def test_os_actions(env):
    from nexus.computer.os_actions import OSActions
    root, ws = env
    a = OSActions()
    r = a.open_path(str(ws / "terminal" / "permissions.json"))
    assert r["success"] is False and r["error"] == "PROTECTED_PATH"
    r = a.play_media(str(ws / "secrets" / "notes.txt"))
    assert r["success"] is False
    assert a.list_dir_os(str(ws / "secrets"))["success"] is False
    names = {i["name"] for i in a.list_dir_os(str(ws / "terminal"))["items"]}
    assert "permissions.json" not in names and "ui_secret.txt" not in names
    assert any(i["name"] == "ok.txt" for i in a.list_dir_os(str(ws / "files"))["items"])


def test_screenshot_target_protected(env):
    from nexus.computer import desktop
    root, ws = env
    r = desktop.screenshot(str(ws / "terminal" / "permissions.json"))
    assert r["success"] is False and r["error"] == "PROTECTED_PATH"
    assert (ws / "terminal" / "permissions.json").read_text(encoding="utf-8") == PERM_ORIG


def test_shell_run_script_protected(env, monkeypatch):
    from nexus.computer.shell import ShellExecutor
    root, ws = env
    (ws / "secrets" / "x.py").write_text("print(1)", encoding="utf-8")
    (ws / "files" / "y.py").write_text("print(1)", encoding="utf-8")
    sh = ShellExecutor(root)
    assert sh._resolve_script("workspace/secrets/x.py") is None
    assert sh._resolve_script("workspace/files/y.py") is not None


# ---------------------------------------------------------------- Swarm-Scout
def test_swarm_scout(env, monkeypatch):
    import nexus.integrations.hermes_tools as ht
    from nexus.swarm import core
    root, ws = env
    monkeypatch.setattr(ht, "ROOT", root)
    monkeypatch.setattr(ht, "WS", ws)
    monkeypatch.setattr(core, "WS", ws)
    with pytest.raises(PermissionError):
        core.n_scout({"paths": ["workspace/secrets/notes.txt"]})
    out = core.n_scout({"paths": ["workspace/terminal"]})["scout"][0]
    assert "permissions.json" not in " ".join(out["listing"])
    assert not any(h["file"] in ("permissions.json", "ui_secret.txt") for h in out["heads"])
    out = core.n_scout({"paths": ["workspace/files"]})["scout"][0]
    assert any(h["file"] == "ok.txt" for h in out["heads"])


# ---------------------------------------------------------------- UI-Dateirouten (chat/server.py)
def test_filedocs_and_routes(env, monkeypatch):
    from nexus.files.docs import FileDocs
    from nexus.chat import server
    root, ws = env
    fd = FileDocs()
    monkeypatch.setattr(fd, "root", ws)
    for rel in ("secrets/notes.txt", "terminal/permissions.json", "terminal/ui_secret.txt"):
        assert fd.read(rel)["error"] == "PROTECTED_PATH"
    assert fd.write("terminal/permissions.json", "{}")["error"] == "PROTECTED_PATH"
    assert fd.list("secrets")["error"] == "PROTECTED_PATH"
    names = {i["name"] for i in fd.list("terminal")["items"]}
    assert not names & {"permissions.json", "ui_secret.txt"}
    assert fd.read("files/ok.txt")["success"] is True

    class H:
        sent: list = []

        def _json(self, code, payload):
            self.sent.append((code, payload))

        def _bytes(self, code, data, ctype):
            self.sent.append((code, data))

    h = H()
    server.Handler._files_json(h, fd.read("terminal/permissions.json"))
    server.Handler._files_json(h, fd.read("files/ok.txt"))
    assert [c for c, _ in h.sent] == [403, 200]
    h.sent.clear()
    server.Handler._serve_file(h, ws, "terminal/permissions.json")
    server.Handler._serve_file(h, ws, "terminal/ui_secret.txt")
    server.Handler._serve_file(h, ws, "files/ok.txt")
    assert [c for c, _ in h.sent] == [403, 403, 200]
    assert h.sent[2][1] == b"normal NEEDLE"
