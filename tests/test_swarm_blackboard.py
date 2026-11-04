from nexus.integrations import aether_supervisor as S


def _setup(monkeypatch, tmp_path):
    sb = tmp_path / "sandbox"
    sb.mkdir()
    monkeypatch.setattr(S, "WORK", tmp_path / "aether")
    monkeypatch.setattr(S, "SANDBOX", sb)
    monkeypatch.setenv("NEXUS_SWARM", "1")
    return sb


def test_reserve_blocks_second_worker(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert S.blackboard_reserve("RUN-1", "sandbox_write", "a.txt", "w1")["ok"]
    second = S.blackboard_reserve("RUN-1", "sandbox_write", "a.txt", "w2")
    assert not second["ok"] and second["owner"] == "w1"
    assert S.blackboard_reserve("RUN-2", "sandbox_write", "a.txt", "w2")["ok"]


def test_record_merge_and_hash(monkeypatch, tmp_path):
    sb = _setup(monkeypatch, tmp_path)
    p = sb / "a.txt"
    p.write_text("OK")
    S.blackboard_reserve("RUN-2", "sandbox_write", "a.txt", "w1")
    rec = S.blackboard_record("RUN-2", "w1", "fileops", "sandbox_write", "a.txt", "PASS", path=str(p))
    assert rec and rec["sha256"] and rec["bytes"] == 2
    merged = S.blackboard_merge("RUN-2")
    assert merged["counts"] == {"PASS": 1}
    assert merged["conflicts"] == []
    assert merged["records"][0]["worker"] == "w1"


def test_secret_target_never_written(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert S.blackboard_record("RUN-3", "w1", "fileops", "read_file", ".env", "PASS", path=None) is None
    assert S.blackboard_merge("RUN-3")["records"] == []


def test_secrets_directory_is_blocked(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    sec = tmp_path / "secrets"
    sec.mkdir()
    canary = sec / "canary.txt"
    canary.write_text("KANARIENVOGEL")
    monkeypatch.setattr(S, "SECRETS_DIR", sec)
    assert S.blackboard_record("RUN-6", "w1", "fileops", "sandbox_write", str(canary), "PASS", path=str(canary)) is None
    assert S.blackboard_merge("RUN-6")["records"] == []
    import pytest
    with pytest.raises(PermissionError, match="SECRET_FILE_DENIED"):
        S._safe_path(str(canary))
    with pytest.raises(S.PolicyDenied, match="SECRET_TARGET"):
        S.ToolGateway(tools={"read_file": S.TOOLS["read_file"]}).invoke(
            "w1", "fileops", "read_file", {"path": str(canary)}, run_id="RUN-6")


def test_verify_checks_hash_and_conflicts(monkeypatch, tmp_path):
    sb = _setup(monkeypatch, tmp_path)
    p = sb / "t.txt"
    p.write_text("OK")
    S.blackboard_reserve("RUN-4", "sandbox_write", "t.txt", "w1")
    S.blackboard_record("RUN-4", "w1", "fileops", "sandbox_write", "t.txt", "PASS", path=str(p))
    task = "In der Sandbox die Datei t.txt anlegen."
    assert S.verify_result(task, run_id="RUN-4")["ok"]
    p.write_text("MANIPULIERT")
    vr = S.verify_result(task, run_id="RUN-4")
    assert not vr["ok"] and "Hash" in vr["reason"]


def test_merge_is_deterministic(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    for worker in ("w2", "w1"):
        S.blackboard_record("RUN-5", worker, "fileops", "write_note", worker + ".txt", "PASS")
    a = S.blackboard_merge("RUN-5")["records"]
    b = S.blackboard_merge("RUN-5")["records"]
    assert a == b and [r["worker"] for r in a] == ["w1", "w2"]
