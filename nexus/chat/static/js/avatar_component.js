/* Original portraits with registered, perspective-aware facial animation. */
(function () {
  "use strict";
  const STATES = new Set(["idle", "listening", "thinking", "speaking"]);
  const VISEMES = new Set(["neutral", "AI", "E", "O", "U", "MBP", "FV"]);
  const SVG = "http://www.w3.org/2000/svg";
  const faces = new WeakMap(), audioGraphs = new WeakMap();
  const nodes = root => Array.from((root || document).querySelectorAll(".nx-partner[data-partner],[data-avatar][data-partner]"));
  let audioCtx = null, frame = 0, idleFrame = 0, serial = 0, active = null;

  function svg(tag, attrs, parent) {
    const el = document.createElementNS(SVG, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, String(v)));
    if (parent) parent.appendChild(el);
    return el;
  }
  function portrait(rig, parent, transform) {
    return svg("image", {href: rig.fallback, width: rig.canvas[0], height: rig.canvas[1],
      preserveAspectRatio: "xMidYMid meet", transform: transform || ""}, parent);
  }
  function build(el) {
    const rig = window.NEXUS_AVATAR_RIGS && window.NEXUS_AVATAR_RIGS[el.dataset.partner];
    const host = el.querySelector(".nx-holo-avatar");
    if (!rig || !host || faces.has(el)) return;
    const stage = document.createElement("span");
    stage.className = "nx-puppet-stage";
    const base = document.createElement("img");
    base.className = "nx-avatar-original";
    base.src = rig.fallback;
    base.alt = "";
    stage.appendChild(base);
    const overlay = svg("svg", {viewBox: "0 0 " + rig.size.join(" "),
      preserveAspectRatio: "xMidYMid meet", class: "nx-face-overlay", "aria-hidden": "true"}, stage);
    const defs = svg("defs", {}, overlay), id = "nx-face-" + (++serial);
    const eyes = rig.face.eyes.map((eye, i) => {
      const side = i ? "right" : "left", clipId = id + "-" + side;
      const clip = svg("clipPath", {id: clipId, clipPathUnits: "userSpaceOnUse"}, defs);
      svg("path", {d: eye.path}, clip);
      const position = "translate(" + eye.center.join(" ") + ") rotate(" + eye.angle + ")";
      const g = svg("g", {transform: position, "data-feature": "eye-" + side}, overlay);
      const clipped = svg("g", {"clip-path": "url(#" + clipId + ")"}, g);
      svg("path", {d: eye.path, fill: eye.fill}, clipped);
      const gaze = svg("g", {}, clipped);
      // Invert the feature frame to keep sampled pixels registered with the original.
      const inverse = "rotate(" + (-eye.angle) + ") translate(" + (-eye.center[0]) + " " + (-eye.center[1]) + ")";
      portrait(rig, gaze, inverse);
      const lidClipId = clipId + "-lid", lidClip = svg("clipPath", {id: lidClipId, clipPathUnits: "userSpaceOnUse"}, defs);
      const lidRect = svg("rect", {x: eye.left, y: eye.top, width: eye.width, height: 0}, lidClip);
      const lid = svg("g", {"clip-path": "url(#" + lidClipId + ")", "data-feature": "lid-" + side}, clipped);
      if (rig.face.mouth.kind === "visor") svg("path", {d: eye.path, fill: eye.fill}, lid);
      else portrait(rig, lid, inverse + " translate(0 " + eye.lidOffset + ")");
      return {eye, gaze, lidRect};
    });
    const m = rig.face.mouth;
    const mouth = svg("g", {transform: "translate(" + m.center.join(" ") + ") rotate(" + m.angle + ")",
      "data-feature": "mouth", opacity: 0}, overlay);
    if (m.kind === "drawn") svg("rect", {x: -48, y: -5, width: 96, height: 10, fill: "#cad3d7"}, mouth);
    const opening = svg("path", {fill: m.kind === "visor" ? "#9aeaff" : "#211d27"}, mouth);
    const rim = svg("path", {fill: "none", stroke: m.kind === "visor" ? "#289fe6" : m.kind === "lips" ? "#8c909e" : "#874c65",
      "stroke-width": m.kind === "visor" ? 1.5 : 2, "stroke-linecap": "round"}, mouth);
    stage.style.visibility = "hidden";
    host.appendChild(stage);
    faces.set(el, {rig, eyes, mouth, opening, rim});
    // The original fallback stays visible if the required portrait fails to load.
    const ready = () => {
      if (!base.naturalWidth) return;
      stage.style.visibility = "";
      host.dataset.rigMode = "registered";
      host.dataset.rigPartner = rig.partner;
    };
    base.addEventListener("load", ready, {once: true});
    if (base.complete) ready();
  }
  function visemePlan(text) {
    const s = String(text || "").toLowerCase();
    return Array.from(s).map((c, i) => {
      if (/[.!?,;:\-\s]/.test(c)) return "neutral";
      if (/^(pf|ph)/.test(s.slice(i, i + 2)) || /[fvw]/.test(c)) return "FV";
      if (/[bmp]/.test(c)) return "MBP";
      if (/[ou]/.test(c)) return c === "u" ? "U" : "O";
      if (/[aäiyj]/.test(c)) return "AI";
      return "E";
    });
  }
  function setState(partner, state, root) {
    if (!STATES.has(state)) state = "idle";
    nodes(root).forEach(el => {
      if (el.dataset.partner !== partner) return;
      el.dataset.avatarState = state;
      el.classList.toggle("nx-speaking", state === "speaking");
      el.setAttribute("aria-busy", String(state === "thinking" || state === "speaking"));
    });
  }
  function setExclusive(partner, state, root) {
    nodes(root).forEach(el => setState(el.dataset.partner, el.dataset.partner === partner ? state : "idle", root));
  }
  function setViseme(partner, name, weight, root) {
    name = VISEMES.has(name) ? name : "neutral";
    weight = Math.max(0, Math.min(1, Number(weight) || 0));
    nodes(root).forEach(el => {
      if (el.dataset.partner !== partner) return;
      el.dataset.viseme = name;
      el.style.setProperty("--nx-viseme-weight", weight.toFixed(3));
      const f = faces.get(el);
      if (!f) return;
      const m = f.rig.face.mouth, closed = name === "neutral" || name === "MBP" || weight < .035;
      f.mouth.setAttribute("opacity", closed ? "0" : "1");
      f.mouth.dataset.viseme = name;
      f.mouth.dataset.visemeWeight = weight.toFixed(3);
      if (closed) return; // Pauses show the exact original lips/visor.
      const widths = {AI: .9, E: 1, O: .55, U: .38, FV: .88};
      const heights = {AI: 1, E: .45, O: 1, U: .9, FV: .22};
      const x = m.width * (widths[name] || 1) / 2;
      const h = Math.max(1.5, m.height * (heights[name] || 1) * weight);
      if (m.kind === "lips") {
        // Follow the sloping original lip seam; opening stays inside the lip region.
        f.opening.setAttribute("d", `M ${-x} -1 Q ${-x * .3} -4 0 -1 Q ${x * .6} -1 ${x} 2 Q ${x * .5} ${h} 0 ${h * .82} Q ${-x * .5} ${h * .7} ${-x} -1 Z`);
        f.rim.setAttribute("d", `M ${-x * .8} ${h * .5} Q 0 ${h + 2} ${x * .9} ${h * .45}`);
      } else {
        f.opening.setAttribute("d", `M ${-x} 0 Q ${-x} ${-h / 2} 0 ${-h / 2} Q ${x} ${-h / 2} ${x} 0 Q ${x} ${h / 2} 0 ${h / 2} Q ${-x} ${h / 2} ${-x} 0 Z`);
        f.rim.setAttribute("d", `M ${-x} 0 Q 0 ${h * .7} ${x} 0`);
      }
    });
  }
  function renderEyes(el, x, y, blink) {
    const f = faces.get(el);
    if (!f) return;
    f.eyes.forEach(({eye, gaze, lidRect}) => {
      gaze.setAttribute("transform", `translate(${(x * eye.gaze).toFixed(3)} ${(y * eye.gaze * .4).toFixed(3)})`);
      lidRect.setAttribute("height", (eye.height * blink).toFixed(3));
    });
  }
  function resetMotion(root) {
    nodes(root).forEach(el => {
      ["--nx-mouth", "--nx-eye-x", "--nx-eye-y", "--nx-lid", "--nx-head-x", "--nx-head-y", "--nx-head-r", "--nx-shoulder", "--nx-breath", "--nx-holo-react", "--nx-viseme-weight"].forEach(v => el.style.setProperty(v, "0"));
      renderEyes(el, 0, 0, 0);
      setViseme(el.dataset.partner, "neutral", 0, root);
    });
  }
  function idle(root) {
    const t = performance.now() / 1000;
    nodes(root).forEach((el, i) => {
      const phase = i * 1.7, speaking = active && el.dataset.partner === active.partner;
      const x = Math.sin(t * .43 + phase) * (speaking ? .4 : 1);
      const y = Math.sin(t * .31 + phase) * .4;
      const cycle = (t + i * 1.13) % (4.8 + i * .37);
      const blink = cycle < .22 ? Math.sin(cycle / .22 * Math.PI) : 0;
      el.style.setProperty("--nx-eye-x", x.toFixed(3));
      el.style.setProperty("--nx-eye-y", y.toFixed(3));
      el.style.setProperty("--nx-lid", blink.toFixed(3));
      el.style.setProperty("--nx-head-r", (Math.sin(t * .19 + phase) * (speaking ? .16 : .08)).toFixed(3));
      el.style.setProperty("--nx-head-y", (Math.sin(t * .27 + phase) * .12).toFixed(3));
      renderEyes(el, x, y, blink);
    });
    idleFrame = requestAnimationFrame(() => idle(root));
  }
  function init(root) {
    nodes(root).forEach(el => {
      el.dataset.avatarState = el.dataset.avatarState || "idle";
      el.setAttribute("data-avatar-component", "nexus-registered-v3");
      build(el);
    });
    resetMotion(root);
    if (!idleFrame) idleFrame = requestAnimationFrame(() => idle(root));
  }
  function stopAudio(root) {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    if (active) active.cleanup();
    active = null;
    resetMotion(root);
    nodes(root).forEach(el => {
      if (el.dataset.avatarState === "speaking") setState(el.dataset.partner, "idle", root);
    });
  }
  function animate(session) {
    if (active !== session) return;
    const {audio, graph, partner, root, plan} = session;
    if (audio.ended) { stopAudio(root); return; }
    if (audio.paused) return;
    graph.analyser.getByteFrequencyData(graph.bins);
    const hi = Math.min(72, graph.bins.length);
    let sum = 0;
    for (let i = 2; i < hi; i++) sum += graph.bins[i];
    const level = hi > 2 ? sum / ((hi - 2) * 255) : 0;
    const weight = level < .045 ? 0 : Math.min(1, (level - .045) * 4.8);
    const duration = Number(audio.duration) || 0;
    const frac = duration > 0 ? Math.min(.999, audio.currentTime / duration) : 0;
    const name = weight > .035 && plan.length ? plan[Math.floor(frac * plan.length)] : "neutral";
    setViseme(partner, name, weight, root);
    nodes(root).forEach(el => {
      if (el.dataset.partner !== partner) return;
      el.style.setProperty("--nx-mouth", weight.toFixed(3));
      el.dataset.audioTime = audio.currentTime.toFixed(3);
      el.dataset.audioDuration = duration.toFixed(3);
      el.dataset.syncViseme = name;
    });
    frame = requestAnimationFrame(() => animate(session));
  }
  function bindAudio(audio, partner, root, text) {
    if (!audio || !partner) return false;
    stopAudio(root);
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return false;
      audioCtx = audioCtx || new Ctx();
      let graph = audioGraphs.get(audio);
      if (!graph) {
        const source = audioCtx.createMediaElementSource(audio), analyser = audioCtx.createAnalyser();
        analyser.fftSize = 512;
        analyser.smoothingTimeConstant = .58;
        source.connect(analyser);
        analyser.connect(audioCtx.destination);
        graph = {source, analyser, bins: new Uint8Array(analyser.frequencyBinCount)};
        audioGraphs.set(audio, graph);
      }
      const session = {audio, graph, partner, root, plan: visemePlan(text)};
      const start = () => {
        if (active !== session) return;
        setExclusive(partner, "speaking", root);
        if (audioCtx.state === "suspended") audioCtx.resume().catch(() => stopAudio(root));
        if (frame) cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => animate(session));
      };
      const finish = () => { if (active === session) stopAudio(root); };
      session.cleanup = () => {
        audio.removeEventListener("playing", start);
        ["ended", "pause", "error"].forEach(e => audio.removeEventListener(e, finish));
      };
      active = session;
      audio.addEventListener("playing", start);
      ["ended", "pause", "error"].forEach(e => audio.addEventListener(e, finish));
      if (!audio.paused && !audio.ended) start();
      return true;
    } catch (_) { stopAudio(root); return false; }
  }
  window.NEXUS_AVATARS = Object.freeze({states: Object.freeze(Array.from(STATES)),
    visemes: Object.freeze(Array.from(VISEMES)), init, setState, setExclusive, setViseme,
    bindAudio, stopAudio, resetMotion});
})();
