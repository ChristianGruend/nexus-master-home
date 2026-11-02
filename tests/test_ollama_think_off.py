import json, unittest
from unittest import mock
from nexus.common import ollama_client as oc


class ThinkOff(unittest.TestCase):
    def test_chat_payload_disables_think(self):
        cap = {}
        def fake(self, method, path, payload=None, *a, **k):
            cap.update(payload or {}); return True, {"message": {"content": "ok"}}
        c = oc.OllamaClient.__new__(oc.OllamaClient)
        c.keep_alive = 0
        with mock.patch.object(oc.OllamaClient, "_req", fake):
            r = c._chat_once([{"role": "user", "content": "hi"}], model="m", temperature=0.1)
        self.assertIs(cap.get("think"), False)
        self.assertEqual(r["reply"], "ok")

    def test_env_can_enable(self):
        with mock.patch.dict("os.environ", {"NEXUS_OLLAMA_THINK": "1"}):
            self.assertTrue(oc._think())


if __name__ == "__main__":
    unittest.main()