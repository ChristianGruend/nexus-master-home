/* NEXUS · Lokaler Musik-Player (nexus_music.js)
   Reine Browserlösung: MP3/WAV/OGG aus lokaler Dateiauswahl.
   Keine Cloud, kein Upload - Dateien bleiben via Object-URL im Browser.
   Play/Pause, Prev/Next, Stop, Playlist, Fortschritt, Lautstärke,
   Shuffle/Repeat und Web-Audio-Visualizer (AnalyserNode). */
(function () {
  "use strict";

  var root = document.getElementById("nxmRoot");
  if (!root) return;

  var audio = document.getElementById("nxmAudio");
  if (!audio) return;

  var $ = function (id) { return document.getElementById(id); };

  var el = {
    canvas: $("nxmCanvas"),
    title: $("nxmTitle"),
    meta: $("nxmMeta"),
    cur: $("nxmCur"),
    dur: $("nxmDur"),
    seek: $("nxmSeek"),
    vol: $("nxmVol"),
    mute: $("nxmMute"),
    toggle: $("nxmToggle"),
    pick: $("nxmPick"),
    files: $("nxmFiles"),
    clear: $("nxmClear"),
    count: $("nxmCount"),
    list: $("nxmPlaylist")
  };

  var tracks = [];
  var current = -1;
  var shuffle = false;
  var repeat = 0; /* 0=off, 1=all, 2=one */
  var seeking = false;

  /* ---------------- Web Audio (Visualizer) ---------------- */
  var actx = null;
  var analyser = null;
  var srcNode = null;
  var freq = null;

  function ensureAudioGraph() {
    if (srcNode) return;
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    try {
      actx = new Ctx();
      srcNode = actx.createMediaElementSource(audio);
      analyser = actx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.82;
      freq = new Uint8Array(analyser.frequencyBinCount);
      srcNode.connect(analyser);
      analyser.connect(actx.destination);
    } catch (e) {
      actx = null; analyser = null; srcNode = null; freq = null;
    }
  }

  function resumeCtx() {
    if (actx && actx.state === "suspended") {
      actx.resume().catch(function () {});
    }
  }

  /* ---------------- Helpers ---------------- */
  function fmt(sec) {
    if (!isFinite(sec) || sec < 0) sec = 0;
    var m = Math.floor(sec / 60);
    var s = Math.floor(sec % 60);
    return m + ":" + (s < 10 ? "0" + s : s);
  }

  function kb(n) {
    if (!n) return "";
    if (n > 1048576) return (n / 1048576).toFixed(1) + " MB";
    return Math.round(n / 1024) + " KB";
  }

  function baseName(name) {
    return String(name || "").replace(/\.[^.]+$/, "");
  }

  function setNowPlaying(t) {
    if (!el.title) return;
    if (!t) {
      el.title.textContent = "Lokaler Player bereit";
      if (el.meta) el.meta.textContent = "MP3 · WAV · OGG · bleibt lokal im Browser";
      return;
    }
    el.title.textContent = baseName(t.name);
    if (el.meta) el.meta.textContent = (t.ext ? t.ext.toUpperCase() : "AUDIO") + (t.size ? " · " + kb(t.size) : "");
  }

  function updateCount() {
    if (el.count) el.count.textContent = tracks.length + (tracks.length === 1 ? " Track" : " Tracks");
  }

  function renderPlaylist() {
    if (!el.list) return;
    if (!tracks.length) {
      el.list.innerHTML = '<div class="streams-note">Noch keine lokalen Tracks. „Lokale Dateien wählen“ – alles bleibt im Browser, kein Upload.</div>';
      updateCount();
      return;
    }
    el.list.innerHTML = "";
    tracks.forEach(function (t, i) {
      var row = document.createElement("div");
      row.className = "nxm-track" + (i === current ? " active" : "");
      var num = document.createElement("span");
      num.className = "nxm-track-num";
      num.textContent = i === current ? "▶" : String(i + 1);
      var nm = document.createElement("span");
      nm.className = "nxm-track-name";
      nm.textContent = baseName(t.name);
      nm.title = t.name;
      var meta = document.createElement("span");
      meta.className = "nxm-track-meta";
      meta.textContent = (t.ext ? t.ext.toUpperCase() : "") + (t.size ? " · " + kb(t.size) : "");
      var rm = document.createElement("button");
      rm.type = "button";
      rm.className = "nxm-track-rm";
      rm.textContent = "✕";
      rm.title = "Entfernen";
      rm.onclick = function (e) {
        e.stopPropagation();
        removeTrack(i);
      };
      row.appendChild(num);
      row.appendChild(nm);
      row.appendChild(meta);
      row.appendChild(rm);
      row.onclick = function () { playIndex(i); };
      el.list.appendChild(row);
    });
    updateCount();
  }

  function markActive() {
    if (!el.list) return;
    var rows = el.list.querySelectorAll(".nxm-track");
    for (var i = 0; i < rows.length; i++) {
      var on = i === current;
      rows[i].classList.toggle("active", on);
      var n = rows[i].querySelector(".nxm-track-num");
      if (n) n.textContent = on ? "▶" : String(i + 1);
    }
  }

  /* ---------------- Playback ---------------- */
  function playIndex(i, autoplay) {
    if (i < 0 || i >= tracks.length) return;
    current = i;
    var t = tracks[i];
    audio.src = t.url;
    try { audio.load(); } catch (e) {}
    setNowPlaying(t);
    markActive();
    if (autoplay !== false) {
      ensureAudioGraph();
      resumeCtx();
      var p = audio.play();
      if (p && p.catch) p.catch(function () {});
    }
  }

  function togglePlay() {
    if (current < 0) {
      if (tracks.length) playIndex(0);
      return;
    }
    if (audio.paused) {
      ensureAudioGraph();
      resumeCtx();
      var p = audio.play();
      if (p && p.catch) p.catch(function () {});
    } else {
      audio.pause();
    }
  }

  function stopAll() {
    audio.pause();
    try { audio.currentTime = 0; } catch (e) {}
    if (el.seek) el.seek.value = 0;
    if (el.cur) el.cur.textContent = "0:00";
  }

  function nextIndex(step) {
    if (!tracks.length) return -1;
    if (shuffle && tracks.length > 1) {
      var r = current;
      while (r === current) r = Math.floor(Math.random() * tracks.length);
      return r;
    }
    var n = current + step;
    if (n >= tracks.length) {
      if (repeat === 1 || step > 0) return 0;
      return tracks.length - 1;
    }
    if (n < 0) return tracks.length - 1;
    return n;
  }

  function next() {
    if (!tracks.length) return;
    var n = nextIndex(1);
    if (n >= 0) playIndex(n);
  }

  function prev() {
    if (!tracks.length) return;
    if (audio.currentTime > 3) { stopAll(); audio.play().catch(function () {}); return; }
    var n = nextIndex(-1);
    if (n >= 0) playIndex(n);
  }

  function canNext() {
    if (!tracks.length) return false;
    if (shuffle || repeat === 1) return true;
    return current < tracks.length - 1;
  }

  audio.addEventListener("ended", function () {
    if (repeat === 2) { playIndex(current); return; }
    if (canNext()) next();
    else {
      audio.pause();
      if (el.toggle) el.toggle.textContent = "▶";
    }
  });

  audio.addEventListener("play", function () {
    if (el.toggle) el.toggle.textContent = "⏸";
    resumeCtx();
  });
  audio.addEventListener("pause", function () {
    if (el.toggle) el.toggle.textContent = "▶";
  });

  audio.addEventListener("loadedmetadata", function () {
    if (el.dur) el.dur.textContent = fmt(audio.duration);
  });

  audio.addEventListener("timeupdate", function () {
    if (seeking) return;
    if (el.cur) el.cur.textContent = fmt(audio.currentTime);
    if (el.seek && isFinite(audio.duration) && audio.duration > 0) {
      el.seek.value = Math.round((audio.currentTime / audio.duration) * 1000);
    }
  });

  audio.addEventListener("error", function () {
    if (current >= 0) {
      setNowPlaying(tracks[current]);
      if (el.meta) el.meta.textContent = "Datei kann nicht dekodiert werden";
    }
  });

  /* ---------------- File handling ---------------- */
  var AUDIO_EXT = /\.(mp3|wav|ogg|oga|m4a|aac|flac|opus|weba)$/i;

  function extOf(name) {
    var m = String(name || "").match(/\.([^.]+)$/);
    return m ? m[1] : "";
  }

  function addFiles(fileList) {
    var added = 0;
    for (var i = 0; i < fileList.length; i++) {
      var f = fileList[i];
      if (!f) continue;
      var isAudio = (f.type && f.type.indexOf("audio/") === 0) || AUDIO_EXT.test(f.name || "");
      if (!isAudio) continue;
      tracks.push({
        name: f.name || ("Track " + (tracks.length + 1)),
        ext: extOf(f.name),
        size: f.size,
        url: URL.createObjectURL(f),
        _file: f
      });
      added++;
    }
    renderPlaylist();
    if (added && current < 0) playIndex(0);
  }

  function removeTrack(i) {
    if (i < 0 || i >= tracks.length) return;
    var t = tracks[i];
    var wasCurrent = i === current;
    try { URL.revokeObjectURL(t.url); } catch (e) {}
    tracks.splice(i, 1);
    if (i < current) current--;
    else if (wasCurrent) {
      current = -1;
      audio.pause();
      audio.removeAttribute("src");
      try { audio.load(); } catch (e) {}
      setNowPlaying(null);
      if (tracks.length) playIndex(Math.min(i, tracks.length - 1));
    }
    renderPlaylist();
  }

  function clearAll() {
    tracks.forEach(function (t) { try { URL.revokeObjectURL(t.url); } catch (e) {} });
    tracks = [];
    current = -1;
    audio.pause();
    audio.removeAttribute("src");
    try { audio.load(); } catch (e) {}
    setNowPlaying(null);
    if (el.seek) el.seek.value = 0;
    if (el.cur) el.cur.textContent = "0:00";
    if (el.dur) el.dur.textContent = "0:00";
    renderPlaylist();
  }

  /* ---------------- Bindings ---------------- */
  if (el.pick && el.files) el.pick.onclick = function () { el.files.click(); };
  if (el.files) el.files.addEventListener("change", function () {
    if (el.files.files && el.files.files.length) addFiles(el.files.files);
    el.files.value = "";
  });
  if (el.clear) el.clear.onclick = clearAll;

  root.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-nxm]") : null;
    if (!b) return;
    var a = b.getAttribute("data-nxm");
    if (a === "prev") prev();
    else if (a === "next") next();
    else if (a === "toggle") togglePlay();
    else if (a === "stop") stopAll();
    else if (a === "mute") {
      audio.muted = !audio.muted;
      if (el.mute) el.mute.textContent = audio.muted ? "🔇" : "🔊";
    } else if (a === "shuffle") {
      shuffle = !shuffle;
      b.classList.toggle("on", shuffle);
    } else if (a === "repeat") {
      repeat = (repeat + 1) % 3;
      b.classList.toggle("on", repeat !== 0);
      b.classList.toggle("one", repeat === 2);
      b.title = repeat === 0 ? "Wiederholen: aus" : (repeat === 1 ? "Wiederholen: alle" : "Wiederholen: ein Titel");
    }
  });

  if (el.seek) {
    el.seek.addEventListener("input", function () {
      seeking = true;
      if (isFinite(audio.duration) && audio.duration > 0 && el.cur) {
        el.cur.textContent = fmt((el.seek.value / 1000) * audio.duration);
      }
    });
    el.seek.addEventListener("change", function () {
      if (isFinite(audio.duration) && audio.duration > 0) {
        audio.currentTime = (el.seek.value / 1000) * audio.duration;
      }
      seeking = false;
    });
  }

  if (el.vol) {
    el.vol.value = audio.volume;
    el.vol.addEventListener("input", function () {
      audio.volume = parseFloat(el.vol.value);
      audio.muted = false;
      if (el.mute) el.mute.textContent = audio.volume === 0 ? "🔇" : "🔊";
    });
  }

  /* Keyboard: nur wenn Musik-Panel sichtbar */
  document.addEventListener("keydown", function (e) {
    var panel = document.getElementById("musicPanel");
    if (!panel || !panel.classList.contains("show")) return;
    var tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (e.key === " " || e.code === "Space") { e.preventDefault(); togglePlay(); }
    else if (e.key === "ArrowRight") { audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 5); }
    else if (e.key === "ArrowLeft") { audio.currentTime = Math.max(0, audio.currentTime - 5); }
    else if (e.key === "n") { next(); }
    else if (e.key === "p") { prev(); }
  });

  /* ---------------- Visualizer ---------------- */
  var dpr = Math.min(window.devicePixelRatio || 1, 2);

  function fit() {
    if (!el.canvas) return false;
    var w = el.canvas.clientWidth || 0;
    var h = el.canvas.clientHeight || 0;
    if (!w || !h) return false;
    var cw = Math.round(w * dpr), ch = Math.round(h * dpr);
    if (el.canvas.width !== cw || el.canvas.height !== ch) {
      el.canvas.width = cw;
      el.canvas.height = ch;
    }
    return true;
  }

  window.addEventListener("resize", fit);

  function draw(ts) {
    requestAnimationFrame(draw);
    if (!fit()) return;
    var ctx = el.canvas.getContext("2d");
    var w = el.canvas.width, h = el.canvas.height;
    ctx.clearRect(0, 0, w, h);

    var cx = w / 2, cy = h / 2;
    var base = Math.min(w, h) * 0.16;
    var t = ts / 1000;

    if (analyser && freq && !audio.paused) analyser.getByteFrequencyData(freq);

    var energy = 0;
    if (freq && analyser && !audio.paused) {
      for (var k = 0; k < freq.length; k += 8) energy += freq[k];
      energy = energy / (freq.length / 8) / 255;
    }

    var glow = 0.35 + energy * 0.65;
    if (!audio.paused) glow = 0.5 + energy * 0.5;

    /* Orbital ring */
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(t * 0.25);
    ctx.strokeStyle = "rgba(47,143,216," + (0.18 * glow + 0.08).toFixed(3) + ")";
    ctx.lineWidth = 1 * dpr;
    for (var r = 0; r < 3; r++) {
      ctx.beginPath();
      ctx.ellipse(0, 0, base * (1.35 + r * 0.55), base * (0.75 + r * 0.32), (r * Math.PI) / 5, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();

    /* Radial spectrum */
    var bars = 96;
    var step = analyser ? Math.floor(freq.length / bars) : 0;
    ctx.save();
    ctx.translate(cx, cy);
    for (var i = 0; i < bars; i++) {
      var v = 0;
      if (freq && step && !audio.paused) {
        var s = 0;
        for (var j = 0; j < step; j++) s += freq[i * step + j] || 0;
        v = s / step / 255;
      } else {
        v = 0.04 + 0.03 * Math.sin(t * 1.6 + i * 0.35);
      }
      var ang = (i / bars) * Math.PI * 2 - Math.PI / 2;
      var len = base * (0.35 + v * 1.5);
      var x1 = Math.cos(ang) * base;
      var y1 = Math.sin(ang) * base;
      var x2 = Math.cos(ang) * (base + len);
      var y2 = Math.sin(ang) * (base + len);
      var a = 0.20 + v * 0.75;
      var g = ctx.createLinearGradient(x1, y1, x2, y2);
      g.addColorStop(0, "rgba(20,120,200," + a.toFixed(3) + ")");
      g.addColorStop(1, "rgba(214,140,20," + (a * 0.9).toFixed(3) + ")");
      ctx.strokeStyle = g;
      ctx.lineWidth = Math.max(1, 2 * dpr);
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    }
    ctx.restore();

    /* Pulsing core */
    var pulse = base * (0.9 + glow * 0.25);
    var grad = ctx.createRadialGradient(cx, cy, base * 0.15, cx, cy, pulse);
    grad.addColorStop(0, "rgba(47,143,216,0.22)");
    grad.addColorStop(0.6, "rgba(30,110,190,0.10)");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cy, pulse, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = "rgba(47,143,216," + (0.25 + glow * 0.4).toFixed(3) + ")";
    ctx.lineWidth = 1.5 * dpr;
    ctx.beginPath();
    ctx.arc(cx, cy, base, 0, Math.PI * 2);
    ctx.stroke();
  }

  renderPlaylist();
  updateCount();
  requestAnimationFrame(draw);
})();
