"""Phase 2 Schritt 3: gemeinsame Pfad-Pruefung, Shims unveraendert."""
import pytest
from nexus.safety import paths
from nexus.autobuild import guard as ab
from nexus.bridge.dev_executors import nexus_target_allowed


def test_c_blocked_both_scopes():
    for s in ("autobuild", "nexus_patch"):
        with pytest.raises(paths.CDriveBlocked):
            paths.check_write(r"C:\Windows\x.txt", s)


def test_autobuild_shim_same_exception():
    with pytest.raises(ab.CDriveBlocked):
        ab.check_write(r"C:\temp\a.py")
    assert ab.check_write("nexus/x.py").drive.upper() == "G:"
    assert issubclass(ab.CDriveBlocked, PermissionError)


def test_nexus_patch_outside_root():
    with pytest.raises(paths.PathBlocked):
        paths.check_write(r"G:\other\x.py", "nexus_patch")


def test_nexus_target_allowed_unchanged():
    assert nexus_target_allowed("nexus/tasks/goal_planner.py") == "nexus/tasks/goal_planner.py"
    assert nexus_target_allowed("../x.py") is None
    assert nexus_target_allowed("nexus/bridge_gateway/gateway_allowlist.py") is None
