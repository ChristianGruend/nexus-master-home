from nexus.autobuild.loop import acceptance_check
def test_http_ok_and_missing():
    g=lambda u:(200,"<b>nxDarkCss btnTheme</b>")
    assert acceptance_check({"accept":[{"http":"x","contains":["nxDarkCss"]}]},g)["ok"]
    r=acceptance_check({"accept":[{"http":"x","contains":["zocker"]}]},g); assert not r["ok"] and "missing" in r["error"]
def test_http_status():
    assert not acceptance_check({"accept":[{"http":"x"}]},lambda u:(500,""))["ok"]
def test_static_html(tmp_path, monkeypatch):
    import nexus.autobuild.loop as L
    monkeypatch.setattr(L,"ROOT",tmp_path)
    (tmp_path/"a.html").write_text("<div><script>var a=1;</script></div>",encoding="utf-8")
    (tmp_path/"b.html").write_text("<div><div></div>",encoding="utf-8")
    (tmp_path/"c.html").write_text("<div><script>var a=;</script></div>",encoding="utf-8")
    assert acceptance_check({"file":"a.html"})["ok"]
    assert "TAG_UNBALANCED:div" in acceptance_check({"file":"b.html"})["error"]
    import shutil
    if shutil.which("node"): assert "JS_SYNTAX" in acceptance_check({"file":"c.html"})["error"]
