/* NXMD - sicherer Markdown-Renderer fuer NEXUS-Chats (2026-10-28 CHAT02). HTML wird immer escaped. */
(function (root) {
  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function inline(s) {
    s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return s;
  }
  function render(text) {
    var src = String(text == null ? "" : text).replace(/\r\n/g, "\n");
    var blocks = [];
    // geschlossene und (waehrend Streaming) offene Code-Bloecke
    src = src.replace(/```([\w+#.-]*)[ \t]*\n?([\s\S]*?)(```|$)/g, function (m, lang, code) {
      blocks.push('<div class="nx-code"><div class="nx-code-bar"><span>' + esc(lang || "code") + '</span><button type="button" class="nx-code-copy">Kopieren</button></div><pre><code' + (lang ? ' class="lang-' + esc(lang) + '"' : "") + ">" + esc(code.replace(/\n$/, "")) + "</code></pre></div>");
      return "\u0000" + (blocks.length - 1) + "\u0000";
    });
    var lines = esc(src).split("\n"), out = [], list = null;
    function closeList() { if (list) { out.push("</" + list + ">"); list = null; } }
    for (var i = 0; i < lines.length; i++) {
      var l = lines[i], m;
      if (/^\u0000\d+\u0000$/.test(l.trim())) { closeList(); out.push(l.trim()); continue; }
      if ((m = /^(#{1,4})\s+(.*)$/.exec(l))) { closeList(); var n = Math.min(6, m[1].length + 2); out.push("<h" + n + ">" + inline(m[2]) + "</h" + n + ">"); continue; }
      if ((m = /^\s*[-*\u2022]\s+(.*)$/.exec(l))) { if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; } out.push("<li>" + inline(m[1]) + "</li>"); continue; }
      if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(l))) { if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; } out.push("<li>" + inline(m[1]) + "</li>"); continue; }
      closeList();
      out.push(l.trim() === "" ? "<br>" : inline(l) + "<br>");
    }
    closeList();
    var html = out.join("").replace(/(<br>)+$/, "");
    return html.replace(/\u0000(\d+)\u0000(<br>)?/g, function (m, k) { return blocks[+k]; });
  }
  function bindCopy(doc) {
    if (!doc || doc.__nxmdCopy) return; doc.__nxmdCopy = true;
    doc.addEventListener("click", function (e) {
      var b = e.target && e.target.closest && e.target.closest(".nx-code-copy"); if (!b) return;
      e.preventDefault(); e.stopPropagation();
      var c = b.closest(".nx-code").querySelector("code"); var t = c ? c.textContent : "";
      var done = function (ok) { b.textContent = ok ? "Kopiert" : "Fehler"; setTimeout(function () { b.textContent = "Kopieren"; }, 1200); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(function () { done(true); }, function () { done(false); });
      else { try { var ta = doc.createElement("textarea"); ta.value = t; doc.body.appendChild(ta); ta.select(); doc.execCommand("copy"); ta.remove(); done(true); } catch (_) { done(false); } }
    }, true);
  }
  var api = { render: render, escape: esc, bindCopy: bindCopy };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else { root.NXMD = api; if (root.document) bindCopy(root.document); }
})(typeof window !== "undefined" ? window : this);
