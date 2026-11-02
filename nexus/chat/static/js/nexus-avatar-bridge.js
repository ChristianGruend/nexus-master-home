/* NEXUS Avatar-Bridge 20261026: Originalportraits (Eddie 26.10.) + NXLive-Engine in Master Home.
 * Idle/Atmung/Kopf, Blinzeln, Sakkaden/Blickfolge, Mimik (Zustaende), Lippensync:
 *  - Audio-TTS: WebAudio-Analyser am tatsaechlich abgespielten Audio-Element
 *  - Browser-TTS (speechSynthesis): Wort-Timings aus onboundary
 * Faellt ohne Canvas/Engine still auf das vorhandene <img> zurueck. */
(function () {
  'use strict';
  if (!window.NXLive) return;
  var BASE = '/static/assets/avatar_orig/', V = '?v=avatar20261026';
  var av = {}, lastAudio = null;
  window.NEXUS_LIVE = { avatars: av, ready: false };
  var op = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () { if (this.tagName === 'AUDIO' || !this.videoWidth) lastAudio = this; return op.apply(this, arguments); };
  function speaker() { var b = document.querySelector('[data-partner][data-avatar-state="speaking"]'); return b && b.dataset.partner; }
  if (window.speechSynthesis) {
    var os = speechSynthesis.speak.bind(speechSynthesis);
    speechSynthesis.speak = function (u) {
      try {
        var id = speaker() || 'nexus', t0 = performance.now();
        u.addEventListener('start', function () { t0 = performance.now(); var a = av[id]; if (a) a.setState('speaking'); });
        u.addEventListener('boundary', function (e) { var a = av[id]; if (!a) return; var w = (u.text || '').substr(e.charIndex, e.charLength || 6); a.speakWords([{ t: 0, d: Math.max(.18, w.length * .065), w: w }], performance.now()); });
        u.addEventListener('end', function () { var a = av[id]; if (a) a.stopSpeaking(); });
      } catch (e) {}
      return os(u);
    };
  }
  function mount(btn) {
    var id = btn.dataset.partner, host = btn.querySelector('.nx-holo-avatar');
    if (!host || av[id]) return;
    fetch(BASE + id + '.json' + V).then(function (r) { if (!r.ok) throw 0; return r.json(); }).then(function (L) {
      var img = host.querySelector('img');
      var wrap = document.createElement('span'); wrap.className = 'nxl-host'; host.appendChild(wrap);
      var a = NXLive.mount(wrap, { id: id, src: BASE + id + '.webp' + V, landmarks: L });
      if (!a || !a.ok) { wrap.remove(); return; }
      if (img) { img.src = BASE + id + '.webp' + V; img.classList.add('nxl-under'); }
      av[id] = a; host.setAttribute('data-live', 'nxlive-20261026');
      window.NEXUS_LIVE.ready = true;
    }).catch(function () {});
  }
  function sync(btn) {
    var a = av[btn.dataset.partner]; if (!a) return; var s = btn.dataset.avatarState || 'idle';
    if (s === 'speaking') { if (lastAudio && !lastAudio.paused) { try { a.attachAudio(lastAudio); } catch (e) {} } a.setState('speaking'); }
    else if (s === 'thinking' || s === 'listening') { a.detachAudio(); a.setState(s); }
    else { a.stopSpeaking(); }
    a.setActive(btn.classList.contains('active') || s !== 'idle' ? 1 : .6);
  }
  function init() {
    var btns = document.querySelectorAll('.nx-partner[data-partner]');
    btns.forEach(mount);
    var mo = new MutationObserver(function (ms) { ms.forEach(function (m) { if (m.target.dataset && m.target.dataset.partner) sync(m.target); }); });
    btns.forEach(function (b) { mo.observe(b, { attributes: true, attributeFilter: ['data-avatar-state', 'class'] }); });
    document.addEventListener('pointermove', function (e) { var x = e.clientX / innerWidth * 2 - 1, y = e.clientY / innerHeight * 2 - 1; for (var k in av) av[k].lookAt(x * .6, y * .4); }, { passive: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
