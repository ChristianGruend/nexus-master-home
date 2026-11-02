"""NEXUS-Terminal DOM 20261031: Panel im Chatfenster, Live-Hub unveraendert, kein Ueberlappen mit Composer."""
import os, re, json, html, subprocess, urllib.request, tempfile, shutil
import pytest
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROF = r"G:\meine-ki\nexus-ai\workspace\tmp\edgeprof_term"
BASE = "http://127.0.0.1:8299/"

def _get(u):
    try:
        return urllib.request.urlopen(u, timeout=8).read().decode("utf-8", "replace")
    except Exception:
        return None

pytestmark = pytest.mark.skipif(not (os.path.exists(EDGE) and _get(BASE + "api/terminal/sessions")), reason="Edge/Server mit Terminal nicht verfuegbar")

def _dom(w, h, tries=3):
    os.makedirs(PROF, exist_ok=True)
    last = None
    for i in range(tries):
        prof = tempfile.mkdtemp(prefix="p_", dir=PROF)
        a = [EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions", "--user-data-dir=" + prof,
             f"--window-size={w},{h}", f"--timeout={25000 + 10000 * i}", "--dump-dom", BASE + "?term=1&termcheck=1"]
        try:
            r = subprocess.run(a, timeout=180, capture_output=True, text=True, encoding="utf-8", errors="replace")
            m = re.search(r'<pre id="termcheck"[^>]*>(.*?)</pre>', r.stdout or "", re.S)
            if m:
                d = json.loads(html.unescape(m.group(1)))
                # 20261102 Nexus-UI: vor master_glass-Init gemessen (Hub noch nicht in der Szene) -> neu messen
                if d.get("hub") and d.get("hub_parent") != "nxReferenceScene" and i < tries - 1:
                    last = "hub noch nicht in nxReferenceScene"
                    continue
                return d
            last = "kein termcheck rc=%s" % r.returncode
        except subprocess.TimeoutExpired:
            last = "edge timeout"
        finally:
            shutil.rmtree(prof, ignore_errors=True)
    pytest.fail(last)

@pytest.mark.parametrize("w,h", [(1920, 1080), (1280, 800)])
def test_terminal_panel(w, h):
    d = _dom(w, h)
    assert d["open"] and d["has_input"] and d["has_pause"]
    assert d["tabs"] == ["all", "NEXUS", "Hermes", "NEXUS-GPT", "Autobuild", "Eddie"]
    assert d["out"]["h"] >= 60 and "Consolas" in d["font"]
    assert d["overlap_composer"] is False
    assert d["term"]["b"] <= d["composer"]["t"] + 2
    if d["hub"]:  # 20261102 Stabschef/Eddie: Hub bei jeder Breite rechts
        assert d["hub"]["l"] >= d["term"]["r"] - 2, "Live-Hub muss rechts neben dem Terminal stehen"
