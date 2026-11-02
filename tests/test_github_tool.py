"""Stufen-Tests GitHub-Werkzeug (20261102)."""
import pytest
from nexus.integrations import github_tool as g
from nexus.terminal import guard, service

R, W, P = "READ_ONLY", "SAFE_WRITE", "PRIVILEGED"

@pytest.mark.parametrize("cmd,lvl", [
    ("gh repo list --limit 100", R), ("gh repo view owner/x", R), ("gh issue list -R o/x", R),
    ("gh issue view 3", R), ("gh pr list", R), ("gh pr view 5", R), ("gh pr diff 5", R),
    ("gh api repos/o/x/issues", R), ("gh api -X GET repos/o/x", R), ("gh auth status", R),
    ("gh repo clone o/x", R), (r"gh repo clone o/x G:\meine-ki\repos\x", R),
    (r"git clone https://github.com/o/x.git G:\meine-ki\repos\x", R), ("git status", R), ("git log -5", R),
    (r"& 'G:\meine-ki\tools\gh\app\bin\gh.exe' repo list", R),
    ("gh issue create -t a -b b", W), ("gh issue comment 3 -b hi", W), ("gh pr create -B main -H feat", W),
    ("gh pr comment 5 -b ok", W), ("git checkout -b feat/x", W), ("git branch feat/y", W),
    ("git commit -m msg", W), ("git push origin feat/x", W), ("git push -u origin feature-1", W),
    ("gh api repos/o/x/issues/3/comments -f body=hi", W),
    (r"git clone https://github.com/o/x.git G:\other\x", W),
    ("git push origin main", P), ("git push origin master", P), ("git push", P), ("git push origin", P),
    ("git push origin HEAD:main", P), ("git push --force origin feat", P), ("git push origin :feat", P),
    ("git push origin +feat", P), ("gh repo delete o/x --yes", P), ("gh repo rename y", P),
    ("gh repo edit --visibility public", P), ("gh pr merge 5", P), ("gh secret set X", P),
    ("gh variable set X", P), ("gh workflow run ci.yml", P), ("gh run rerun 1", P),
    ("gh api -X DELETE repos/o/x", P), ("gh api -X PUT repos/o/x/collaborators/u", P),
    ("gh api repos/o/x/collaborators/u -X PUT", P), ("gh repo create new", P), ("gh extension install x", P),
    ("git branch -D feat", P), ("git merge feat", P), ("git reset --hard", P),
])
def test_classify(cmd, lvl):
    assert g.classify(cmd)[0] == lvl, cmd
    assert guard.required_level(cmd)[0] == lvl, cmd

@pytest.mark.parametrize("cmd", ["gh auth token", "gh auth status --show-token", "gh auth status -t",
    "gh auth login", "gh auth logout", "gh auth refresh -s repo", "gh auth setup-git", "git credential fill",
    "git config --global credential.helper store", "echo $env:GH_TOKEN", r"& 'G:\meine-ki\tools\gh\app\bin\gh.exe' auth token"])
def test_token_blocked(cmd):
    assert g.hard_block(cmd)[0]
    ok, why = service.check_command(cmd, r"G:\meine-ki\repos")
    assert not ok and "TOKEN" in why

def test_non_github_unchanged():
    assert g.classify("Get-ChildItem") is None
    assert guard.required_level("Get-ChildItem")[0] == R
    assert guard.required_level("curl.exe http://x")[0] == P

def test_mask_github_tokens():
    for t in ("ghp_" + "a" * 36, "gho_" + "b" * 36, "ghu_" + "c" * 36, "ghs_" + "d" * 36, "github_pat_" + "e" * 40):
        assert t not in service.mask("x " + t)

def test_privileged_needs_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "TERM_DIR", tmp_path)
    a = guard.authorize("Hermes", "git push origin main")
    assert not a["allowed"] and a["status"] == "APPROVAL_REQUIRED" and a["approval_id"]
    assert guard.authorize("Hermes", "gh repo list")["allowed"]
    assert guard.authorize("NEXUS", "git push origin feat/x")["allowed"]

def test_tool_registered():
    from nexus.master import tools
    assert "github" in tools.KNOWN_TOOLS
    assert tools._normalize_call({"name": "gh", "args": {"command": "gh repo list"}})["name"] == "github"
    from nexus.integrations import hermes_tools
    assert any(t["function"]["name"] == "github" for t in hermes_tools.TOOLS)

def test_run_rejects_non_github():
    assert g.run("Get-ChildItem")["status"] == "BLOCKED"
    assert g.run("gh auth token")["status"] == "BLOCKED"
