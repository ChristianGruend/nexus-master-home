/** NEXUS live desktop — seamless ping-pong + crossfade (kein Reset-Ruckler) */
(function () {
  const KEY = "nexus.desktopBg";
  const FADE_LEAD = 1.15; // Sekunden vor Ende Crossfade starten
  const body = document.body;
  const a = document.getElementById("nxDesktopBg");
  const b = document.getElementById("nxDesktopBgB");
  const btn = document.getElementById("btnDesktopBg");
  if (!a || !b) return;

  let active = a;
  let idle = b;
  let fading = false;
  let raf = 0;
  let enabled = true;

  function prep(v) {
    v.muted = true;
    v.playsInline = true;
    v.loop = false; // wir loopen selbst per Crossfade
    try { v.setAttribute("playsinline", ""); } catch (e) {}
  }
  prep(a); prep(b);

  function playSafe(v) {
    const p = v.play();
    if (p && p.catch) p.catch(function () {});
  }

  function swapSoon() {
    if (!enabled || fading) return;
    const dur = active.duration;
    if (!isFinite(dur) || dur < 2) return;
    if (active.currentTime < dur - FADE_LEAD) return;

    fading = true;
    idle.currentTime = 0;
    playSafe(idle);
    idle.classList.add("is-active");
    active.classList.remove("is-active");

    window.setTimeout(function () {
      try { active.pause(); } catch (e) {}
      const tmp = active;
      active = idle;
      idle = tmp;
      fading = false;
    }, 1150);
  }

  function tick() {
    if (enabled) swapSoon();
    raf = requestAnimationFrame(tick);
  }

  function apply(on) {
    enabled = on;
    body.classList.toggle("nx-desktop-bg", on);
    if (btn) btn.setAttribute("aria-pressed", on ? "true" : "false");
    if (on) {
      a.classList.add("is-active");
      b.classList.remove("is-active");
      active = a; idle = b;
      playSafe(active);
      if (!raf) raf = requestAnimationFrame(tick);
    } else {
      try { a.pause(); b.pause(); } catch (e) {}
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
    }
    try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) {}
  }

  let on = true;
  try {
    if (localStorage.getItem(KEY) === "0") on = false;
  } catch (e) {}
  apply(on);

  if (btn) {
    btn.addEventListener("click", function () {
      apply(!body.classList.contains("nx-desktop-bg"));
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (!enabled) return;
    if (document.hidden) {
      try { a.pause(); b.pause(); } catch (e) {}
    } else {
      playSafe(active);
    }
  });
})();
