"""Funktionsmodus des Selbstbaus (20261030_fnmode): AST-Ersetzung ganzer Funktionen."""
import json

import pytest

import nexus.autobuild.loop as L

SRC = '"""Doc."""\nfrom __future__ import annotations\n\nimport os\n\n\ndef a(x):\n    raise ValueError("NOT_IMPLEMENTED:a")\n\n\n@staticmethod\ndef b():\n    return 1\n'


def test_replace_existing_function_keeps_rest():
    out = L._replace_function(SRC, "a", "def a(x):\n    return x * 2\n")
    ns = {}
    exec(compile(out, "m", "exec"), ns)
    assert ns["a"](3) == 6 and "def b():" in out and "NOT_IMPLEMENTED:a" not in out


def test_replace_with_decorator_and_fence_and_indent():
    out = L._replace_function(SRC, "b", "```python\n    def b():\n        return 2\n```")
    assert "@staticmethod" not in out and "return 2" in out


def test_append_new_helper():
    out = L._replace_function(SRC, "helper", "def helper():\n    return 5")
    assert out.rstrip().endswith("return 5") and "def a(x):" in out


@pytest.mark.parametrize("code,err", [("def a(:\n  pass", "FN_SYNTAX"), ("def c():\n    pass", "FN_NAME_MISMATCH"),
                                      ("x = 1\ndef a():\n    pass", "FN_NOT_SINGLE_DEF"), ("", "FN_EMPTY")])
def test_invalid_code_rejected(code, err):
    with pytest.raises(ValueError, match=err):
        L._replace_function(SRC, "a", code)


def test_add_imports_dedup_and_invalid():
    out = L._add_imports(SRC, ["import os", "import json", "from pathlib import Path"])
    assert out.count("import os") == 1 and out.index("import json") > out.index("import os") and "from pathlib import Path" in out
    with pytest.raises(ValueError, match="FN_IMPORT_INVALID"):
        L._add_imports(SRC, ["os.remove('x')"])


def test_compute_edits_function_mode(monkeypatch):
    target = L.ROOT / "workspace" / "tmp_fn" / "fn_target.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SRC, encoding="utf-8")
    monkeypatch.setattr(L, "check_write", lambda p: p)
    rel = str(target.relative_to(L.ROOT))
    res = L._compute_edits([{"file": rel, "funktion": "a", "code": "def a(x):\n    return json.dumps(x)", "imports": ["import json"]},
                            {"file": rel, "function": "neu", "code": "def neu():\n    return a([1])"}])
    out = res[target]
    ns = {}
    exec(compile(out, "m", "exec"), ns)
    assert ns["neu"]() == "[1]"
    assert target.read_text(encoding="utf-8") == SRC  # nichts geschrieben vor Uebernahme
    with pytest.raises(ValueError, match="FN_SYNTAX"):
        L._compute_edits([{"file": rel, "function": "a", "code": "def a(x) return"}])
    target.unlink()


def test_search_replace_still_works(monkeypatch):
    target = L.ROOT / "workspace" / "tmp_fn" / "sr_target.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SRC, encoding="utf-8")
    monkeypatch.setattr(L, "check_write", lambda p: p)
    rel = str(target.relative_to(L.ROOT))
    out = L._compute_edits([{"file": rel, "find": "    return 1", "replace": "    return 3"}])[target]
    assert "return 3" in out
    target.unlink()


def test_plan_function_prompt_and_shortform():
    item = {"id": "ST09", "title": "t", "file": "nexus/studio/imgtools.py", "anchor": "def tool(",
            "acceptance": "tests/accept_studio_tools.py"}
    assert L.fn_mode_applicable(item)
    assert not L.fn_mode_applicable({**item, "auto_key": "x"})
    seen = {}

    def ask(p):
        seen["p"] = p
        return json.dumps({"code": "def tool(img, name, **p):\n    return img.copy()"})
    plan = L.plan_function(item, ask=ask)
    assert plan["fn_mode"] and plan["result"]["edits"][0]["function"] == "tool"
    assert "def tool(" in seen["p"] and "KOMPLETT" in seen["p"]  # unabhaengig vom Stub-Stand


def test_parse_code_block_multiple_defs_and_imports():
    reply = "Hier:\n```python\nimport cv2\nfrom PIL import ImageOps\n\ndef tool(img, name, **p):\n    \"\"\"Doc mit \"Quotes\".\"\"\"\n    return _h(img)\n\n\ndef _h(img):\n    return img.copy()\n```\nBegruendung: neu gebaut"
    r = L.parse_code_blocks(reply, "nexus/x.py", "tool")
    assert [e["function"] for e in r["edits"]] == ["tool", "_h"] and r["format"] == "codeblock"
    assert r["edits"][0]["imports"] == ["import cv2", "from PIL import ImageOps"] and r["begruendung"] == "neu gebaut"
    out = L._apply_fn_edit(SRC, dict(r["edits"][1]), "x")
    assert "def _h(img):" in out


def test_parse_code_block_json_fallback_and_errors():
    r = L.parse_code_blocks('{"code": "def a(x):\\n    return 1"}', "f.py", "a")
    assert r["format"] == "json" and r["edits"][0]["function"] == "a" and r["edits"][0]["file"] == "f.py"
    with pytest.raises(ValueError, match="FN_SYNTAX"):
        L.parse_code_blocks("```python\ndef a(:\n```", "f.py", "a")
    with pytest.raises(ValueError, match="NO_CODE_BLOCK"):
        L.parse_code_blocks("keine Ahnung", "f.py", "a")


def test_plan_function_codeblock_retry():
    item = {"id": "ST09", "title": "t", "file": "nexus/studio/imgtools.py", "anchor": "def tool("}
    replies = iter(["kaputt", "```python\ndef tool(img, name, **p):\n    return img.copy()\n```"])
    prompts = []

    def ask(p):
        prompts.append(p)
        return next(replies)
    plan = L.plan_function(item, ask=ask)
    assert plan["result"]["format"] == "codeblock" and len(prompts) == 2 and "```python" in prompts[0]


def test_open_studio_items_have_function_anchors():
    import json as _j
    raw = _j.loads((L.ROOT / "workspace/autobuild/backlog.json").read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw["items"]
    for it in items:
        if str(it.get("id", "")).startswith("ST") and it.get("status") in ("offen", "fehlgeschlagen"):
            assert L.fn_mode_applicable(it), it["id"]
