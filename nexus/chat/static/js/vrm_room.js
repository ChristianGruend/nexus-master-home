/* 005D.68E: isolated VRM iframe. Existing portraits and audio engine stay owned by
   their original components; only already computed motion values are forwarded. */
(function () {
  'use strict';
  const button = document.getElementById('btnVrmView');
  const frame = document.getElementById('nxVrmFrame');
  const group = document.querySelector('#nxAiRoom .nx-partners');
  const state = document.getElementById('nxVrmViewStatus');
  if (!button || !frame || !group || !state) return;
  let requested = false, ready = false, active = false, timer = null, intersecting = true;
  function post(message) {
    if (frame.contentWindow && frame.getAttribute('src')) frame.contentWindow.postMessage(message, location.origin);
  }
  function layout() {
    const root = frame.getBoundingClientRect(), slots = {};
    group.querySelectorAll('.nx-partner').forEach(card => {
      const rect = card.querySelector('.nx-holo-avatar').getBoundingClientRect();
      slots[card.dataset.partner] = {x:rect.x-root.x, y:rect.y-root.y, width:rect.width, height:rect.height};
    });
    post({type:'nexus:vrm-layout', slots});
  }
  function visibility() {post({type:'nexus:vrm-visible', visible:active && intersecting && !document.hidden});}
  function activate(value) {
    active = value && ready;
    group.classList.toggle('nx-vrm-active', active);
    frame.style.visibility = active ? 'visible' : 'hidden';
    button.setAttribute('aria-pressed', String(active));
    button.textContent = requested ? 'Originalporträts anzeigen' : '3D-Charaktere anzeigen';
    if (active) {state.textContent='Drei 3D-Charaktere aktiv';layout();}
    visibility();
  }
  button.addEventListener('click', () => {
    requested = !requested;
    if (requested && !frame.getAttribute('src')) {
      frame.src = '/static/avatars3d/index.html?embedded=1&design=005d68g3';
      state.textContent = '3D-Charaktere werden geladen…';
      timer = setTimeout(() => {
        if (ready) return;
        requested=false;activate(false);frame.removeAttribute('src');state.textContent='3D-Laden nicht bestätigt · Originalporträts aktiv';
      },90000);
    }
    activate(requested);
    if (!requested) state.textContent='Originalporträts aktiv';
    else if (!ready) state.textContent='3D-Charaktere werden geladen…';
  });
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || event.source !== frame.contentWindow || event.data?.type !== 'nexus:vrm-status') return;
    if (event.data.status === 'ready') {
      ready=true;clearTimeout(timer);activate(requested);
      if (!requested) state.textContent='Originalporträts aktiv · 3D bereit';
    } else if (event.data.status === 'error') {
      ready=false;requested=false;clearTimeout(timer);activate(false);
      frame.removeAttribute('src');
      state.textContent='3D-Fehler · Originalporträts aktiv';
      state.title=event.data.error || 'Laufzeit konnte nicht geladen werden';
    }
  });
  frame.addEventListener('load',layout);
  if (window.ResizeObserver) new ResizeObserver(layout).observe(group);
  if (window.IntersectionObserver) new IntersectionObserver(entries => {
    intersecting=entries[0].isIntersecting;visibility();
  }).observe(group);
  window.addEventListener('resize',layout);
  document.addEventListener('visibilitychange',visibility);
  // Reuse the existing single TTS analyser and its confirmed speaker. No model
  // request, phoneme service, audio element or second AudioContext is introduced.
  let previous = 0;
  function motion(time) {
    requestAnimationFrame(motion);
    if (!active || !intersecting || document.hidden || time-previous<33) return;
    previous=time;
    const partners = {};
    group.querySelectorAll('.nx-partner').forEach(card => {
      const style=getComputedStyle(card), partner=card.dataset.partner;
      const numeric=key=>Number.parseFloat(style.getPropertyValue(key))||0;
      partners[partner] = {
        speaking:!!(window.voice?.speaking && window.voice.speakingPartner===partner),
        viseme:card.dataset.viseme || 'neutral', weight:numeric('--nx-viseme-weight'),
        blink:numeric('--nx-lid'), headRoll:numeric('--nx-head-r'),
        eyeX:numeric('--nx-eye-x'), eyeY:numeric('--nx-eye-y')
      };
    });
    post({type:'nexus:vrm-motion',partners});
  }
  requestAnimationFrame(motion);
  // The three designed characters are the normal view; original portraits remain
  // visible until all models are ready and on any runtime error.
  button.click();
})();
