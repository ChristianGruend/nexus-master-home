import tempfile, pathlib, unittest
from unittest.mock import patch, MagicMock
from nexus.chat import engine as eng

def _mk():
    e = eng.NexusEngine.__new__(eng.NexusEngine)
    import threading
    e._chat_lock = threading.RLock(); e._save_lock = threading.RLock(); e.version = "test"
    e.sessions = e._load_sessions(); e.session_id = "CHAT03-T"
    e.sessions.setdefault("CHAT03-T", e._empty_session("CHAT03-T"))
    e.master = MagicMock(); e._route_order = lambda *a: None
    return e

class Chat03History(unittest.TestCase):
    def test_history_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            with patch.object(eng, "HISTORY_FILE", d / "h.json"), patch.object(eng, "ACTIVE_FILE", d / "a.json"), \
                 patch.object(eng.ai_layer, "apply_to_client"), patch.object(eng.ai_layer, "resolve_chat_model", return_value=None):
                e1 = _mk()
                e1.master.handle_stream = lambda *a: iter([{"type": "done", "intent": "chat", "success": True, "reply": "Hallo Testo."}])
                list(e1.chat_stream("Ich heisse Testo", session_id="CHAT03-T"))
                e2 = _mk()  # "Neustart": neuer Prozesszustand, nur Datei
                seen = {}
                def hs(msg, hist):
                    seen["h"] = hist
                    return iter([{"type": "done", "intent": "chat", "success": True, "reply": "Du heisst Testo."}])
                e2.master.handle_stream = hs
                ev = list(e2.chat_stream("Wie heisse ich?", session_id="CHAT03-T"))
                self.assertIn("Testo", " ".join(m["content"] for m in seen["h"]))
                self.assertIn("Testo", ev[-1]["reply"])
