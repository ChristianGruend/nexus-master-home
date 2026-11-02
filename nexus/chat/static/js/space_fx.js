// Lightweight star field — canvas optional enhancement
(function () {
  const host = document.getElementById("nxStars");
  if (!host) return;
  const c = document.createElement("canvas");
  c.style.cssText = "position:absolute;inset:0;width:100%;height:100%;opacity:.28";
  host.appendChild(c);
  const ctx = c.getContext("2d");
  let w, h, stars;
  function resize() {
    w = c.width = host.clientWidth;
    h = c.height = host.clientHeight;
    stars = Array.from({ length: 80 }, () => ({
      x: Math.random() * w,
      y: Math.random() * h,
      r: Math.random() * 1.4 + 0.2,
      v: Math.random() * 0.25 + 0.05,
    }));
  }
  function tick() {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(70,115,160,0.85)";
    for (const s of stars) {
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fill();
      s.y += s.v;
      if (s.y > h) { s.y = 0; s.x = Math.random() * w; }
    }
    requestAnimationFrame(tick);
  }
  resize();
  window.addEventListener("resize", resize);
  tick();
})();
