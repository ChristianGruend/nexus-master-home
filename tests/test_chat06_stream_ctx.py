import unittest
from unittest.mock import patch, MagicMock
from nexus.common import ai_layer
from nexus.common import ollama_client as oc
class T(unittest.TestCase):
    def test_unified_ctx(self):
        self.assertEqual(ai_layer.unified_num_ctx({"num_ctx": 32768}), 32768)
        self.assertEqual(ai_layer.unified_num_ctx({}), 8192)
        from nexus.autobuild import loop
        self.assertEqual(loop.GPT_CTX, ai_layer.unified_num_ctx())
    def test_plain_tokens_stream(self):
        from nexus.chat.engine import NexusEngine
        e = NexusEngine.__new__(NexusEngine)
        e.master = MagicMock(); e.master.handle_stream = lambda *a: iter([{"type":"token","text":"Rot, "},{"type":"token","text":"Blau"},{"type":"done","intent":"chat","success":True,"reply":"Rot, Blau"}])
        e._prepare_session = lambda *a: {"messages":[{"role":"user","content":"x"}], "id":"s"}
        e._route_order = lambda *a: None
        e._finalize = lambda s, r, p: {"reply": r, "success": True}
        with patch.object(ai_layer, "apply_to_client"), patch.object(ai_layer, "resolve_chat_model", return_value=None):
            ev = list(e._chat_stream_impl("x"))
        self.assertEqual([x["type"] for x in ev], ["token","token","done"])

class TRag(unittest.TestCase):
    def test_no_embed_when_chat_model_loaded(self):
        import io, json as _j
        from nexus.memory import store
        resp = MagicMock(); resp.__enter__ = lambda s: s; resp.__exit__ = lambda *a: None
        resp.read = lambda *a: _j.dumps({"models": [{"name": "qwen3.5:9b"}]}).encode()
        with patch("urllib.request.urlopen", return_value=resp), patch.object(store, "_embed", side_effect=AssertionError("embed")):
            self.assertIsNone(store._semantic_scores("x", [{"text": "a"}]))

class TPrompt(unittest.TestCase):
    def test_compact_prompt_small_and_stable(self):
        from nexus.master.controller import MasterController
        m = MasterController()
        a = m.build_context([{"role": "user", "content": "Nenne drei Farben."}])[0]["content"]
        b = m.build_context([{"role": "user", "content": "Was ist ein Apfel?"}])[0]["content"]
        self.assertLess(len(a), 6000, len(a))
        self.assertIn("Fake-Erfolge", a); self.assertIn("Deutsch", a)
        self.assertEqual(a[:2500], b[:2500])
        full = m.build_context([{"role": "user", "content": "Bau mir ein Skript fuer Backups"}])[0]["content"]
        self.assertGreater(len(full), len(a) + 5000)
