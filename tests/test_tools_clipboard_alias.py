"""Regression: clipboard-Aliase in _normalize_call nutzten args vor der Definition (UnboundLocalError)."""
from nexus.master.tools import _normalize_call


def _call(name, args=None):
    obj = {"name": name}
    if args is not None:
        obj["args"] = args
    return _normalize_call(obj)


def test_clipboard_set_alias_sets_action():
    out = _call("clipboard_set", {"text": "x"})
    assert out["name"] == "clipboard"
    assert out["args"]["action"] == "set"


def test_clipboard_get_alias_defaults_to_get():
    out = _call("zwischenablage")
    assert out["name"] == "clipboard"
    assert out["args"]["action"] == "get"


def test_explicit_action_is_kept():
    out = _call("clipboard_set", {"action": "paste"})
    assert out["args"]["action"] == "paste"
