/* NEXUS Phone /m2 – Viki + Crystal OS + Smart View. Nur lokale API + Open-Meteo/OSM (kostenlos, ohne Key). */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const toast = (t) => { const e = $("#toast"); e.textContent = t; e.classList.add("on"); clearTimeout(toast._t); toast._t = setTimeout(() => e.classList.remove("on"), 2600); };
  const api = async (p, o) => { const r = await fetch(p, Object.assign({ cache: "no-store", headers: { "Content-Type": "application/json" } }, o || {})); let j = {}; try { j = await r.json(); } catch (_) {} j._status = r.status; return j; };

  /* ---------- Navigation ---------- */
  const loaders = {};
  let go = function (id) {
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === id));
    window.scrollTo(0, 0);
    if (loaders[id]) loaders[id]();
    if (location.hash !== "#" + id) history.replaceState(null, "", id === "home" ? location.pathname : "#" + id);
  };
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-go],[data-agent],.back");
    if (!b) return;
    if (b.classList.contains("back")) return go("home");
    if (b.dataset.agent) { setTarget(b.dataset.agent); return go("chat"); }
    go(b.dataset.go);
  });

  /* ---------- Uhr ---------- */
  function tick() { const d = new Date(); $("#clock").textContent = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }); $("#date").textContent = d.toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "short", year: "numeric" }); }
  tick(); setInterval(tick, 15000);

  /* ---------- Health ---------- */
  async function health() {
    const el = $("#health");
    const set = (t, cls) => { el.textContent = t; el.className = "dot " + cls; };
    if (!navigator.onLine) { set("● offline (kein Netz)", "bad"); return null; }
    const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
    const to = setTimeout(() => ctl && ctl.abort(), 5000);
    try {
      const r = await fetch("/api/health", { cache: "no-store", signal: ctl ? ctl.signal : undefined });
      const h = await r.json().catch(() => ({}));
      if (!r.ok || h.offline || h.nexus_offline || r.headers.get("X-Nexus-Offline")) { set("● offline – NEXUS nicht erreichbar", "bad"); return null; }
      set(h.ok ? "● online · " + ((h.ollama && h.ollama.model) || "lokal") : "● gestört (Score " + (h.score == null ? "?" : h.score) + ")", h.ok ? "ok" : "bad");
      return h;
    } catch (_) { set("● offline – NEXUS nicht erreichbar", "bad"); return null; }
    finally { clearTimeout(to); }
  }
  health(); setInterval(health, 30000);

  /* ---------- Standort + Wetter (Open-Meteo, ohne Key) ---------- */
  const WMO = { 0: ["☀", "Klar"], 1: ["🌤", "Heiter"], 2: ["⛅", "Wolkig"], 3: ["☁", "Bedeckt"], 45: ["🌫", "Nebel"], 48: ["🌫", "Nebel"], 51: ["🌦", "Niesel"], 61: ["🌧", "Regen"], 63: ["🌧", "Regen"], 65: ["🌧", "Starkregen"], 71: ["🌨", "Schnee"], 80: ["🌦", "Schauer"], 95: ["⛈", "Gewitter"] };
  let pos = null;
  try { pos = JSON.parse(localStorage.getItem("m2pos") || "null"); } catch (_) {}
  async function weather() {
    const p = pos || { lat: 52.52, lon: 13.40, name: "Berlin (Standard)" };
    try {
      const r = await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${p.lat}&longitude=${p.lon}&current=temperature_2m,weather_code&timezone=auto`);
      const j = await r.json(); const c = j.current; const w = WMO[c.weather_code] || WMO[Math.floor(c.weather_code / 10) * 10] || ["🌡", "–"];
      $("#wxIcon").textContent = w[0]; $("#wxTemp").textContent = Math.round(c.temperature_2m) + "°";
      $("#wWxTxt").textContent = `${Math.round(c.temperature_2m)} °C · ${w[1]} · ${p.name}`;
    } catch (_) { $("#wWxTxt").textContent = "Wetter nicht erreichbar (offline?)"; }
    $("#wLoc").textContent = p.name;
  }
  function locate() {
    if (!navigator.geolocation) return toast("Standort nicht verfügbar");
    navigator.geolocation.getCurrentPosition((g) => { pos = { lat: +g.coords.latitude.toFixed(3), lon: +g.coords.longitude.toFixed(3), name: "Mein Standort" }; localStorage.setItem("m2pos", JSON.stringify(pos)); weather(); }, () => toast("Standort: Freigabe fehlt (HTTPS nötig)"), { timeout: 8000, maximumAge: 600000 });
  }
  $("#wWx").addEventListener("click", locate); $("#wx").addEventListener("click", locate);
  weather(); setInterval(weather, 30 * 60000);

  /* ---------- Notizen / Kalender (lokale API) ---------- */
  async function notesWidget() { try { const j = await api("/api/notes"); const n = (j.notes || [])[0]; $("#wNotes").textContent = j.count ? `${j.count} · ${n.title || n.text || ""}` : "Keine Notizen"; } catch (_) { $("#wNotes").textContent = "–"; } }
  async function calWidget() { try { const j = await api("/api/calendar?from=" + encodeURIComponent(new Date().toISOString())); const e = (j.events || [])[0]; $("#wCal").textContent = e ? `${new Date(e.start).toLocaleString("de-DE", { weekday: "short", hour: "2-digit", minute: "2-digit" })} ${e.title || ""}` : "Keine Termine"; } catch (_) { $("#wCal").textContent = "–"; } }
  notesWidget(); calWidget();
  loaders.notes = async () => { const j = await api("/api/notes"); $("#noteList").innerHTML = (j.notes || []).map((n) => `<li>${esc(n.title || "")}${n.title ? "<br>" : ""}${esc(n.text || "")}<small>${esc(n.updated || n.created || "")}</small></li>`).join("") || "<li>Keine Notizen</li>"; };
  $("#noteForm").addEventListener("submit", async (e) => { e.preventDefault(); const t = $("#noteIn").value.trim(); if (!t) return; const r = await api("/api/notes", { method: "POST", body: JSON.stringify({ text: t }) }); toast(r.success ? "Notiz gespeichert" : "Fehler: " + (r.error || r._status)); $("#noteIn").value = ""; loaders.notes(); notesWidget(); });
  loaders.calendar = async () => { const j = await api("/api/calendar?from=" + encodeURIComponent(new Date(Date.now() - 864e5).toISOString())); $("#calList").innerHTML = (j.events || []).map((e) => `<li>${esc(e.title || "Termin")}<small>${esc(new Date(e.start).toLocaleString("de-DE"))}</small></li>`).join("") || "<li>Keine Termine</li>"; };

  /* ---------- Galerie / Media / Karte / System ---------- */
  loaders.gallery = async () => { const j = await api("/api/gallery"); $("#galGrid").innerHTML = (j.items || []).filter((i) => i.kind === "image").map((i) => `<a href="${esc(i.url)}" target="_blank"><img loading="lazy" src="${esc(i.url)}" alt="${esc(i.name)}"></a>`).join("") || "<p>Noch keine Bilder.</p>"; };
  loaders.media = async () => { const j = await api("/api/media"); const items = (j.state && j.state.items) || []; $("#mediaList").innerHTML = items.map((m, i) => `<li data-i="${i}" data-p="${esc(m.path)}">${m.kind === "video" ? "🎬" : "🎵"} ${esc(m.name)}</li>`).join("") || "<li>Keine Medien</li>"; };
  $("#mediaList").addEventListener("click", (e) => { const li = e.target.closest("li[data-p]"); if (!li) return; const pl = $("#player"); pl.src = "/media/" + li.dataset.p.split("/").map(encodeURIComponent).join("/"); pl.play().catch(() => toast("Wiedergabe blockiert – nochmal tippen")); });
  function showMap(lat, lon) { const d = 0.02; $("#mapFrame").src = `https://www.openstreetmap.org/export/embed.html?bbox=${lon - d},${lat - d},${lon + d},${lat + d}&layer=mapnik&marker=${lat},${lon}`; }
  loaders.map = () => { const p = pos || { lat: 52.52, lon: 13.40 }; if (!$("#mapFrame").src) showMap(p.lat, p.lon); };
  $("#mapForm").addEventListener("submit", async (e) => { e.preventDefault(); const q = $("#mapQ").value.trim(); if (!q) return; try { const r = await fetch("https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + encodeURIComponent(q)); const j = await r.json(); if (!j[0]) return toast("Nicht gefunden"); showMap(+j[0].lat, +j[0].lon); } catch (_) { toast("Suche offline"); } });
  loaders.system = async () => { const h = await health(); let s = ""; if (h) { s += `Status: ${h.ok ? "OK" : "gestört"} · Score ${h.score}\nModell: ${h.ollama && h.ollama.model}\nkeep_alive: ${h.ai && h.ai.keep_alive}\nOllama: ${h.ollama && h.ollama.reachable ? "erreichbar" : "nicht erreichbar"}\n\nModule:\n` + Object.entries(h.modules || {}).map(([k, v]) => `${v ? "✔" : "✖"} ${k}`).join("\n"); } $("#sysOut").textContent = s || "keine Daten"; };

  /* ---------- Chat (NEXUS Stream / HERMES / AETHER) ---------- */
  let target = "nexus";
  const NAMES = { nexus: "NEXUS", hermes: "HERMES", aether: "AETHER" };
  function setTarget(t) { target = NAMES[t] ? t : "nexus"; $("#chatTarget").value = target; $("#chatTitle").textContent = NAMES[target] + (target === "nexus" ? " · Chat" : " · Agent"); }
  $("#chatTarget").addEventListener("change", (e) => setTarget(e.target.value));
  function bubble(cls, text, who) { const d = document.createElement("div"); d.className = "msg " + cls; d.innerHTML = (who ? `<small>${esc(who)}</small>` : "") + "<span></span>"; d.lastChild.textContent = text; $("#msgs").appendChild(d); d.scrollIntoView({ block: "end" }); return d.lastChild; }
  let busy = false;
  async function send(text) {
    text = (text || "").trim(); if (!text || busy) return; busy = true;
    bubble("u", text); const out = bubble("a", "…", NAMES[target]);
    try {
      if (target === "nexus") {
        const res = await fetch("/api/chat/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text, source: "m2" }) });
        if (!res.ok || !res.body) throw new Error("stream " + res.status);
        const rd = res.body.getReader(), dec = new TextDecoder(); let buf = "", full = "";
        for (;;) { const { value, done } = await rd.read(); if (done) break; buf += dec.decode(value, { stream: true }); const parts = buf.split("\n\n"); buf = parts.pop() || "";
          for (const blk of parts) for (const ln of blk.split("\n")) { if (!ln.startsWith("data:")) continue; const raw = ln.slice(5).trim(); if (!raw || raw === "[DONE]") continue; try { const ev = JSON.parse(raw); if (ev.type === "token") { full += ev.text || ""; out.textContent = full; } else if (ev.type === "done" && !full) { full = ev.reply || ev.text || ""; out.textContent = full; } else if (ev.type === "error") { out.textContent = "Fehler: " + (ev.error || ""); } } catch (_) {} } out.parentNode.scrollIntoView({ block: "end" }); }
        if (!full) { const j = await api("/api/chat", { method: "POST", body: JSON.stringify({ message: text, source: "m2" }) }); out.textContent = j.reply || j.error || "(keine Antwort)"; }
      } else {
        out.textContent = NAMES[target] + " arbeitet …";
        const j = await api(`/api/${target}/agent`, { method: "POST", body: JSON.stringify(target === "hermes" ? { task: text } : { message: text }) });
        out.textContent = j.reply || (j.error ? "Nicht verfügbar: " + j.error : "(keine Antwort)");
      }
    } catch (e) { out.textContent = "Verbindung fehlgeschlagen: " + e.message; }
    busy = false;
  }
  $("#chatForm").addEventListener("submit", (e) => { e.preventDefault(); const v = $("#chatIn").value; $("#chatIn").value = ""; send(v); });
  $("#askBar").addEventListener("submit", (e) => { e.preventDefault(); const v = $("#askIn").value; $("#askIn").value = ""; setTarget("nexus"); go("chat"); send(v); });

  /* ---------- Sprache: WebSpeech, sonst Aufnahme -> /api/voice/stt (faster-whisper lokal) ---------- */
  let rec = null;
  async function listen(btn, onText) {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SR) { const r = new SR(); r.lang = "de-DE"; r.interimResults = false; btn.classList.add("rec"); r.onresult = (ev) => onText(ev.results[0][0].transcript); r.onerror = (ev) => toast("Mikro: " + ev.error + (location.protocol !== "https:" ? " (HTTPS :8443 nötig)" : "")); r.onend = () => btn.classList.remove("rec"); try { r.start(); } catch (_) { btn.classList.remove("rec"); } return; }
    if (!navigator.mediaDevices || !window.MediaRecorder) return toast("Mikrofon braucht HTTPS (Port 8443)");
    if (rec) { rec.stop(); return; }
    try {
      const st = await navigator.mediaDevices.getUserMedia({ audio: true }); const chunks = []; rec = new MediaRecorder(st); btn.classList.add("rec"); toast("Aufnahme … nochmal tippen zum Stoppen");
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = async () => { btn.classList.remove("rec"); st.getTracks().forEach((t) => t.stop()); rec = null; const b = new Blob(chunks, { type: chunks[0] && chunks[0].type || "audio/webm" }); const b64 = await new Promise((ok) => { const fr = new FileReader(); fr.onload = () => ok(String(fr.result).split(",")[1]); fr.readAsDataURL(b); }); const j = await api("/api/voice/stt", { method: "POST", body: JSON.stringify({ audio_b64: b64, filename: "m2.webm", language: "de" }) }); if (j.text) onText(j.text); else toast("Nichts erkannt"); };
      rec.start(); setTimeout(() => rec && rec.state === "recording" && rec.stop(), 12000);
    } catch (_) { toast("Mikrofon-Freigabe fehlt"); }
  }
  const voiceAsk = (btn) => listen(btn, (t) => { setTarget(target || "nexus"); go("chat"); send(t); });
  $("#micBtn").addEventListener("click", (e) => { setTarget("nexus"); voiceAsk(e.currentTarget); });
  $("#micBtn2").addEventListener("click", (e) => voiceAsk(e.currentTarget));
  $("#nBtn").addEventListener("click", (e) => { setTarget("nexus"); voiceAsk(e.currentTarget); });


  /* ---------- Kamera (getUserMedia, nur HTTPS) -> /api/vision (llava lokal, optional speichern) ---------- */
  let camStream = null, camFacing = "environment", camData = null;
  const secure = () => window.isSecureContext;
  const httpsUrl = () => "https://" + location.hostname + ":8443" + location.pathname + location.hash;
  function camStop() { if (camStream) { camStream.getTracks().forEach((t) => t.stop()); camStream = null; } }
  async function camStart() {
    const hint = $("#camHint");
    if (!secure() || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      hint.hidden = false; hint.innerHTML = 'Kamera braucht HTTPS. Öffne <a href="' + esc(httpsUrl()) + '">' + esc(httpsUrl()) + '</a> (einmal „Erweitert → Weiter“), dann Kamera erlauben.'; $("#camSnap").disabled = true; return;
    }
    hint.hidden = true; camStop();
    try { camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: camFacing, width: { ideal: 1280 } }, audio: false }); $("#camVideo").srcObject = camStream; $("#camSnap").disabled = false; }
    catch (e) { hint.hidden = false; hint.textContent = "Kamera nicht verfügbar: " + e.name + " (Berechtigung in Chrome-Website-Einstellungen prüfen)"; }
  }
  function camReset() { camData = null; $("#camShot").hidden = true; $("#camVideo").hidden = false; $("#camAsk").disabled = true; $("#camRetake").disabled = true; $("#camOut").hidden = true; }
  loaders.camera = () => { camReset(); camStart(); };
  $("#camFlip").addEventListener("click", () => { camFacing = camFacing === "environment" ? "user" : "environment"; camStart(); });
  $("#camSnap").addEventListener("click", () => {
    const v = $("#camVideo"); if (!v.videoWidth) return toast("Kamera noch nicht bereit");
    const c = $("#camCanvas"), sc = Math.min(1, 1024 / Math.max(v.videoWidth, v.videoHeight)); c.width = Math.round(v.videoWidth * sc); c.height = Math.round(v.videoHeight * sc);
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height); camData = c.toDataURL("image/jpeg", 0.85);
    $("#camShot").src = camData; $("#camShot").hidden = false; v.hidden = true; $("#camAsk").disabled = false; $("#camRetake").disabled = false;
  });
  $("#camRetake").addEventListener("click", camReset);
  $("#camAsk").addEventListener("click", async () => {
    if (!camData) return; const out = $("#camOut"); out.hidden = false; out.textContent = "llava schaut sich das Bild an … (lokal, kann 20–60 s dauern)"; $("#camAsk").disabled = true;
    try { const j = await api("/api/vision", { method: "POST", body: JSON.stringify({ image_b64: camData, prompt: $("#camPrompt").value || "Beschreibe das Bild auf Deutsch.", save: $("#camSave").checked }) });
      out.textContent = j.reply || j.text || j.description || j.answer || (j.error ? "Fehler: " + j.error : JSON.stringify(j).slice(0, 400));
      if ($("#camSave").checked && j._status === 200) toast("In Galerie gespeichert"); }
    catch (e) { out.textContent = "Fehler: " + e.message; }
    $("#camAsk").disabled = false;
  });

  /* ---------- PC-Steuerung (echte Endpoints /api/media/*, /api/apps/open, /api/os/*) ---------- */
  function ctlShow(j) { const o = $("#ctlOut"); o.hidden = false; o.textContent = (j.reply || j.message || (j.success ? "OK" : "Fehler: " + (j.error || j._status))) + (j.detail ? "\n" + j.detail : ""); }
  async function ctl(path, body, confirmText) { if (confirmText && !confirm(confirmText)) return; try { ctlShow(await api(path, { method: "POST", body: JSON.stringify(body || {}) })); } catch (e) { ctlShow({ error: e.message }); } }
  document.addEventListener("click", (e) => { const b = e.target.closest("[data-media]"); if (b) ctl("/api/media/" + b.dataset.media, {}); });
  loaders.control = async () => {
    try { const j = await api("/api/apps"); $("#ctlApps").innerHTML = (j.apps || []).slice(0, 24).map((a) => `<button data-app="${esc(a.id)}" data-name="${esc(a.name)}">${esc(a.name)}</button>`).join("") || "<span class='mut'>keine Apps</span>"; }
    catch (_) { $("#ctlApps").textContent = "Apps nicht ladbar"; }
  };
  $("#ctlApps").addEventListener("click", (e) => { const b = e.target.closest("[data-app]"); if (b) ctl("/api/apps/open", { id: b.dataset.app }, `„${b.dataset.name}“ am PC öffnen?`); });
  $("#ctlUrlForm").addEventListener("submit", (e) => { e.preventDefault(); let u = $("#ctlUrl").value.trim(); if (!u) return; if (!/^https?:\/\//i.test(u)) u = "https://" + u; ctl("/api/os/open_url", { url: u }, "Diese Webseite am PC öffnen?\n" + u); });
  $("#ctlAppForm").addEventListener("submit", (e) => { e.preventDefault(); const n = $("#ctlApp").value.trim(); if (n) ctl("/api/os/launch_app", { name: n }, `Programm „${n}“ am PC starten?`); });
  $("#ctlCloseForm").addEventListener("submit", (e) => { e.preventDefault(); const n = $("#ctlClose").value.trim(); if (n) ctl("/api/os/close_app", { name: n }, `ACHTUNG: „${n}“ am PC schließen? Ungespeicherte Daten können verloren gehen.`); });
  const _go = go; go = function (id) { if (id !== "camera") camStop(); return _go(id); };

  /* ---------- 3D-Erde (Canvas, leicht, pausiert im Hintergrund) ---------- */
  (function globe() {
    const c = $("#globe"); if (!c || !c.getContext) return; const g = c.getContext("2d"); const W = c.width, R = W * 0.42, cx = W / 2, cy = W / 2;
    const pts = []; for (let i = 0; i < 520; i++) { const y = 1 - (i / 519) * 2, r = Math.sqrt(1 - y * y), th = i * 2.39996; pts.push([Math.cos(th) * r, y, Math.sin(th) * r]); }
    let a = 0, last = 0; const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    function frame(t) {
      if (t - last > 40) { last = t; a += 0.006; g.clearRect(0, 0, W, W);
        const gr = g.createRadialGradient(cx - R * .3, cy - R * .3, R * .1, cx, cy, R); gr.addColorStop(0, "rgba(160,205,255,.55)"); gr.addColorStop(1, "rgba(47,140,255,.08)"); g.fillStyle = gr; g.beginPath(); g.arc(cx, cy, R, 0, 7); g.fill();
        g.strokeStyle = "rgba(47,140,255,.35)"; g.lineWidth = 1; g.beginPath(); g.arc(cx, cy, R, 0, 7); g.stroke();
        const s = Math.sin(a), co = Math.cos(a);
        for (const p of pts) { const x = p[0] * co - p[2] * s, z = p[0] * s + p[2] * co; if (z < -0.1) continue; g.fillStyle = `rgba(30,110,230,${0.25 + z * 0.6})`; g.fillRect(cx + x * R, cy + p[1] * R, 2, 2); }
      }
      if (!reduce && !document.hidden) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame); document.addEventListener("visibilitychange", () => { if (!document.hidden && !reduce) requestAnimationFrame(frame); });
  })();

  /* PWA: Service Worker (Offline-Shell, Cache nexus-m-v10-m2) */
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).then((r) => r.update()).catch(() => {});
  window.addEventListener("offline", () => { toast("Offline – NEXUS nicht erreichbar"); health(); }); window.addEventListener("online", () => { toast("Wieder online"); health(); });

  /* Start-View aus Hash */
  const h = location.hash.slice(1); if (h && document.getElementById(h)) go(h);
})();
