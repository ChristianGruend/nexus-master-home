import json, pathlib, subprocess, shutil, unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
JS = ROOT / "nexus" / "chat" / "static" / "js" / "nx_md.js"

@unittest.skipIf(not shutil.which("node"), "node fehlt")
class Chat02Markdown(unittest.TestCase):
    def render(self, text):
        code = "const m=require(process.argv[1]);process.stdout.write(m.render(process.argv[2]))"
        return subprocess.run(["node", "-e", code, str(JS), text], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    def test_code_block_to_pre(self):
        h = self.render("Hier:\n```python\nprint('<hi>')\n```\nfertig")
        self.assertIn("<pre><code class=\"lang-python\">", h)
        self.assertIn("print(&#39;&lt;hi&gt;&#39;)", h)
        self.assertIn("nx-code-copy", h)
    def test_script_escaped(self):
        h = self.render("<script>alert(1)</script> **fett** `x<y`")
        self.assertNotIn("<script>", h); self.assertIn("&lt;script&gt;", h)
        self.assertIn("<strong>fett</strong>", h); self.assertIn("<code>x&lt;y</code>", h)
    def test_lists_and_open_block_while_streaming(self):
        h = self.render("- a\n- b\n1. c\n```js\nlet x")
        self.assertIn("<ul><li>a</li><li>b</li></ul>", h); self.assertIn("<ol><li>c</li></ol>", h)
        self.assertIn("<pre><code class=\"lang-js\">let x</code></pre>", h)
    def test_pc_chat_uses_renderer(self):
        idx = (ROOT / "nexus/chat/static/index.html").read_text(encoding="utf-8")
        self.assertLess(idx.index("nx_md.js"), idx.index("/static/js/app.js"))
        self.assertIn("window.NXMD.render", (ROOT / "nexus/chat/static/js/app.js").read_text(encoding="utf-8"))
