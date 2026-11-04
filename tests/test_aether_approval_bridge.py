import pytest

from nexus.integrations import aether_supervisor as S
from nexus.terminal import guard as TG


def _guard_spec():
    return S.AgentSpec(**S.TEMPLATES["fileops"])


def test_pending_request_does_not_allow(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "LOG", tmp_path / "aether.jsonl")
    g = S.PolicyGuard()
    aid = S.request_approval("delete_temp", {"name": "x.txt"})
    assert aid.startswith("apr_")
    assert any(a["approval_id"] == aid for a in TG.pending_approvals())
    with pytest.raises(S.PolicyDenied, match="APPROVAL_REQUIRED"):
        g.check(_guard_spec(), "delete_temp", {"name": "x.txt"}, 1, aid)


def test_human_approval_is_single_use_and_bound(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "LOG", tmp_path / "aether.jsonl")
    g = S.PolicyGuard()
    aid = S.request_approval("delete_temp", {"name": "x.txt"})
    assert TG.decide(aid, True)["success"]
    with pytest.raises(S.PolicyDenied, match="APPROVAL_REQUIRED"):
        g.check(_guard_spec(), "delete_temp", {"name": "y.txt"}, 1, aid)
    g.check(_guard_spec(), "delete_temp", {"name": "x.txt"}, 1, aid)
    with pytest.raises(S.PolicyDenied, match="APPROVAL_REQUIRED"):
        g.check(_guard_spec(), "delete_temp", {"name": "x.txt"}, 1, aid)


def test_denied_approval_does_not_allow(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "LOG", tmp_path / "aether.jsonl")
    aid = S.request_approval("delete_temp", {"name": "x.txt"})
    assert TG.decide(aid, False)["success"]
    with pytest.raises(S.PolicyDenied, match="APPROVAL_REQUIRED"):
        S.PolicyGuard().check(_guard_spec(), "delete_temp", {"name": "x.txt"}, 1, aid)


def test_grant_approval_alias_only_creates_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "LOG", tmp_path / "aether.jsonl")
    aid = S.grant_approval("delete_temp", {"name": "x.txt"})
    with pytest.raises(S.PolicyDenied, match="APPROVAL_REQUIRED"):
        S.PolicyGuard().check(_guard_spec(), "delete_temp", {"name": "x.txt"}, 1, aid)
    assert any(a["approval_id"] == aid for a in TG.pending_approvals())
