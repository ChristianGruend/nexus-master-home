(function () {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const input = $("#mInput");
  let busy = false;
  let live = false; // legacy short live (hidden)
  let recognition = null;
  const ttsAudio = $("#mTts");
  let deferredInstall = null;
  let streamAbort = null;

  // ——— Call mode state ———
  const call = {
    active: false,
    muted: false,
    speaking: false,
    bargeIn: false,
    showText: false,
    status: "idle", // idle|listening|thinking|building|speaking|muted
    recognition: null,
    mode: "webspeech", // webspeech | stt_chunks
    mediaStream: null,
    mediaRecorder: null,
    chunkTimer: null,
    audioUnlocked: false,
    timerId: null,
    startedAt: 0,
    speakGen: 0,
    // voicefix-20261006: Mikro-Zustandsautomat
    holds: 0,          // >0 = thinking/speaking -> Erkennung AUS
    recPaused: false,
    recRunning: false,
    recTimer: null,
    recFastEnds: 0,
    recStartedAt: 0,
    resumeAt: 0,
  };

  const BUILD_RE = /\b(baue|bauen|build|implementiere|implementier|patch(e)?|lege an|erstelle modul|entwickelt|scaffold|npm|vite)\b/i;

  function isStandalone() {
    try {
      if (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) return true;
      if (window.navigator.standalone === true) return true;
    } catch (_) {}
    return false;
  }


  let hostReachable = true;

  function setHostReachable(ok) {
    hostReachable = !!ok;
    const banner = $("#mOfflineBanner");
    const chip = $("#mNetChip");
    const online = typeof navigator !== "undefined" ? navigator.onLine !== false : true;
    if (banner) {
      // Show when browser offline OR host APIs unreachable
      banner.hidden = online && hostReachable;
    }
    if (chip) {
      if (!online) {
        chip.textContent = "Offline";
        chip.classList.add("warn");
        chip.classList.remove("ok");
      } else if (!hostReachable) {
        chip.textContent = "Host weg";
        chip.classList.add("warn");
        chip.classList.remove("ok");
      } else {
        chip.textContent = "LAN ok";
        chip.classList.add("ok");
        chip.classList.remove("warn");
      }
    }
  }

  
async function refreshMobileDash() {
  function set(id, text, unk) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("unk", !!unk);
  }
  try {
    const r = await fetch("/api/system/metrics", { cache: "no-store" });
    const m = await r.json();
    if (!m || m.success === false) {
      ["wCpu","wRam","wDisk","wLlm","wBridge","wNet"].forEach((id) => set(id, "UNKNOWN", true));
      set("wBat", "UNKNOWN", true);
      return;
    }
    const cpu = m.cpu && m.cpu.percent != null ? (m.cpu.percent + " %") : "UNKNOWN";
    set("wCpu", cpu, m.cpu == null || m.cpu.percent == null);
    const ram = m.ram && m.ram.percent != null ? (m.ram.percent + " % · " + (m.ram.used_gb ?? "?") + "/" + (m.ram.total_gb ?? "?") + " GB") : "UNKNOWN";
    set("wRam", ram, !(m.ram && m.ram.percent != null));
    const disk = m.disk && m.disk.percent != null ? (m.disk.percent + " % · frei " + (m.disk.free_gb ?? "?")) : "UNKNOWN";
    set("wDisk", disk, !(m.disk && m.disk.percent != null));
    if (m.battery && (m.battery.percent != null || m.battery.power_plugged != null)) {
      set("wBat", "percent=" + (m.battery.percent ?? "?") + " plugged=" + (m.battery.power_plugged ?? "?"), false);
    } else {
      set("wBat", "UNKNOWN", true);
    }
    const o = m.ollama || {};
    if (o.reachable) set("wLlm", (o.preferred || "ok") + " · online", false);
    else set("wLlm", o.error || "UNKNOWN", true);
    const n = m.network || {};
    const up = (n.interfaces || []).filter((i) => i.up).map((i) => i.name).slice(0, 2);
    if (n.bytes_recv_rate != null || up.length) {
      set("wNet", "↓" + Math.round(n.bytes_recv_rate || 0) + " ↑" + Math.round(n.bytes_sent_rate || 0) + (up.length ? " · " + up.join(",") : ""), false);
    } else set("wNet", "UNKNOWN", true);
  } catch (e) {
    ["wCpu","wRam","wDisk","wLlm","wNet"].forEach((id) => set(id, "UNKNOWN", true));
    set("wBat", "UNKNOWN", true);
  }
  try {
    const br = await fetch("/api/bridge/health", { cache: "no-store" });
    if (br.status === 401) set("wBridge", "UP · Auth 401", false);
    else if (br.ok) set("wBridge", "UP", false);
    else set("wBridge", "HTTP " + br.status, true);
  } catch (e) {
    set("wBridge", "UNKNOWN", true);
  }
}

function setupKeyboardGuard() {
  const vv = window.visualViewport;
  if (!vv) return;
  const apply = () => {
    const gap = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
    document.body.classList.toggle("nx-kb-open", gap > 120);
    const app = document.getElementById("mApp");
    if (app) app.style.height = Math.round(vv.height) + "px";
  };
  vv.addEventListener("resize", apply);
  vv.addEventListener("scroll", apply);
  apply();
}

function setupOffline() {
    const sync = () => setHostReachable(hostReachable);
    window.addEventListener("online", () => {
      setHostReachable(true);
      // probe health quietly
      api("/api/health").catch(() => {});
    });
    window.addEventListener("offline", () => setHostReachable(false));
    setHostReachable(navigator.onLine !== false);
  }

  function showSwVersion() {
    const el = $("#mSwVer");
    if (!el || !("serviceWorker" in navigator)) {
      if (el) el.textContent = "kein SW";
      return;
    }
    navigator.serviceWorker.ready.then((reg) => {
      const sw = reg.active || reg.waiting || reg.installing;
      if (!sw) {
        el.textContent = "SW pending";
        return;
      }
      const ch = new MessageChannel();
      ch.port1.onmessage = (ev) => {
        const d = ev.data || {};
        el.textContent = (d.cache || "?") + " / " + (d.shell || "");
      };
      try {
        sw.postMessage({ type: "GET_VERSION" }, [ch.port2]);
      } catch (_) {
        el.textContent = "SW aktiv";
      }
      // fallback timeout
      setTimeout(() => {
        if (el.textContent === "-" || el.textContent === "SW pending") el.textContent = "SW aktiv (no version reply)";
      }, 1500);
    }).catch(() => {
      el.textContent = "SW error";
    });
  }

  function registerSW() {
    if (!("serviceWorker" in navigator)) return;
    const tryRegs = ["/sw.js", "/static/sw.js"];
    (async () => {
      for (const path of tryRegs) {
        try {
          const reg = await navigator.serviceWorker.register(path, { scope: "/" });
          console.log("[NEXUS PWA] SW registered", path, reg.scope);
          if (reg.waiting) {
            try { reg.waiting.postMessage({ type: "SKIP_WAITING" }); } catch (_) {}
          }
          reg.addEventListener("updatefound", () => {
            const nw = reg.installing;
            if (!nw) return;
            nw.addEventListener("statechange", () => {
              if (nw.state === "installed" && navigator.serviceWorker.controller) {
                console.log("[NEXUS PWA] new SW installed — reload for fresh /m");
              }
            });
          });
          try { await reg.update(); } catch (_) {}
          return;
        } catch (e) {
          console.warn("[NEXUS PWA] SW register failed", path, e);
        }
      }
    })();
  }

  function setupInstall() {
    const card = $("#mInstallCard");
    const btn = $("#mInstallBtn");
    const mode = $("#mPwaMode");
    if (mode) mode.textContent = isStandalone() ? "Installiert (standalone)" : "Browser";

    if (isStandalone()) {
      document.body.classList.add("nx-standalone");
      if (card) card.hidden = true;
      return;
    }

    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault();
      deferredInstall = e;
      if (card) card.hidden = false;
    });

    window.addEventListener("appinstalled", () => {
      deferredInstall = null;
      if (card) card.hidden = true;
      if (mode) mode.textContent = "Installiert";
    });

    if (card && !isStandalone()) {
      setTimeout(() => {
        if (!deferredInstall && !isStandalone() && card) {
          card.hidden = false;
          if (btn) btn.textContent = "Zum Startbildschirm hinzufügen";
        }
      }, 2500);
    }

    if (btn) {
      btn.addEventListener("click", async () => {
        if (deferredInstall) {
          deferredInstall.prompt();
          try {
            const choice = await deferredInstall.userChoice;
            if (choice && choice.outcome === "accepted") {
              if (card) card.hidden = true;
            }
          } catch (_) {}
          deferredInstall = null;
        } else {
          alert(
            "So installieren:\n\n" +
              "Android Chrome: Menü (⋮) → „App installieren“ oder „Zum Startbildschirm hinzufügen“.\n" +
              "iOS Safari: Teilen-Taste → „Zum Home-Bildschirm“.\n\n" +
              "Hinweis: Das Install-Prompt braucht oft HTTPS oder localhost. Im LAN per HTTP (http://IP:8299) geht auf vielen Androids trotzdem „Zum Startbildschirm“."
          );
        }
      });
    }
  }

  async function api(path, opts) {
    try {
      const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
      const ct = r.headers.get("content-type") || "";
      if (ct.includes("application/json")) {
        const j = await r.json();
        if (j && j.offline) setHostReachable(false);
        else if (r.ok) setHostReachable(true);
        return j;
      }
      return { success: r.ok, status: r.status };
    } catch (e) {
      setHostReachable(false);
      return {
        success: false,
        ok: false,
        status: "offline",
        error: "offline",
        offline: true,
        message: "NEXUS lokal nicht erreichbar (Offline/LAN).",
        nexus_offline: true,
      };
    }
  }

  function autosize() {
    if (!input) return;
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
  }

  function setChip(t) {
    const c = $("#mVoiceChip");
    if (c) c.textContent = t;
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function formatLite(text) {
    let s = escapeHtml(text || "");
    s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    return s;
  }
  function setContent(el, text, md) {
    el.dataset.raw = text || "";
    if (md) el.innerHTML = formatLite(text);
    else el.textContent = text || "";
  }
  function add(role, text) {
    const row = document.createElement("div");
    row.className = "msg-row " + role;
    const av = document.createElement("div");
    av.className = "avatar";
    av.textContent = role === "user" ? "Du" : "N";
    const body = document.createElement("div");
    body.className = "msg-body";
    const who = document.createElement("div");
    who.className = "who";
    who.textContent = role === "user" ? "Du" : "NEXUS";
    const content = document.createElement("div");
    content.className = "content";
    if (role === "assistant") setContent(content, text, true);
    else setContent(content, text, false);
    body.appendChild(who);
    body.appendChild(content);
    row.appendChild(av);
    row.appendChild(body);
    $("#mMsgs").appendChild(row);
    row.scrollIntoView({ behavior: "smooth", block: "end" });
    return { row, content };
  }

  function appendCallTranscript(role, text) {
    const box = $("#mCallTranscript");
    if (!box || !text) return;
    const div = document.createElement("div");
    div.className = role === "user" ? "ct-user" : "ct-bot";
    div.textContent = (role === "user" ? "Du: " : "NEXUS: ") + String(text).slice(0, 800);
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  function setCallStatus(state) {
    call.status = state;
    const labels = {
      idle: "Bereit",
      listening: "hört zu…",
      thinking: "denkt…",
      building: "baut…",
      speaking: "spricht…",
      muted: "Stumm",
    };
    const el = $("#mCallStatus");
    if (el) {
      el.textContent = labels[state] || state;
      el.dataset.state = state;
    }
    const ov = $("#mCallOverlay");
    if (ov) ov.dataset.state = state;
    if (call.active) {
      if (state === "listening") setChip("hört");
      else if (state === "thinking") setChip("denkt");
      else if (state === "building") setChip("baut");
      else if (state === "speaking") setChip("spricht");
      else if (state === "muted") setChip("Stumm");
      else setChip("Anruf");
    }
  }

  function formatTimer(ms) {
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    const r = s % 60;
    return String(m).padStart(2, "0") + ":" + String(r).padStart(2, "0");
  }

  function startCallTimer() {
    call.startedAt = Date.now();
    const el = $("#mCallTimer");
    if (call.timerId) clearInterval(call.timerId);
    call.timerId = setInterval(() => {
      if (el) el.textContent = formatTimer(Date.now() - call.startedAt);
    }, 1000);
    if (el) el.textContent = "00:00";
  }

  function stopCallTimer() {
    if (call.timerId) {
      clearInterval(call.timerId);
      call.timerId = null;
    }
  }

  async function unlockAudio() {
    if (call.audioUnlocked || !ttsAudio) return;
    try {
      // Silent wav to unlock autoplay after user gesture
      ttsAudio.src =
        "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
      ttsAudio.volume = 0.01;
      await ttsAudio.play();
      ttsAudio.pause();
      ttsAudio.removeAttribute("src");
      ttsAudio.load();
      ttsAudio.volume = 1;
      call.audioUnlocked = true;
    } catch (_) {
      try {
        ttsAudio.volume = 1;
      } catch (e) {}
    }
  }

  function stopTts() {
    call.speakGen += 1;
    call.speaking = false;
    document.body.classList.remove("nx-speaking");
    try {
      if (window.speechSynthesis) window.speechSynthesis.cancel();
    } catch (_) {}
    if (ttsAudio) {
      try {
        ttsAudio.pause();
        ttsAudio.removeAttribute("src");
        ttsAudio.load();
      } catch (_) {}
    }
  }

  function bargeIn() {
    if (!call.speaking && !(ttsAudio && !ttsAudio.paused && ttsAudio.src)) return;
    stopTts();
    call.bargeIn = true;
    if (streamAbort) {
      try {
        streamAbort.abort();
      } catch (_) {}
    }
    if (call.active && !call.muted) setCallStatus("listening");
  }

  async function sendStream(text, opts) {
    opts = opts || {};
    const looksBuild = BUILD_RE.test(text || "");
    add("user", text);
    if (call.active) appendCallTranscript("user", text);
    const ast = add("assistant", "…");
    const { content } = ast;
    ast.row.classList.add("is-streaming", "typing");
    let full = "";
    let intent = null;
    streamAbort = typeof AbortController !== "undefined" ? new AbortController() : null;
    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
        signal: streamAbort ? streamAbort.signal : undefined,
      });
      if (!res.ok || !res.body) throw new Error("fallback");
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      setContent(content, "", false);
      if (call.active) {
        setCallStatus(looksBuild ? "building" : "thinking");
      }
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() || "";
        for (const block of parts) {
          for (const line of block.split("\n")) {
            if (!line.startsWith("data:")) continue;
            const raw = line.slice(5).trim();
            if (!raw || raw === "[DONE]") continue;
            try {
              const ev = JSON.parse(raw);
              if (ev.type === "token") {
                full += ev.text || "";
                ast.row.classList.add("is-streaming");
                setContent(content, full, false);
              } else if (ev.type === "done") {
                full = ev.reply || full;
                intent = ev.intent || intent;
                ast.row.classList.remove("is-streaming", "typing");
                setContent(content, full, true);
              }
            } catch (_) {}
          }
        }
      }
      ast.row.classList.remove("is-streaming", "typing");
      if (call.active) appendCallTranscript("assistant", full);
      return { reply: full, intent: intent };
    } catch (e) {
      if (e && e.name === "AbortError") {
        ast.row.classList.remove("is-streaming", "typing");
        if (!full) setContent(content, "(unterbrochen)", false);
        return { reply: full || "", intent: intent, aborted: true };
      }
      ast.row.classList.remove("is-streaming", "typing");
      const r = await api("/api/chat", { method: "POST", body: JSON.stringify({ message: text }) });
      const reply = r.reply || r.error || "—";
      intent = r.intent || intent;
      setContent(content, reply, true);
      if (call.active) appendCallTranscript("assistant", reply);
      return { reply: reply, intent: intent };
    } finally {
      streamAbort = null;
    }
  }

  // Edge-TTS AmalaNeural — Call/Mobile: immer nach Antwort speak(); kein Double-Speak, kein Hang
  async function speak(text) {
    if (!text) return;
    callHold(); // voicefix-20261006: Mikro aus waehrend Wiedergabe
    try { return await speakInner(text); } finally { callRelease(); }
  }

  async function speakInner(text) {
    if (!text) return;
    if (call.bargeIn) {
      call.bargeIn = false;
    }
    const clean = String(text)
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 1600);
    if (!clean) return;
    stopTts(); // bumps speakGen; cancel prior audio
    const gen = call.speakGen;
    call.speaking = true;
    if (call.active) setCallStatus("speaking");
    else setChip("Speaking");
    document.body.classList.add("nx-speaking");
    try {
      if (window.speechSynthesis) window.speechSynthesis.cancel();
    } catch (e) {}
    const ctrl = new AbortController();
    const abortTimer = setTimeout(function () { try { ctrl.abort(); } catch (e) {} }, 20000);
    try {
      const res = await fetch("/api/voice/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: clean }),
        signal: ctrl.signal,
      });
      if (gen !== call.speakGen) return;
      const ct = res.headers.get("content-type") || "";
      if (res.ok && ct.indexOf("audio") >= 0) {
        const blob = await res.blob();
        if (gen !== call.speakGen) return;
        if (blob.size > 200 && ttsAudio) {
          const url = URL.createObjectURL(blob);
          try { ttsAudio.pause(); } catch (e0) {}
          ttsAudio.src = url;
          // voicefix-20261006: Sicherheits-Timer aus echter Audiodauer (vorher Textlaenge -> zu frueh 'fertig', Mikro hoerte eigene Stimme)
          await new Promise(function (resolve) {
            let settled = false;
            let hang = null;
            let genWatch = null;
            const done = function () {
              if (settled) return;
              settled = true;
              try { clearTimeout(hang); } catch (e1) {}
              try { clearInterval(genWatch); } catch (e1b) {}
              try { ttsAudio.onloadedmetadata = null; } catch (e1c) {}
              try { URL.revokeObjectURL(url); } catch (e2) {}
              resolve();
            };
            const onHang = function () { try { ttsAudio.pause(); } catch (e0) {} done(); };
            hang = setTimeout(onHang, ttsHangMs(null, blob));
            ttsAudio.onloadedmetadata = function () {
              if (settled) return;
              try { clearTimeout(hang); } catch (e) {}
              hang = setTimeout(onHang, ttsHangMs(ttsAudio, blob));
            };
            genWatch = setInterval(function () { if (gen !== call.speakGen) done(); }, 400);
            ttsAudio.onended = done;
            ttsAudio.onerror = done;
            ttsAudio.play().catch(function () { done(); });
          });
          if (gen !== call.speakGen) return;
          call.speaking = false;
          document.body.classList.remove("nx-speaking");
          if (call.active && !call.muted) setCallStatus("listening");
          else if (live) setChip("Listening");
          else if (!call.active) setChip("Voice");
          return;
        }
      }
    } catch (e) {
    } finally {
      clearTimeout(abortTimer);
    }
    if (gen !== call.speakGen) return;
    call.speaking = false;
    document.body.classList.remove("nx-speaking");
    if (call.active && !call.muted) setCallStatus("listening");
    else if (live) setChip("Listening");
    else if (!call.active) setChip("Voice");
  }

  async function handleUtterance(text) {
    const t0 = (text || "").trim();
    if (!t0 || busy || call.muted) return;
    callHold(); // voicefix-20261006: Mikro aus bis Antwort+Wiedergabe fertig
    try {
      return await handleUtteranceInner(text);
    } finally {
      busy = false;
      callRelease(); // genau ein verzoegerter Neustart
    }
  }

  async function handleUtteranceInner(text) {
    const t = (text || "").trim();
    if (!t || busy) return;
    if (call.muted) return;
    busy = true;
    call.bargeIn = false;
    const looksBuild = BUILD_RE.test(t);
    if (call.active) setCallStatus(looksBuild ? "building" : "thinking");
    else if (live) setChip("Thinking");
    const result = await sendStream(t);
    if (result && result.aborted) {
      busy = false;
      if (call.active && !call.muted) setCallStatus("listening");
      return;
    }
    const reply = result && result.reply;
    const intent = result && result.intent;
    if (call.active && (intent === "build" || looksBuild) && reply) {
      setCallStatus("building");
    }
    if (reply && !call.bargeIn) await speak(reply);
    else if (call.active && !call.muted) setCallStatus("listening");
    else if (live) setChip("Listening");
    busy = false;
  }

  async function send() {
    if (busy || !(input && (input.value || "").trim())) return sendInner();
    callHold(); // voicefix-20261006
    try { return await sendInner(); } finally { callRelease(); }
  }

  async function sendInner() {
    if ((typeof navigator !== "undefined" && navigator.onLine === false) || hostReachable === false) {
      // shell-only: browser offline or host APIs unreachable
      try {
        const box = $("#mMsgs");
        if (box) {
          const d = document.createElement("div");
          d.className = "m-msg bot";
          d.textContent = "Offline/Host weg: UI-Shell bleibt. Chat/Memory/Dateien/System brauchen LAN zum PC.";
          box.appendChild(d);
          box.scrollTop = box.scrollHeight;
        }
      } catch (_) {}
      return;
    }

    const t = (input.value || "").trim();
    if (!t || busy) return;
    busy = true;
    input.value = "";
    autosize();
    const looksBuild = BUILD_RE.test(t);
    if (call.active) setCallStatus(looksBuild ? "building" : "thinking");
    else if (live) setChip("Thinking");
    const result = await sendStream(t);
    const reply = result && result.reply;
    // Call + Mobile: immer TTS nach Antwort (Amala)
    if (reply) await speak(reply);
    else if (call.active && !call.muted) setCallStatus("listening");
    else if (live) setChip("Listening");
    busy = false;
  }

  // ===== voicefix-20261006: Anruf-Mikro als Zustandsautomat =====
  // listening: Erkennung an | thinking/speaking: Erkennung AUS (call.holds > 0)
  // Nach Antwort+Wiedergabe genau EIN Neustart nach CALL_RESUME_DELAY_MS.
  // onend / no-speech / aborted: Neustart nur per Timer mit Backoff (kein An/Aus-Sturm).
  const CALL_RESUME_DELAY_MS = 450;

  function ttsHangMs(audio, blob) {
    // Edge-TTS MP3 = 48 kbit/s = 6 Bytes pro ms. Sicherheitsnetz = Dauer + 4 s.
    const est = blob && blob.size ? Math.round(blob.size / 6) : 0;
    const dur = audio && isFinite(audio.duration) && audio.duration > 0 ? Math.round(audio.duration * 1000) : 0;
    return Math.min(240000, Math.max(est, dur) + 4000);
  }

  function callClearRecTimer() {
    if (call.recTimer) {
      clearTimeout(call.recTimer);
      call.recTimer = null;
    }
  }

  function callRecStart() {
    callClearRecTimer();
    const rec = call.recognition;
    if (!call.active || call.muted || call.mode !== "webspeech" || !rec || call.recPaused || call.recRunning) return;
    try {
      rec.start();
      call.recRunning = true;
      call.recStartedAt = Date.now();
    } catch (_) {
      // laeuft noch -> onend plant neu
    }
  }

  function callScheduleRecStart(ms) {
    callClearRecTimer();
    if (!call.active || call.muted || call.mode !== "webspeech" || call.recPaused) return;
    call.recTimer = setTimeout(callRecStart, ms);
  }

  function callHold() {
    call.holds = (call.holds || 0) + 1;
    call.recPaused = true;
    callClearRecTimer();
    const rec = call.recognition;
    if (rec && call.recRunning) {
      try { rec.abort(); } catch (_) {}
    }
  }

  function callRelease() {
    call.holds = Math.max(0, (call.holds || 0) - 1);
    if (call.holds > 0) return;
    call.recPaused = false;
    call.resumeAt = Date.now() + CALL_RESUME_DELAY_MS;
    if (!call.active || call.muted) return;
    call.recFastEnds = 0;
    setCallStatus("listening");
    callScheduleRecStart(CALL_RESUME_DELAY_MS);
  }

  // --- Web Speech (preferred) ---
  function hasWebSpeech() {
    return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  function startWebSpeechListening() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return false;
    stopWebSpeech();
    const rec = new SR();
    rec.lang = "de-DE";
    rec.continuous = true;
    rec.interimResults = true;
    call.recognition = rec;
    recognition = rec;
    call.mode = "webspeech";
    call.recRunning = false;
    call.recFastEnds = 0;
    call.recPaused = (call.holds || 0) > 0;

    rec.onresult = async (ev) => {
      // nach abort()/waehrend thinking+speaking ignorieren (Echo der eigenen Stimme)
      if (call.recognition !== rec || !call.active || call.muted || call.recPaused) return;
      let interim = "";
      let finalText = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) finalText += t;
        else interim += t;
      }
      if (finalText.trim()) {
        if (busy) return;
        await handleUtterance(finalText.trim());
      } else if (interim && input) {
        input.value = interim;
      }
    };
    rec.onerror = (e) => {
      if (call.recognition !== rec || !call.active) return;
      const err = e && e.error;
      if (err === "not-allowed") {
        add("assistant", "Mikrofon-Zugriff verweigert - Anruf beendet.");
        hangUp();
      } else if (err === "network" || err === "service-not-allowed") {
        // Chrome Web Speech braucht Google-Netz; Fallback auf Server-STT-Chunks
        console.warn("[NEXUS call] webspeech " + err + " -> STT fallback");
        stopWebSpeech();
        startChunkStt().catch((er2) => {
          add("assistant", "Spracherkennung fehlgeschlagen: " + (er2 && er2.message ? er2.message : er2));
          hangUp();
        });
      }
      // no-speech / aborted: nichts tun - onend plant Neustart mit Backoff
    };
    rec.onend = () => {
      if (call.recognition !== rec) return;
      call.recRunning = false;
      if (!call.active || call.muted || call.mode !== "webspeech" || call.recPaused || call.recTimer) return;
      const ran = Date.now() - (call.recStartedAt || 0);
      call.recFastEnds = ran < 1500 ? Math.min((call.recFastEnds || 0) + 1, 5) : 0;
      const delay = call.recFastEnds ? Math.min(5000, 300 * Math.pow(2, call.recFastEnds)) : 250;
      callScheduleRecStart(delay);
    };
    if (call.recPaused) return true; // startet nach callRelease()
    try {
      rec.start();
      call.recRunning = true;
      call.recStartedAt = Date.now();
      return true;
    } catch (_) {
      return false;
    }
  }

  function stopWebSpeech() {
    callClearRecTimer();
    const r1 = call.recognition;
    call.recognition = null;
    call.recRunning = false;
    const kill = function (r) {
      if (!r) return;
      try {
        r.onend = null;
        r.onresult = null;
        r.onerror = null;
        r.abort();
      } catch (_) {}
    };
    kill(r1);
    if (recognition && recognition !== r1) kill(recognition);
    recognition = null;
  }

  // ——— Fallback: MediaRecorder chunks → POST /api/voice/stt ———

  function httpsMobileUrl() {
    var host = (location && location.hostname) ? location.hostname : "192.0.2.12";
    return "https://" + host + ":8443/m";
  }

  function showHttpsBannerIfNeeded() {
    var el = document.getElementById("mHttpsBanner");
    var link = document.getElementById("mHttpsLink");
    if (!el) return;
    if (window.isSecureContext) {
      el.hidden = true;
      return;
    }
    var url = httpsMobileUrl();
    if (link) {
      link.href = url;
      link.textContent = url;
    }
    el.hidden = false;
  }

  async function startChunkStt() {
    call.mode = "stt_chunks";
    if (!window.isSecureContext) {
      throw new Error("Mikrofon braucht HTTPS (sicherer Kontext). Bitte oeffne " + httpsMobileUrl() + " — einmal Erweitert/Weiter tippen, dann Mikro erlauben. (Alternativ: Chrome Flags Insecure origins, nur Fallback.)");
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("Kein getUserMedia");
    }
    try {
      call.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch (micErr) {
      var name = (micErr && micErr.name) || "";
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        throw new Error((!window.isSecureContext ? ("Zuerst HTTPS nutzen: " + httpsMobileUrl() + " — dann Mikro erlauben. ") : "") + "Mikrofon-Zugriff verweigert. In Chrome Website-Einstellungen Mikrofon auf Zulassen; bei Xiaomi auch App-Berechtigung fuer Chrome pruefen.");
      }
      if (name === "NotFoundError") {
        throw new Error("Kein Mikrofon gefunden.");
      }
      throw micErr;
    }
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : MediaRecorder.isTypeSupported("audio/webm")
        ? "audio/webm"
        : "";
    const recOpts = mime ? { mimeType: mime } : undefined;
    let chunks = [];

    function armRecorder() {
      if (!call.active || call.muted || call.mode !== "stt_chunks") return;
      const chunkStartedAt = Date.now(); // voicefix-20261006
      chunks = [];
      try {
        call.mediaRecorder = new MediaRecorder(call.mediaStream, recOpts);
      } catch (_) {
        call.mediaRecorder = new MediaRecorder(call.mediaStream);
      }
      call.mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunks.push(e.data);
      };
      call.mediaRecorder.onstop = async () => {
        if (!call.active || call.muted) {
          scheduleNextChunk();
          return;
        }
        // Skip STT while TTS playing (avoid feedback); barge-in via webspeech only
        if (call.speaking || busy || call.recPaused || chunkStartedAt < (call.resumeAt || 0)) {
          scheduleNextChunk();
          return;
        }
        const blob = new Blob(chunks, { type: (call.mediaRecorder && call.mediaRecorder.mimeType) || "audio/webm" });
        chunks = [];
        if (blob.size < 800) {
          scheduleNextChunk();
          return;
        }
        try {
          const b64 = await blobToBase64(blob);
          const r = await api("/api/voice/stt", {
            method: "POST",
            body: JSON.stringify({
              audio_b64: b64,
              filename: "chunk.webm",
              language: "de",
            }),
          });
          const text = ((r && (r.text || r.reply)) || "").trim();
          if (text && text !== "(kein Text erkannt)" && !busy) {
            await handleUtterance(text);
          }
        } catch (e) {
          console.warn("[NEXUS call] stt chunk failed", e);
        }
        scheduleNextChunk();
      };
      try {
        call.mediaRecorder.start();
      } catch (e) {
        console.warn(e);
        scheduleNextChunk();
        return;
      }
      call.chunkTimer = setTimeout(() => {
        try {
          if (call.mediaRecorder && call.mediaRecorder.state === "recording") call.mediaRecorder.stop();
        } catch (_) {}
      }, 3200);
    }

    function scheduleNextChunk() {
      if (!call.active || call.muted || call.mode !== "stt_chunks") return;
      call.chunkTimer = setTimeout(armRecorder, 180);
    }

    armRecorder();
    setCallStatus("listening");
  }

  function stopChunkStt() {
    if (call.chunkTimer) {
      clearTimeout(call.chunkTimer);
      call.chunkTimer = null;
    }
    if (call.mediaRecorder) {
      try {
        call.mediaRecorder.ondataavailable = null;
        call.mediaRecorder.onstop = null;
        if (call.mediaRecorder.state === "recording") call.mediaRecorder.stop();
      } catch (_) {}
      call.mediaRecorder = null;
    }
    if (call.mediaStream) {
      try {
        call.mediaStream.getTracks().forEach((t) => t.stop());
      } catch (_) {}
      call.mediaStream = null;
    }
  }

  function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => {
        const s = String(fr.result || "");
        const i = s.indexOf(",");
        resolve(i >= 0 ? s.slice(i + 1) : s);
      };
      fr.onerror = reject;
      fr.readAsDataURL(blob);
    });
  }

  async function startCall() {
    if (call.active) return;
    // Android/LAN: mic + reliable STT need secure context (HTTPS :8443)
    if (!window.isSecureContext) {
    
  // ——— Voice message: hold mic → STT → chat → TTS ———
  async function voiceTurnMobile(blob, filename) {
    if (!blob || blob.size < 100 || busy) return;
    busy = true;
    if (call.active) setCallStatus("thinking");
    else setChip("Thinking");
    try {
      const buf = await blob.arrayBuffer();
      const bytes = new Uint8Array(buf);
      let bin = "";
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
      const b64 = btoa(bin);
      const res = await fetch("/api/voice/turn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          audio_b64: b64,
          filename: filename || "voice.webm",
          language: "de",
          speak: true,
        }),
      });
      const data = await res.json();
      if (data && data.text) add("user", "🎤 " + data.text);
      const reply = (data && (data.reply || data.error)) || "Keine Antwort.";
      add("assistant", reply);
      if (data && data.tts_b64) {
        const raw = atob(data.tts_b64);
        const arr = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
        const mp3 = new Blob([arr], { type: "audio/mpeg" });
        // play via speak path: inject by temporarily using audio element
        stopTts();
        const gen = call.speakGen;
        call.speaking = true;
        if (call.active) setCallStatus("speaking");
        else setChip("Speaking");
        const url = URL.createObjectURL(mp3);
        ttsAudio.src = url;
        await new Promise(function (resolve) {
          let settled = false;
          const done = function () {
            if (settled) return;
            settled = true;
            try { clearTimeout(hang); } catch (e) {}
            try { URL.revokeObjectURL(url); } catch (e2) {}
            resolve();
          };
          const hang = setTimeout(done, 90000);
          ttsAudio.onended = done;
          ttsAudio.onerror = done;
          ttsAudio.play().catch(done);
        });
        if (gen === call.speakGen) {
          call.speaking = false;
          document.body.classList.remove("nx-speaking");
          if (call.active && !call.muted) setCallStatus("listening");
          else setChip("Voice");
        }
      } else if (reply) {
        await speak(reply);
      }
    } catch (e) {
      add("assistant", "Voice-Msg Fehler: " + (e.message || e));
    } finally {
      busy = false;
      if (call.active && !call.muted && !call.speaking) setCallStatus("listening");
    }
  }

  (function wireMobileVoiceMsg() {
    const btn = document.getElementById("mVoiceMsg");
    if (!btn) return;
    let rec = null, stream = null, chunks = [], holdTimer = null, recording = false;
    async function startRec() {
      try {
        await unlockAudio();
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
        const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
          ? "audio/webm;codecs=opus"
          : (MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "");
        chunks = [];
        rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
        rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
        rec.onstop = async () => {
          try { stream.getTracks().forEach((tr) => tr.stop()); } catch (e) {}
          stream = null;
          const blob = new Blob(chunks, { type: (rec && rec.mimeType) || "audio/webm" });
          recording = false;
          btn.classList.remove("is-recording");
          if (blob.size > 200) await voiceTurnMobile(blob, "mvoice.webm");
        };
        rec.start();
        recording = true;
        btn.classList.add("is-recording");
        setChip("Listening");
      } catch (e) {
        add("assistant", "Mic: " + (e.message || e));
      }
    }
    function stopRec() {
      if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
      if (recording && rec && rec.state === "recording") {
        try { rec.stop(); } catch (e) {}
      } else if (stream) {
        try { stream.getTracks().forEach((tr) => tr.stop()); } catch (e) {}
        stream = null;
      }
    }
    btn.addEventListener("pointerdown", (e) => {
      if (call.active) return;
      if (e.button != null && e.button !== 0) return;
      e.preventDefault();
      holdTimer = setTimeout(startRec, 280);
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => {
      btn.addEventListener(ev, () => { if (recording) stopRec(); else if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; } });
    });
  })();


  try { showHttpsBannerIfNeeded(); } catch (_) {}
      var httpsUrl = httpsMobileUrl();
      add(
        "assistant",
        "Anruf braucht HTTPS fuer Mikrofon/STT. Oeffne: " +
          httpsUrl +
          " (einmal Zertifikat akzeptieren). Redirect…"
      );
      setTimeout(function () {
        location.href = httpsUrl;
      }, 600);
      return;
    }
    // User gesture: unlock audio + mic
    await unlockAudio();
    call.active = true;
    call.muted = false;
    call.bargeIn = false;
    call.speaking = false;
    call.holds = 0; // voicefix-20261006
    call.recPaused = false;
    call.resumeAt = 0;
    document.body.classList.add("nx-in-call");
    const ov = $("#mCallOverlay");
    if (ov) ov.hidden = false;
    const btn = $("#mCallBtn");
    if (btn) {
      btn.classList.add("on");
      btn.setAttribute("aria-pressed", "true");
    }
    const muteBtn = $("#mCallMute");
    if (muteBtn) muteBtn.setAttribute("aria-pressed", "false");
    const tx = $("#mCallTranscript");
    if (tx) {
      tx.hidden = !call.showText;
      tx.innerHTML = "";
    }
    startCallTimer();
    setCallStatus("listening");

    // Switch to chat view
    $$("#mNav button").forEach((x) => x.classList.remove("active"));
    const chatNav = $$("#mNav button").find((b) => b.dataset.view === "chat");
    if (chatNav) chatNav.classList.add("active");
    $$(".m-view").forEach((x) => x.classList.toggle("active", x.dataset.view === "chat"));

    let ok = false;
    if (hasWebSpeech()) {
      ok = startWebSpeechListening();
    }
    if (!ok) {
      try {
        await startChunkStt();
        ok = true;
      } catch (e) {
        add("assistant", "Anruf nicht möglich: " + (e && e.message ? e.message : e) + ". Chrome/Safari mit Mikrofon-Recht nutzen.");
        hangUp();
        return;
      }
    }
    const hint = $("#mCallHint");
    if (hint) {
      hint.textContent =
        call.mode === "webspeech"
          ? "Web Speech · Amala TTS · Agent + Tools parallel"
          : "Server-STT (Whisper) · Amala TTS · Agent + Tools parallel";
    }
  }

  function hangUp() {
    call.active = false;
    stopTts();
    stopWebSpeech();
    stopChunkStt();
    stopCallTimer();
    document.body.classList.remove("nx-in-call", "nx-speaking");
    const ov = $("#mCallOverlay");
    if (ov) {
      ov.hidden = true;
      ov.dataset.state = "idle";
    }
    const btn = $("#mCallBtn");
    if (btn) {
      btn.classList.remove("on");
      btn.setAttribute("aria-pressed", "false");
    }
    setChip("Voice Aus");
    call.status = "idle";
  }

  function toggleMute() {
    if (!call.active) return;
    call.muted = !call.muted;
    const muteBtn = $("#mCallMute");
    if (muteBtn) muteBtn.setAttribute("aria-pressed", call.muted ? "true" : "false");
    if (call.muted) {
      stopWebSpeech();
      stopChunkStt();
      setCallStatus("muted");
    } else {
      setCallStatus("listening");
      if (call.mode === "stt_chunks" || !hasWebSpeech()) {
        startChunkStt().catch(() => {});
      } else {
        startWebSpeechListening();
      }
    }
  }

  function toggleCallText() {
    call.showText = !call.showText;
    const tx = $("#mCallTranscript");
    const btn = $("#mCallShowText");
    if (tx) tx.hidden = !call.showText;
    if (btn) btn.setAttribute("aria-pressed", call.showText ? "true" : "false");
  }

  // ——— Legacy short live (kept, hidden button) ———
  function startLive() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      add("assistant", "Web Speech API nicht verfügbar — nutze „Anrufen“.");
      return;
    }
    live = true;
    const lb = $("#mLive");
    if (lb) lb.classList.add("on");
    setChip("Listening");
    const rec = new SR();
    rec.lang = "de-DE";
    rec.continuous = true;
    rec.interimResults = false;
    recognition = rec;
    rec.onresult = async (ev) => {
      const t = ev.results[ev.results.length - 1][0].transcript.trim();
      if (!t || busy) return;
      if (ttsAudio && !ttsAudio.paused) {
        try {
          ttsAudio.pause();
        } catch (_) {}
      }
      input.value = t;
      await send();
    };
    rec.onend = () => {
      if (live) {
        try {
          rec.start();
        } catch (_) {}
      }
    };
    try {
      rec.start();
    } catch (_) {}
  }

  function stopLive() {
    live = false;
    const lb = $("#mLive");
    if (lb) lb.classList.remove("on");
    setChip("Voice Aus");
    if (recognition) {
      try {
        recognition.onend = null;
        recognition.stop();
      } catch (_) {}
      recognition = null;
    }
  }

  $("#mSend").onclick = send;
  const liveBtn = $("#mLive");
  if (liveBtn) liveBtn.onclick = () => (live ? stopLive() : startLive());
  const callBtn = $("#mCallBtn");
  if (callBtn) callBtn.onclick = () => (call.active ? hangUp() : startCall());
  const hangBtn = $("#mCallHang");
  if (hangBtn) hangBtn.onclick = hangUp;
  const muteBtn = $("#mCallMute");
  if (muteBtn) muteBtn.onclick = toggleMute;
  const textBtn = $("#mCallShowText");
  if (textBtn) textBtn.onclick = toggleCallText;

  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  async function loadGallery() {
    const box = $("#mGallery");
    const empty = $("#mGalEmpty");
    if (!box) return;
    box.innerHTML = '<div class="m-muted" style="grid-column:1/-1">Lade…</div>';
    try {
      const r = await api("/api/gallery");
      const items = (r && r.items) || [];
      if (!items.length) {
        box.innerHTML = "";
        if (empty) empty.hidden = false;
        return;
      }
      if (empty) empty.hidden = true;
      box.innerHTML = "";
      items.forEach((it) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "m-thumb";
        btn.title = it.name || "";
        const img = document.createElement("img");
        img.loading = "lazy";
        img.alt = it.name || "Bild";
        img.src = it.url;
        btn.appendChild(img);
        btn.onclick = () => openLightbox(it.url, (it.name || "") + (it.source ? " · " + it.source : ""));
        box.appendChild(btn);
      });
    } catch (e) {
      box.innerHTML = '<div class="m-muted" style="grid-column:1/-1">Galerie-Fehler</div>';
    }
  }

  function openLightbox(url, cap) {
    const lb = $("#mLightbox");
    const img = $("#mLbImg");
    const c = $("#mLbCap");
    if (!lb || !img) return;
    img.src = url;
    if (c) c.textContent = cap || "";
    lb.hidden = false;
  }
  function closeLightbox() {
    const lb = $("#mLightbox");
    if (lb) lb.hidden = true;
  }
  const lbClose = $("#mLbClose");
  if (lbClose) lbClose.onclick = closeLightbox;
  const lb = $("#mLightbox");
  if (lb) lb.addEventListener("click", (e) => { if (e.target === lb) closeLightbox(); });
  const galRefresh = $("#mGalRefresh");
  if (galRefresh) galRefresh.onclick = () => loadGallery();

  function setMapQuery(q) {
    const query = (q || "").trim() || "München";
    const enc = encodeURIComponent(query);
    const frame = $("#mMapFrame");
    const link = $("#mMapLink");
    fetch("https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + enc, {
      headers: { Accept: "application/json" },
    })
      .then((r) => r.json())
      .then((arr) => {
        if (!arr || !arr[0]) {
          if (frame) frame.src = "https://www.openstreetmap.org/export/embed.html?bbox=5.5,47.2,15.2,55.2&layer=mapnik";
          if (link) link.href = "https://www.openstreetmap.org/search?query=" + enc;
          return;
        }
        const it = arr[0];
        const south = parseFloat(it.boundingbox[0]);
        const north = parseFloat(it.boundingbox[1]);
        const west = parseFloat(it.boundingbox[2]);
        const east = parseFloat(it.boundingbox[3]);
        const bbox = [west, south, east, north].join(",");
        const lat = it.lat;
        const lon = it.lon;
        if (frame) {
          frame.src =
            "https://www.openstreetmap.org/export/embed.html?bbox=" +
            encodeURIComponent(bbox) +
            "&layer=mapnik&marker=" +
            encodeURIComponent(lat + "," + lon);
        }
        if (link) link.href = "https://www.openstreetmap.org/#map=14/" + lat + "/" + lon;
      })
      .catch(() => {
        if (link) link.href = "https://www.openstreetmap.org/search?query=" + enc;
      });
  }
  const mapGo = $("#mMapGo");
  const mapQ = $("#mMapQ");
  if (mapGo) mapGo.onclick = () => setMapQuery(mapQ && mapQ.value);
  if (mapQ) mapQ.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); setMapQuery(mapQ.value); }
  });

  $$("#mNav button").forEach((b) => {
    b.onclick = () => {
      if (call.active) return;
      $$("#mNav button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const v = b.dataset.view;
      $$(".m-view").forEach((x) => x.classList.toggle("active", x.dataset.view === v));
      if (v === "galerie") loadGallery();
      if (v === "maps" && mapQ && !mapQ.dataset.primed) {
        mapQ.dataset.primed = "1";
      }
    };
  });

  window.media = async (a) => {
    await api("/api/media/" + a, { method: "POST", body: "{}" });
  };

  try { showHttpsBannerIfNeeded(); } catch (_) {}
  registerSW();
  setupInstall();
  try { setupOffline(); } catch (_) {}
  setupKeyboardGuard();
  const mDashBtn = document.getElementById("mDashRefresh");
  if (mDashBtn) mDashBtn.onclick = () => refreshMobileDash();
  refreshMobileDash();
  setInterval(refreshMobileDash, 5000);
  try { showSwVersion(); } catch (_) {}

  (async () => {
    const h = await api("/api/health");
    if (h && h.offline) {
      $("#mHealth").textContent = "Offline";
      $("#mHealth").classList.add("warn");
      $("#wHealth").textContent = "Host nicht erreichbar";
      $("#wOllama").textContent = "unbekannt (offline)";
      $("#wMods").textContent = "—";
      $("#mVer").textContent = "—";
      return;
    }
    setHostReachable(true);
    $("#mHealth").textContent = "Health " + h.score;
    $("#mHealth").classList.remove("warn");
    $("#mHealth").classList.add("ok");
    $("#wHealth").textContent = h.score;
    $("#wOllama").textContent = (h.ollama && h.ollama.reachable ? "online" : "offline") + " · " + ((h.ollama && h.ollama.model) || "—");
    $("#wMods").textContent = Object.keys(h.modules || {}).filter((k) => h.modules[k]).join(", ");
    $("#mVer").textContent = h.version || "—";
  })();
})();
