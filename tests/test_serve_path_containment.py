"""_is_within: Pfad-Eingrenzung für _serve_static/_serve_file (Präfix-Bug, '..', Symlinks)."""
import os

import pytest

from nexus.chat.server import _is_within


def test_file_directly_in_root(tmp_path):
    f = tmp_path / "a.js"
    f.touch()
    assert _is_within(tmp_path, f)


def test_file_in_subfolder(tmp_path):
    (tmp_path / "sub").mkdir()
    f = tmp_path / "sub" / "a.js"
    f.touch()
    assert _is_within(tmp_path, f)


def test_root_itself(tmp_path):
    assert _is_within(tmp_path, tmp_path)


def test_sibling_with_same_prefix_is_outside(tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    (tmp_path / "static_evil").mkdir()
    f = tmp_path / "static_evil" / "a.js"
    f.touch()
    assert not _is_within(root, f)


def test_dotdot_escape_is_outside(tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    assert not _is_within(root, root / ".." / "x")


def test_symlink_pointing_outside_is_outside(tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.touch()
    link = root / "link.txt"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks nicht erlaubt (Windows ohne Entwicklermodus)")
    assert not _is_within(root, link)
