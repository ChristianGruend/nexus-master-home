"""Stufe 2.4: Autobuild darf nur nexus/** (ohne Guard/Policy/Autobuild/Runner) und tests/test_auto_*.py ändern."""
import pytest

from nexus.autobuild.scope import check_path, normalize, violations

ALLOWED = ["nexus/chat/engine.py", "nexus/master/tools.py", "./nexus/voice/tts.py",
           "nexus\\studio\\video.py", "tests/test_auto_sb_12.py"]
PROTECTED = ["nexus/safety/paths.py", "nexus/autobuild/loop.py", "nexus/autobuild/scope.py",
             "nexus/runner/server.py", "nexus/terminal/guard.py", "nexus/common/api_auth.py",
             "nexus/chat/server.py", "nexus/Safety/paths.py", "NEXUS/autobuild/loop.py"]
OUTSIDE = ["run_server.py", "tests/test_autobuild.py", "tests/sub/test_auto_x.py", "compose.yaml",
           "Dockerfile", "docs/x.md", "nexus_evil/x.py"]
INVALID = ["", "/etc/passwd", "C:/x.py", "nexus/../run_server.py", "../nexus/x.py", "nexus//x.py",
           ".git/config", "nexus/.git/hooks/x", "nexus/./safety/paths.py", "nexus/.GIT/x",
           "nexus/.\\../run_server.py",
           ".github/workflows/tests.yml"]  # .git*-Teile sind generell tabu


@pytest.mark.parametrize("p", ALLOWED)
def test_allowed(p):
    assert check_path(p)[0] is True, check_path(p)


@pytest.mark.parametrize("p", PROTECTED)
def test_protected(p):
    ok, why = check_path(p)
    assert not ok and why.startswith("GESCHUETZT"), why


@pytest.mark.parametrize("p", OUTSIDE)
def test_outside(p):
    assert check_path(p) == (False, "AUSSERHALB_ALLOWLIST")


@pytest.mark.parametrize("p", INVALID)
def test_invalid(p):
    assert check_path(p) == (False, "UNGUELTIGER_PFAD")


def test_violations():
    v = violations(["nexus/a.py", "nexus/safety/gate.py", "x.py"])
    assert len(v) == 2 and v[0].startswith("nexus/safety/gate.py: GESCHUETZT")


def test_normalize():
    assert normalize("./nexus/a.py") == "nexus/a.py"
    assert normalize("nexus\\a.py") == "nexus/a.py"
