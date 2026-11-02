/* NEXUS Live-Avatar Hook – rein additiv. Ersetzt sichtbare Avatar-Bilder durch lebende Canvas-Avatare.
 * /m : Anruf-Overlay (#mCallOverlay) + echte TTS (#mTts, gleiche Herkunft /api/voice/tts) -> WebAudio-Lip-Sync.
 * App: NexusEvents 'call' {type:speaker|played|state|mouth|word} -> Sprecherwechsel, Pegel aus nativem Visualizer (Java-Patch).
 * Kein WebGL im App-WebView (Redmi: EGL_BAD_PARAMETER) -> Canvas2D Standard. Bild-Fallback bleibt im DOM. */
(function () {
  'use strict';
  var BASE = window.NXLIVE_BASE || '/static/assets/avatar/', isApp = !!(window.NexusNative || window.NexusEvents || /NexusOS/i.test(navigator.userAgent));
  window.NXLIVE_OPTS = Object.assign({ renderer: 'canvas2d', budgetMs: 4 }, window.NXLIVE_OPTS || {});
  if (!isApp && /[?&]gl=1/.test(location.search) && window.NXLive && NXLive.glSelfTest()) window.NXLIVE_OPTS.renderer = 'webgl';
  var NAMES = { nexus: ['NEXUS', 'Koordination'], hermes: ['HERMES', 'Umsetzung'], gpt: ['NEXUS GPT', 'Analyse · OpenAI'], aether: ['AETHER', 'Sohn von NEXUS'] }, AV = {}, stage = null;
  function lm(id) { return fetch(BASE + id + '.json').then(function (r) { return r.json(); }); }
  function build(host, ids, before) {
    var wrap = document.createElement('div'); wrap.className = 'nxl-stage nxl-embed'; if (before) host.insertBefore(wrap, before); else host.appendChild(wrap);
    return Promise.all(ids.map(function (id) { var c = document.createElement('div'); c.className = 'nxl-card'; c.innerHTML = '<div class="nxl-wave"></div><div class="nxl-name">' + NAMES[id][0] + '<small>' + NAMES[id][1] + '</small></div>'; wrap.appendChild(c);
      return lm(id).then(function (L) { AV[id] = NXLive.mount(c, { id: id, src: BASE + id + '.webp', landmarks: L }); }); }))
      .then(function () { stage = NXLive.stage(ids.map(function (i) { return AV[i]; })); window.NXLIVE_STAGE = stage; window.NXLIVE_AV = AV; return stage; });
  }
  function unlock() { try { NXLive.audioCtx().resume(); } catch (e) {} }
  document.addEventListener('pointerdown', unlock, { once: true, capture: true });
  function initM() {
    var ov = document.getElementById('mCallOverlay'), tts = document.getElementById('mTts'); if (!ov) return;
    var panel = ov.querySelector('.m-call-panel') || ov, round = ov.querySelector('.m-call-avatar'); build(panel, ['nexus', 'hermes', 'gpt'], round).then(function () { if (round) round.classList.add('nxl-replaced');
      if (!tts) return; var attached = false;
      tts.addEventListener('play', function () { var ac = NXLive.audioCtx(); ac.resume().then(function () { if (!attached && ac.state === 'running') { AV.nexus.attachAudio(tts); attached = true; } stage.setSpeaker('nexus'); }); });
      tts.addEventListener('ended', function () { stage.setSpeaker(null); AV.nexus.setState('listening'); });
      tts.addEventListener('pause', function () { stage.setSpeaker(null); });
    });
  }
  function initApp() {
    var host = document.querySelector('#v-call .nx-agents') || document.querySelector('#v-call'); if (!host || !window.NexusEvents) return;
    build(host, ['nexus', 'hermes']).then(function () {
      var prev = NexusEvents.emit; NexusEvents.emit = function (ev, d) {
        try { if (ev === 'call' && d) {
          if (d.type === 'speaker') stage.setSpeaker(d.speaker === 'hermes' ? 'hermes' : 'nexus');
          else if (d.type === 'mouth') { var a = AV[d.speaker === 'hermes' ? 'hermes' : 'nexus']; a && a.feedLevels(d); }
          else if (d.type === 'word') { var b = AV[d.speaker === 'hermes' ? 'hermes' : 'nexus']; b && b.speakWords([{ t: 0, d: Math.max(.12, (d.end - d.start) * .065), w: d.text || 'a' }], performance.now()); }
          else if (d.type === 'played' || (d.type === 'state' && d.state !== 'SPEAKING')) { stage.setSpeaker(null); Object.keys(AV).forEach(function (k) { AV[k].setState(d.state === 'THINKING' ? 'thinking' : 'listening'); }); }
        } } catch (e) {}
        return prev.apply(this, arguments);
      };
    });
  }
  function go() { if (!window.NXLive) return; if (document.getElementById('mCallOverlay')) initM(); else if (document.getElementById('v-call')) initApp(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
})();
