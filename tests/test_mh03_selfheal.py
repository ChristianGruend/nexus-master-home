from nexus.autobuild import loop


def test_record_issue_creates_and_dedupes():
    items = []
    a = loop.record_issue(items, "server.log", "ERR_X boom", "Traceback ... ERR_X")
    b = loop.record_issue(items, "server.log", "ERR_X boom", "Traceback ... ERR_X")
    assert a is not None and str(a["id"]).startswith("SB-AUTO-")
    assert len([i for i in items if str(i.get("id", "")).startswith("SB-AUTO-")]) == 1
