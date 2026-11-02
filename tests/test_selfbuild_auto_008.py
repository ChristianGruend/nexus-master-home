"""008-AUTO: autonome Skill-Uebernahme ohne Freigabe, mit Backup, Nachtest, Rollback, Audit."""
import json
from nexus.skills import registry


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "USER_SKILLS", tmp_path / "skills" / "user_skills.json")
    monkeypatch.setattr(registry, "PENDING_DIR", tmp_path / "skills" / "pending")
    monkeypatch.setattr(registry, "SELFBUILD_AUDIT", tmp_path / "audit.jsonl")
    (tmp_path / "skills" / "pending").mkdir(parents=True)
    registry.USER_SKILLS.write_text('{"alt": {"steps": [{"action": "read_file"}], "runs": 0}}', encoding="utf-8")


def test_auto_apply_without_approval(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(registry, "run_user_skill", lambda n, p=None: {"success": True, "status": "succeeded"})
    out = registry.auto_apply_user_skill("neu_skill", {"steps": [{"action": "read_file", "params": {"path": "x"}}]}, {})
    assert out["applied"] and not out["rolled_back"]
    assert "neu_skill" in json.loads(registry.USER_SKILLS.read_text(encoding="utf-8"))
    assert '"applied": true' in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


def test_broken_change_is_rolled_back(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    before = registry.USER_SKILLS.read_text(encoding="utf-8")
    monkeypatch.setattr(registry, "run_user_skill", lambda n, p=None: {"success": False, "status": "failed"})
    out = registry.auto_apply_user_skill("kaputt_skill", {"steps": [{"action": "read_file"}]}, {})
    assert out["rolled_back"] and not out["applied"]
    assert registry.USER_SKILLS.read_text(encoding="utf-8") == before
    assert '"rolled_back": true' in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


def test_privileged_still_blocked(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = registry.auto_apply_user_skill("priv_skill", {"steps": [{"action": "nexus_restart"}]}, {})
    assert out["rolled_back"] and out["reason"] == "privileged_not_allowed_in_skill"