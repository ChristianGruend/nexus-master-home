import io, json, time, threading, unittest, tempfile, pathlib
from unittest.mock import patch, MagicMock
from nexus.common import ollama_client as oc
from nexus.chat import engine as eng

class SlowResp:
    def __init__(self): self.closed_at = None; self.lines = iter([json.dumps({"message": {"content": "t%d " % i}, "done": False}).encode() + b"\n" for i in range(200)])
    def __enter__(self): return self
    def __exit__(self, *a): self.closed_at = time.time()
    def __iter__(self): return self
    def __next__(self): time.sleep(0.05); return next(self.lines)
    def readline(self): return next(self)
    def close(self): self.closed_at = self.closed_at or time.time()

class Chat04Abort(unittest.TestCase):
    def test_close_ends_ollama_request_fast(self):
        r = SlowResp()
        c = oc.OllamaClient() if hasattr(oc, "OllamaClient") else None
        if c is None: self.skipTest("kein OllamaClient")
        with patch("urllib.request.urlopen", return_value=r), patch.object(oc, "urlopen", return_value=r, create=True):
            g = c.chat_stream([{"role": "user", "content": "x"}], model="m")
            first = next(g)
            t0 = time.time(); g.close()
        self.assertIsNotNone(r.closed_at); self.assertLess(r.closed_at - t0, 2.0)

    def test_regenerate_replaces_last_turn(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            with patch.object(eng, "HISTORY_FILE", d / "h.json"), patch.object(eng, "ACTIVE_FILE", d / "a.json"), \
                 patch.object(eng.ai_layer, "apply_to_client"), patch.object(eng.ai_layer, "resolve_chat_model", return_value=None):
                e = eng.NexusEngine.__new__(eng.NexusEngine); e._chat_lock = threading.RLock(); e._save_lock = threading.RLock(); e.version = "test"
                e.sessions = {"S": e._empty_session("S")}; e.session_id = "S"; e.master = MagicMock(); e._route_order = lambda *a: None
                n = iter(["Antwort A", "Antwort B", "Antwort C"])
                e.master.handle_stream = lambda *a: iter([{"type": "done", "intent": "chat", "success": True, "reply": next(n)}])
                list(e.chat_stream("Frage 1", session_id="S"))
                list(e.chat_stream("", session_id="S", rewrite="regenerate"))
                m = e.sessions["S"]["messages"]
                self.assertEqual([x["content"] for x in m], ["Frage 1", "Antwort B"])
                list(e.chat_stream("Frage 1b", session_id="S", rewrite="edit"))
                self.assertEqual([x["content"] for x in e.sessions["S"]["messages"]], ["Frage 1b", "Antwort C"])

    def test_ui_has_stop_regen_edit(self):
        js = (pathlib.Path(__file__).resolve().parents[1] / "nexus/chat/static/js/app.js").read_text(encoding="utf-8")
        for k in ("btnStopGen", "Neu generieren", "Bearbeiten", "body[nxRewrite] = true"): self.assertIn(k, js)
