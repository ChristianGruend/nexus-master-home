/* NEXUS Live Avatar Engine v1 – WebGL Foto-Warp, kein ML zur Laufzeit, keine Bibliotheken.
 * API:
 *   const a = NXLive.mount(container, {id:'nexus'|'hermes'|'gpt'|'aether', src, landmarks})
 *   a.attachAudio(mediaElement|AudioNode)  -> echtes Lip-Sync per WebAudio-Analyser
 *   a.setWords([{t,d,w}], startCtxTime)    -> edge-tts WordBoundary (Gating + Form), optional
 *   a.speakWords(words, startPerfMs)       -> nur Timings (z.B. native App ohne Audio-Zugriff)
 *   a.setState('idle'|'listening'|'thinking'|'speaking'), a.setActive(0..1), a.lookAt(x,y), a.nod()
 *   NXLive.stage([a,b,c]).setSpeaker(id|null)
 */
(function (G) {
  'use strict';
  var PROFILES = {
    nexus:  { zoom: 1.0, power: 1.0, eyeBoost: 1.8, headSlow: 0.55, mouthIn: ['#dfe6ee', '#16263a', '#2c5677'], glow: [0.55, 0.85, 1.0], blink: [2.2, 5.0], dbl: 0.18, head: 1.0, headHz: 0.11, gaze: 1.0, smile: 0.35, jaw: 1.0,  brow: 1.0, voice: 'de-DE-AmalaNeural' },
    hermes: { zoom: 1.35, mouthIn: ['#dfe6ee', '#16263a', '#2c5677'], glow: [0.45, 0.78, 1.0], blink: [3.0, 6.0], dbl: 0.08, head: 0.7, headHz: 0.08, gaze: 0.7, smile: 0.15, jaw: 0.85, brow: 1.3, voice: 'de-DE-ConradNeural' },
    gpt:    { zoom: 1.35, mouthIn: ['#dfe6ee', '#16263a', '#2c5677'], glow: [0.62, 0.92, 1.0], blink: [2.0, 4.2], dbl: 0.25, head: 0.85, headHz: 0.14, gaze: 1.2, smile: 0.25, jaw: 0.9, brow: 0.6, voice: 'de-DE-KillianNeural' },
    aether: { zoom: 1.0, power: 0.75, eyeBoost: 1.6, mouthIn: ['#dfe6ee', '#16263a', '#2c5677'], glow: [0.58, 0.88, 1.0], blink: [1.8, 4.0], dbl: 0.22, head: 1.25, headHz: 0.15, gaze: 1.4, sacc: 0.6, smile: 0.5, jaw: 1.0, brow: 1.2, voice: 'de-DE-FlorianMultilingualNeural' }
  };
  var VS = 'attribute vec2 p;uniform vec4 uCrop;varying vec2 v;void main(){v=uCrop.xy+vec2(p.x*.5+.5,.5-p.y*.5)*uCrop.zw;gl_Position=vec4(p,0.,1.);}';
  var FS = [
    'precision mediump float;varying vec2 v;uniform sampler2D T;uniform float A,uT,uOpen,uWide,uRound,uFric,uBlink,uBrow,uSmile,uAct,uPulse,uScan,uBreath;',
    'uniform vec2 uEL,uER,uM,uChin,uGaze;uniform float uEW,uEH,uMW;uniform vec3 uHead,uGlow;',
    'vec2 toA(vec2 u){return vec2(u.x*A,u.y);} vec2 toU(vec2 a){return vec2(a.x/A,a.y);}',
    'float ell(vec2 d){return length(d);}',
    'void main(){',
    ' vec2 q=toA(v); vec2 piv=toA(uChin)+vec2(0.,uMW*.6);',
    // Kopf: Rotation/Verschiebung, unterhalb vom Kinn auslaufend; Atmen hebt den Oberkörper leicht
    ' float wh=1.-smoothstep(piv.y-.02,piv.y+uMW*2.2,q.y);',
    ' float ang=-uHead.x*wh; vec2 r=q-piv; r=vec2(cos(ang)*r.x-sin(ang)*r.y,sin(ang)*r.x+cos(ang)*r.y); vec2 h=piv+r-uHead.yz*wh;',
    ' h.y+=uBreath*(1.-wh)*.004*smoothstep(piv.y,1.,q.y)+uBreath*.002;',
    ' vec2 m=toA(uM); float mw=uMW; vec2 s=h;',
    // Kiefer
    ' float D=uOpen*mw*.34; float hw=mw*(.40+.10*uWide-.14*uRound);',
    ' float wx=1.-smoothstep(mw*.55,mw*1.5,abs(h.x-m.x)); float wy=smoothstep(m.y-mw*.01,m.y+mw*.03,h.y)*(1.-smoothstep(m.y+mw*1.2,m.y+mw*2.2,h.y));',
    ' s.y-=D*wx*wy;',
    ' float wu=(1.-smoothstep(mw*.4,mw*.9,abs(h.x-m.x)))*smoothstep(m.y-mw*.55,m.y-mw*.05,h.y)*(1.-step(m.y,h.y)); s.y+=D*.28*wu;',
    // Mundbreite (E breit, O rund)
    ' float wm=exp(-pow(length((h-m)/vec2(mw*.9,mw*.5)),2.)); s.x=m.x+(s.x-m.x)/(1.+(.18*uWide-.22*uRound)*wm);',
    // Lächeln (Mundwinkel)
    ' for(int i=0;i<2;i++){float sg=i==0?-1.:1.;vec2 c=m+vec2(sg*mw*.5,0.);float w=exp(-pow(length((h-c)/(mw*.32)),2.));s.y+=uSmile*mw*.07*w;s.x-=sg*uSmile*mw*.05*w;}',
    // Augen: Brauen, Blick, Lider
    ' float lidLine=0.; float lash=0.; vec2 eyeC[2]; eyeC[0]=toA(uEL); eyeC[1]=toA(uER);',
    ' for(int i=0;i<2;i++){vec2 e=eyeC[i];',
    '  vec2 bd=(h-(e-vec2(0.,uEH*2.2)))/vec2(uEW*1.1,uEH*1.6); float wb=exp(-dot(bd,bd)); s.y+=uBrow*uEW*.12*wb;',
    '  vec2 d=(h-e)/vec2(uEW*.56,uEH*.85); float r2=length(d); float inner=1.-smoothstep(.55,.95,r2); s-=uGaze*uEW*vec2(.16,.10)*inner;',
    '  float lid=-1.+2.15*uBlink; float inE=1.-smoothstep(.95,1.15,r2);',
    '  if(uBlink>.01&&d.y<lid&&inE>0.){float k=inE; s=mix(s,vec2(h.x,e.y-uEH*(1.05-.12*(d.y+1.))),k);}',
    '  lash=max(lash,inE*(1.-smoothstep(0.,.18,abs(d.y-lid)))*step(.02,uBlink));',
    ' }',
    ' vec3 col=texture2D(T,toU(s)).rgb;',
    ' col=mix(col,col*.35,lash*.8);',
    // Mundinnenraum + Zähne
    ' float top=m.y-D*.28,bot=m.y+D; if(D>.0005){vec2 md=vec2((h.x-m.x)/hw,(h.y-(top+bot)*.5)/max((bot-top)*.5,1e-4)); float mi=(1.-smoothstep(.55,1.,length(vec2(md.x,md.y*(1.+.6*md.x*md.x)))))*.92;',
    '  vec3 inside=mix(vec3(.09,.15,.23),vec3(.17,.34,.47),smoothstep(-.2,1.,md.y))*mix(1.,texture2D(T,toU(m)).r*1.4,.35);',
    '  float teeth=(1.-smoothstep(-.55,-.15,md.y))*(1.-smoothstep(.55,.85,abs(md.x)));',
    '  inside=mix(inside,vec3(.80,.82,.86),teeth*(.35+.45*uFric)); col=mix(col,inside,mi*clamp(D/(mw*.05),0.,1.));}',
    // pulsierende Augen
    ' for(int i=0;i<2;i++){vec2 e=eyeC[i]+uGaze*uEW*vec2(.16,.10); float g=exp(-pow(length(h-e)/(uEW*.16),2.)); float g2=exp(-pow(length(h-e)/(uEW*.55),2.));',
    '  col+=uGlow*(g*(.22+.30*uPulse)+g2*.06*uPulse)*(1.-uBlink);}',
    // Holo-Scan + Scanlines
    ' float sl=.5+.5*sin(v.y*900.); col+=uGlow*.025*sl*uScan; float band=exp(-pow((v.y-fract(uT*.12))*40.,2.)); col+=uGlow*.08*band*uScan;',
    ' float luma=dot(col,vec3(.299,.587,.114)); col=mix(vec3(luma)*vec3(.86,.93,1.),col,.35+.65*uAct)*(.62+.38*uAct)+uGlow*.05*uAct;',
    ' gl_FragColor=vec4(col,1.);}'
  ].join('\n');

  var ctxA = null; function audioCtx() { if (!ctxA) ctxA = new (G.AudioContext || G.webkitAudioContext)(); return ctxA; }
  function rnd(a, b) { return a + Math.random() * (b - a); }
  function damp(cur, tgt, k, dt) { return tgt + (cur - tgt) * Math.exp(-k * dt); }
  function vowelShape(ch) { ch = ch.toLowerCase(); if ('aä'.indexOf(ch) >= 0) return [1, .2, 0]; if ('eiyé'.indexOf(ch) >= 0) return [.55, 1, 0]; if ('oöuü'.indexOf(ch) >= 0) return [.7, 0, 1]; return null; }

  var all = [], raf = 0, lastT = 0, perf = { frames: 0, acc: 0, fps: 0, scale: 1, slow: 0, fast: 0 };
  var opts = { saver: /[?&]lite=1/.test(location.search) || (G.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches), maxDpr: 2, fpsCap: 60, renderer: /[?&]gl=1/.test(location.search) ? 'webgl' : 'canvas2d', budgetMs: 4 };
  if (G.NXLIVE_OPTS) Object.assign(opts, G.NXLIVE_OPTS);
  if (navigator.getBattery) navigator.getBattery().then(function (b) { function u() { if (b.level < .2 && !b.charging) opts.saver = true; } u(); b.addEventListener('levelchange', u); }).catch(function () {});

  function Avatar(el, o) {
    this.id = o.id; this.P = Object.assign({}, PROFILES[o.id] || PROFILES.nexus, o.profile || {}); this.L = o.landmarks; this.el = el;
    var c = this.cv = document.createElement('canvas'); c.className = 'nxl-canvas'; c.style.cssText = 'width:100%;height:100%;display:block'; el.appendChild(c);
    this.mode = o.renderer || opts.renderer; var gl = null;
    if (this.mode === 'webgl') { gl = c.getContext('webgl', { alpha: false, antialias: false, powerPreference: 'low-power', preserveDrawingBuffer: false }); if (!gl) this.mode = 'canvas2d'; }
    if (this.mode === 'canvas2d') this.g2 = c.getContext('2d', { alpha: false, desynchronized: true });
    this.gl = gl; this.ok = !!(gl || this.g2); this.ms = 0; this.st = { open: 0, wide: 0, round: 0, fric: 0, blink: 0, brow: 0, smile: 0, act: 1, pulse: 0, gx: 0, gy: 0, hx: 0, hy: 0, hr: 0 };
    this.tg = { gx: 0, gy: 0, act: 1, smile: 0, brow: 0 }; this.state = 'idle'; this.nextBlink = rnd(this.P.blink[0], this.P.blink[1]); this.blinkT = -1; this.dbl = false;
    this.stats = { blinks: 0, dbl: 0, sacc: 0, nods: 0 }; this.nextSacc = rnd(.4, 1.6); this.nodT = -1; this.t = Math.random() * 100; this.words = null; this.vis = true; this.level = 0; this.noise = 0.004;
    var self = this; this.img = new Image(); this.img.onload = function () { self._init(); }; this.img.src = o.src;
    if (G.IntersectionObserver) new IntersectionObserver(function (e) { self.vis = e[0].isIntersecting; }).observe(el);
    all.push(this); loop();
  }
  Avatar.prototype._init = function () {
    var gl = this.gl; if (!gl) { if (this.g2) { this._init2d(); return; } this.el.style.background = 'url(' + this.img.src + ') center/cover'; return; }
    function sh(t, s) { var x = gl.createShader(t); gl.shaderSource(x, s); gl.compileShader(x); if (!gl.getShaderParameter(x, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(x)); return x; }
    var pr = this.pr = gl.createProgram(); gl.attachShader(pr, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(pr); gl.useProgram(pr);
    var b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    var lp = gl.getAttribLocation(pr, 'p'); gl.enableVertexAttribArray(lp); gl.vertexAttribPointer(lp, 2, gl.FLOAT, false, 0, 0);
    var tx = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, tx); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, this.img);
    [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T].forEach(function (k) { gl.texParameteri(gl.TEXTURE_2D, k, gl.CLAMP_TO_EDGE); });
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    var U = this.U = {}; ['A', 'uT', 'uOpen', 'uWide', 'uRound', 'uFric', 'uBlink', 'uBrow', 'uSmile', 'uAct', 'uPulse', 'uScan', 'uBreath', 'uEL', 'uER', 'uM', 'uChin', 'uGaze', 'uEW', 'uEH', 'uMW', 'uHead', 'uGlow', 'uCrop'].forEach(function (n) { U[n] = gl.getUniformLocation(pr, n); });
    var L = this.L, A = this.img.width / this.img.height; this.A = A;
    var eh = Math.max(((L.lowerLid.L[1] - L.upperLid.L[1]) + (L.lowerLid.R[1] - L.upperLid.R[1])) * .5, (L.eyeLw + L.eyeRw) * .5 * A * .3);
    gl.uniform1f(U.A, A); gl.uniform2fv(U.uEL, L.eyeL); gl.uniform2fv(U.uER, L.eyeR); gl.uniform2fv(U.uM, L.mouth); gl.uniform2fv(U.uChin, L.chin);
    gl.uniform1f(U.uEW, (L.eyeLw + L.eyeRw) * .5 * A); gl.uniform1f(U.uEH, eh); gl.uniform1f(U.uMW, L.mouthW * A); gl.uniform3fv(U.uGlow, this.P.glow);
    this.ready = true; this.el.classList.add('nxl-ready');
  };
  Avatar.prototype.attachAudio = function (src) {
    var ac = audioCtx(); var node = src;
    if (src instanceof HTMLMediaElement) { if (!src._nxNode) { src._nxNode = ac.createMediaElementSource(src); src._nxNode.connect(ac.destination); } node = src._nxNode; }
    var an = ac.createAnalyser(); an.fftSize = 1024; an.smoothingTimeConstant = .3; node.connect(an);
    this.an = an; this.fbuf = new Float32Array(an.frequencyBinCount); this.tbuf = new Float32Array(an.fftSize); return an;
  };
  Avatar.prototype.feedLevels = function (o) { this.ext = o; this.extT = performance.now(); if (this.state !== 'speaking') this.setState('speaking'); };
  Avatar.prototype.detachAudio = function () { this.an = null; };
  Avatar.prototype.setWords = function (words, ctxStart) { this.words = words; this.wClock = function () { return audioCtx().currentTime - ctxStart; }; };
  Avatar.prototype.speakWords = function (words, perfStart) { this.words = words; this.textOnly = true; this.wClock = function () { return (performance.now() - perfStart) / 1000; }; this.setState('speaking'); };
  Avatar.prototype.stopSpeaking = function () { this.words = null; this.textOnly = false; this.an = null; this.setState('idle'); };
  Avatar.prototype.setState = function (s) { this.state = s; this.el.setAttribute('data-state', s); };
  Avatar.prototype.setActive = function (a) { this.tg.act = a; };
  Avatar.prototype.lookAt = function (x, y) { this.tg.gx = x; this.tg.gy = y || 0; this.lookHold = 1.5; };
  Avatar.prototype.nod = function () { if (this.nodT < 0) { this.nodT = 0; this.stats.nods++; } };
  Avatar.prototype._visemes = function (dt) {
    var o = 0, w = 0, r = 0, f = 0, gate = 1, wordShape = null;
    if (this.words) {
      var t = this.wClock(), cur = null, last = this.words[this.words.length - 1];
      for (var i = 0; i < this.words.length; i++) { var wd = this.words[i]; if (t >= wd.t - .03 && t <= wd.t + wd.d + .02) { cur = wd; break; } }
      gate = cur ? 1 : 0.15; this.inWord = !!cur;
      if (cur) { var vs = (cur.w.match(/[aeiouäöüy]/gi) || ['a']), k = Math.min(vs.length - 1, Math.floor((t - cur.t) / cur.d * vs.length)); wordShape = vowelShape(vs[Math.max(0, k)]);
        if (this.textOnly) { var ph = ((t - cur.t) / cur.d * vs.length) % 1; var env = Math.sin(Math.PI * Math.min(1, ph * 1.15)); o = .25 + .65 * env; w = wordShape[1] * env; r = wordShape[2] * env; } }
      if (last && t > last.t + last.d + .4) { if (this.textOnly) this.stopSpeaking(); else this.words = null; }
    }
    if (this.an) {
      this.an.getFloatTimeDomainData(this.tbuf); var s = 0; for (var j = 0; j < this.tbuf.length; j++) s += this.tbuf[j] * this.tbuf[j]; var rms = Math.sqrt(s / this.tbuf.length);
      this.an.getFloatFrequencyData(this.fbuf); var hz = audioCtx().sampleRate / this.an.fftSize, bands = [0, 0, 0, 0], edges = [[150, 900], [900, 2500], [2500, 4500], [4500, 9000]];
      for (var bi = 0; bi < 4; bi++) { var a0 = Math.round(edges[bi][0] / hz), a1 = Math.round(edges[bi][1] / hz), e = 0; for (var q = a0; q < a1; q++) e += Math.pow(10, this.fbuf[q] / 10); bands[bi] = e / (a1 - a0); }
      var tot = bands[0] + bands[1] + bands[2] + bands[3] + 1e-12; if (rms < this.noise * 3) this.noise = this.noise * .995 + rms * .005;
      var lvl = Math.max(0, Math.min(1, (rms - this.noise * 1.5) * 9)); this.level = lvl;
      var low = bands[0] / tot, mid = bands[1] / tot, hi = (bands[2] + bands[3]) / tot;
      o = lvl * (.45 + .55 * Math.min(1, low * 1.3)); w = Math.min(1, Math.max(0, (mid - .18) * 3)) * lvl; r = Math.min(1, Math.max(0, (low - .7) * 3.5)) * lvl * (1 - w);
      f = Math.min(1, Math.max(0, (hi - .12) * 4)) * lvl; o *= (1 - .55 * f);
      if (wordShape && lvl > .05) { w = w * .6 + wordShape[1] * .4 * lvl; r = r * .6 + wordShape[2] * .4 * lvl; }
      o *= gate;
    }
    if (!this.an && this.ext && performance.now() - this.extT < 200) { var x = this.ext, lv = Math.max(0, Math.min(1, x.level || 0)), sum = (x.low || 0) + (x.mid || 0) + (x.hi || 0) + 1e-9;
      this.level = lv; f = Math.min(1, Math.max(0, ((x.hi || 0) / sum - .15) * 4)) * lv; o = lv * (.45 + .55 * Math.min(1, (x.low || 0) / sum * 1.3)) * (1 - .55 * f); w = Math.min(1, Math.max(0, ((x.mid || 0) / sum - .18) * 3)) * lv; r = Math.min(1, Math.max(0, ((x.low || 0) / sum - .7) * 3.5)) * lv * (1 - w); }
    var st = this.st, atk = 28, rel = 14; st.open = damp(st.open, o * this.P.jaw, o > st.open ? atk : rel, dt); st.wide = damp(st.wide, w, 14, dt); st.round = damp(st.round, r, 14, dt); st.fric = damp(st.fric, f, 16, dt);
    return o;
  };
  Avatar.prototype._tick = function (dt, now) {
    var P = this.P, st = this.st, tg = this.tg; this.t += dt;
    var talk = this._visemes(dt); var speaking = this.state === 'speaking' || talk > .05;
    // Blinzeln
    this.nextBlink -= dt * (this.state === 'thinking' ? 1.4 : 1);
    if (this.blinkT < 0 && this.nextBlink <= 0) { this.blinkT = 0; this.stats.blinks++; this.dbl = Math.random() < P.dbl; if (this.dbl) this.stats.dbl++; this.nextBlink = rnd(P.blink[0], P.blink[1]); }
    if (this.blinkT >= 0) { this.blinkT += dt; var bt = this.blinkT / .16; st.blink = bt < .45 ? bt / .45 : Math.max(0, 1 - (bt - .45) / .55); if (bt >= 1) { this.blinkT = -1; st.blink = 0; if (this.dbl) { this.dbl = false; this.nextBlink = .12; } } }
    // Sakkaden
    this.lookHold = (this.lookHold || 0) - dt; this.nextSacc -= dt;
    if (this.nextSacc <= 0 && this.lookHold <= 0) { this.nextSacc = rnd(.6, speaking ? 1.4 : 2.6) * (P.sacc || 1); this.stats.sacc++; var gr = .55 * P.gaze; tg.gx = rnd(-gr, gr) * (speaking ? .5 : 1) + (this.bias || 0); tg.gy = rnd(-.25, .25); if (this.state === 'thinking') { tg.gx = .55 * P.gaze; tg.gy = -.45; } }
    st.gx = damp(st.gx, tg.gx, 35, dt); st.gy = damp(st.gy, tg.gy, 35, dt);
    // Mimik
    var pause = speaking && !this.inWord && this.words; tg.smile = (pause ? .55 : (this.state === 'listening' ? .3 : .12)) * P.smile * 2.2;
    tg.brow = (this.state === 'thinking' ? .7 : 0) + (this.an ? this.level * .5 : 0) * P.brow; st.smile = damp(st.smile, tg.smile, 3, dt); st.brow = damp(st.brow, tg.brow, 8, dt);
    // Kopf
    var hz = P.headHz * (P.headSlow || 1), A = P.head * (speaking ? 1.25 : 1) * (P.headSlow ? .8 : 1), tt = this.t;
    var nod = 0; if (this.nodT >= 0) { this.nodT += dt; nod = Math.sin(Math.min(1, this.nodT / .7) * Math.PI * 2) * .006; if (this.nodT > .7) this.nodT = -1; }
    st.hr = .022 * A * Math.sin(tt * hz * 6.283) + .01 * A * Math.sin(tt * hz * 2.7 * 6.283 + 1) + st.gx * .012;
    st.hx = .004 * A * Math.sin(tt * hz * 1.3 * 6.283 + 2) + st.gx * .004; st.hy = .003 * A * Math.sin(tt * hz * 2.1 * 6.283) + nod + (this.an ? this.level * .002 : 0);
    st.act = damp(st.act, tg.act, 5, dt); st.pulse = .5 + .5 * Math.sin(tt * (speaking ? 7 : 2.2)); if (speaking && this.an) st.pulse = Math.min(1, .3 + this.level * 1.2);
  };
  Avatar.prototype._draw = function () {
    if (!this.ready) return; var tq = performance.now(); var gl = this.gl, U = this.U, st = this.st, c = this.cv;
    var dpr = Math.min(G.devicePixelRatio || 1, opts.maxDpr) * perf.scale * (opts.saver ? .6 : 1); var w = Math.round(c.clientWidth * dpr), h = Math.round(c.clientHeight * dpr);
    if (!w || !h) return; if (c.width !== w || c.height !== h) { c.width = w; c.height = h; } gl.viewport(0, 0, w, h);
    gl.uniform1f(U.uT, this.t); gl.uniform1f(U.uOpen, st.open); gl.uniform1f(U.uWide, st.wide); gl.uniform1f(U.uRound, st.round); gl.uniform1f(U.uFric, st.fric);
    gl.uniform1f(U.uBlink, st.blink); gl.uniform1f(U.uBrow, st.brow); gl.uniform1f(U.uSmile, st.smile); gl.uniform1f(U.uAct, st.act); gl.uniform1f(U.uPulse, st.pulse);
    gl.uniform1f(U.uScan, opts.saver ? 0 : 1); gl.uniform1f(U.uBreath, Math.sin(this.t * 1.6)); gl.uniform2f(U.uGaze, st.gx, st.gy); gl.uniform3f(U.uHead, st.hr, st.hx, st.hy);
    var ca = w / h, A = this.A, z = this.zoom || this.P.zoom || 1, sx = Math.min(1, ca / A) / z, sy = Math.min(1, A / ca) / z, L = this.L;
    var fx = (L.eyeL[0] + L.eyeR[0] + L.mouth[0] * 2) / 4, fy = (L.eyeL[1] + L.eyeR[1]) / 2 * .55 + L.mouth[1] * .45;
    var cx = Math.max(sx / 2, Math.min(1 - sx / 2, fx)), cy = Math.max(sy / 2, Math.min(1 - sy / 2, fy + sy * .06));
    gl.uniform4f(U.uCrop, cx - sx / 2, cy - sy / 2, sx, sy);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    this.ms = this.ms * .9 + (performance.now() - tq) * .1;
  };

  // ---------- Canvas2D-Pfad (Standard; kein WebGL nötig): Foto-Ebenen per drawImage/clip/transform ----------
  Avatar.prototype._init2d = function () {
    var L = this.L, W = this.img.width, H = this.img.height; this.A = W / H;
    var eh = Math.max(((L.lowerLid.L[1] - L.upperLid.L[1]) + (L.lowerLid.R[1] - L.upperLid.R[1])) * .5 * H, (L.eyeLw + L.eyeRw) * .5 * W * .3);
    this.px = { W: W, H: H, eL: [L.eyeL[0] * W, L.eyeL[1] * H], eR: [L.eyeR[0] * W, L.eyeR[1] * H], ew: (L.eyeLw + L.eyeRw) * .5 * W, eh: eh,
      m: [L.mouth[0] * W, L.mouth[1] * H], mw: L.mouthW * W, chin: [L.chin[0] * W, L.chin[1] * H], oval: L.faceOval.map(function (p) { return [p[0] * W, p[1] * H]; }), crown: (L.crown || []).map(function (p) { return [p[0] * W, p[1] * H, p[2] * W]; }), fh: [L.forehead[0] * W, L.forehead[1] * H] };
    var g = this.P.glow; this.glowCss = 'rgba(' + Math.round(g[0] * 255) + ',' + Math.round(g[1] * 255) + ',' + Math.round(g[2] * 255) + ',';
    this.ready = true; this.el.classList.add('nxl-ready');
  };
  Avatar.prototype._mkSprites = function () {
    var P = this.px, g = this.glowCss, hc = [P.fh[0], P.fh[1] + (P.chin[1] - P.fh[1]) * .25], R = (P.chin[1] - P.fh[1]) * 1.25, S = 256, c = document.createElement('canvas'); c.width = c.height = S;
    var x = c.getContext('2d'), m = S / 2, k = m / 1.5; x.globalCompositeOperation = 'lighter';
    for (var i = 0; i < 14; i++) { var an = i / 14 * 6.283, wv = .05 + .02 * Math.sin(i * 1.7), al = .05 + .07 * (.6 + .4 * Math.sin(i * 2.3));
      var rg = x.createRadialGradient(m, m, k * .35, m, m, k * 1.5); rg.addColorStop(0, g + al.toFixed(3) + ')'); rg.addColorStop(1, g + '0)'); x.fillStyle = rg; x.beginPath(); x.moveTo(m, m); x.arc(m, m, k * 1.5, an - wv, an + wv); x.closePath(); x.fill(); }
    var au = x.createRadialGradient(m, m, k * .55, m, m, k * 1.05); au.addColorStop(0, g + '0)'); au.addColorStop(.6, g + '.16)'); au.addColorStop(1, g + '0)'); x.fillStyle = au; x.fillRect(0, 0, S, S);
    var gc = document.createElement('canvas'); gc.width = gc.height = 64; var y = gc.getContext('2d'), cg = y.createRadialGradient(32, 32, 0, 32, 32, 32);
    cg.addColorStop(0, 'rgba(235,250,255,1)'); cg.addColorStop(.3, g + '.6)'); cg.addColorStop(1, g + '0)'); y.fillStyle = cg; y.fillRect(0, 0, 64, 64);
    this.spr = { rays: c, glint: gc, hc: hc, R: R };
  };
  Avatar.prototype._draw2d = function () {
    var t0 = performance.now(), x = this.g2, c = this.cv, st = this.st, P = this.px, im = this.img;
    var dpr = Math.min(G.devicePixelRatio || 1, opts.maxDpr) * perf.scale * (opts.saver ? .6 : 1); var w = Math.round(c.clientWidth * dpr), h = Math.round(c.clientHeight * dpr);
    if (!w || !h) return; if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    var ca = w / h, A = this.A, z = this.zoom || this.P.zoom || 1, sx = Math.min(1, ca / A) / z, sy = Math.min(1, A / ca) / z, L = this.L;
    var fx = (L.eyeL[0] + L.eyeR[0] + L.mouth[0] * 2) / 4, fy = (L.eyeL[1] + L.eyeR[1]) / 2 * .55 + L.mouth[1] * .45;
    var cx = Math.max(sx / 2, Math.min(1 - sx / 2, fx)), cy = Math.max(sy / 2, Math.min(1 - sy / 2, fy + sy * .06));
    var k = w / (sx * P.W); // Bildpixel -> Canvaspixel
    x.setTransform(k, 0, 0, k, -(cx - sx / 2) * P.W * k, -(cy - sy / 2) * P.H * k);
    // Kopf: Drehung um Halspunkt + Verschiebung (ganzes Bild; Rand wird durch Zoom/Überzeichnung verdeckt)
    var pv = [P.chin[0], P.chin[1] + P.mw * .6]; x.translate(pv[0], pv[1]); x.rotate(st.hr); x.scale(1.012 + .002 * Math.sin(this.t * 1.6), 1.012 + .002 * Math.sin(this.t * 1.6)); x.translate(-pv[0] + st.hx * P.H, -pv[1] + st.hy * P.H);
    x.drawImage(im, 0, 0);
    var m = P.m, mw = P.mw, D = st.open * mw * .27, hw = mw * (.40 + .10 * st.wide - .14 * st.round);
    if (D > .4) {
      // Mundinnenraum, dann Unterkiefer-Ebene (Streifen unterhalb der Lippenlinie innerhalb der Gesichtskontur) nach unten
      var top = m[1] - D * .12, bot = m[1] + D;
      x.save(); x.beginPath(); x.ellipse(m[0], (top + bot) / 2, hw, Math.max(1, (bot - top) / 2), 0, 0, 6.283); x.clip();
      var gr = x.createLinearGradient(0, top, 0, bot); var mc = this.P.mouthIn || ['#d2d4da', '#4a2a2e', '#6a3a3c']; gr.addColorStop(0, mc[0]); gr.addColorStop(.22 + .1 * st.fric, mc[1]); gr.addColorStop(1, mc[2]); x.fillStyle = gr; x.fillRect(m[0] - hw, top, hw * 2, bot - top); x.restore();
      x.save(); x.beginPath(); var ov = P.oval; x.moveTo(ov[0][0], ov[0][1]); for (var i = 1; i < ov.length; i++) x.lineTo(ov[i][0], ov[i][1] + (ov[i][1] > m[1] ? D : 0)); x.closePath(); x.clip();
      var sx0 = m[0] - mw * 1.6, sw = mw * 3.2, sh = P.chin[1] - m[1] + mw * .5, n = 6;
      // Unterkiefer gestaucht statt verschoben: oben +D, unten fest -> keine Naht; seitlich in Streifen auslaufend
      for (var b = 0; b < n; b++) { var u = (b + .5) / n * 2 - 1, f2 = Math.max(0, 1 - Math.pow(Math.abs(u), 2.2)), Db = D * f2, bx = sx0 + sw * b / n;
        x.drawImage(im, bx, m[1], sw / n + .6, sh, bx, m[1] + Db, sw / n + .6, sh - Db); }
      x.restore();
    }
    // Augen: Blick (Iris-Ebene verschieben) + Lider (Haut oberhalb über das Auge ziehen)
    var ew = P.ew, eh = P.eh, eyes = [P.eL, P.eR];
    for (var j = 0; j < 2; j++) { var e = eyes[j], rx = ew * .5, ry = eh * .8;
      if (Math.abs(st.gx) + Math.abs(st.gy) > .02) { x.save(); x.beginPath(); x.ellipse(e[0], e[1], rx * .9, ry * .85, 0, 0, 6.283); x.clip(); x.drawImage(im, e[0] - rx, e[1] - ry, rx * 2, ry * 2, e[0] - rx + st.gx * ew * .16, e[1] - ry + st.gy * ew * .10, rx * 2, ry * 2); x.restore(); }
      if (st.blink > .02) { var lid = e[1] - ry + 2 * ry * Math.min(1, st.blink * 1.08); x.save(); x.beginPath(); x.ellipse(e[0], e[1], rx * 1.08, ry * 1.1, 0, 0, 6.283); x.clip();
        x.drawImage(im, e[0] - rx * 1.1, e[1] - eh * 1.25, rx * 2.2, eh * .45, e[0] - rx * 1.1, e[1] - ry * 1.1, rx * 2.2, lid - (e[1] - ry * 1.1));
        x.fillStyle = 'rgba(20,20,30,.55)'; x.fillRect(e[0] - rx * 1.1, lid - eh * .06, rx * 2.2, eh * .12); x.restore(); }
    }
    // Leuchten der Augen (additiv)
    x.globalCompositeOperation = 'lighter'; var spk = this.state === 'speaking' ? 1 : 0, gp = (.22 + .30 * st.pulse) * (1 - st.blink) * (1 + ((this.P.eyeBoost || 1) - 1) * spk * (.5 + .5 * this.level));
    for (j = 0; j < 2; j++) { var ec = [eyes[j][0] + st.gx * ew * .16, eyes[j][1] + st.gy * ew * .10], R = ew * .5, rg = x.createRadialGradient(ec[0], ec[1], 0, ec[0], ec[1], R);
      rg.addColorStop(0, this.glowCss + gp.toFixed(3) + ')'); rg.addColorStop(.35, this.glowCss + (gp * .25).toFixed(3) + ')'); rg.addColorStop(1, this.glowCss + '0)'); x.fillStyle = rg; x.fillRect(ec[0] - R, ec[1] - R, R * 2, R * 2); }

    // ---- Macht-Effekte (NEXUS/AETHER): vorgerenderte Sprites (Aura-Strahlen, Kristall-Glanz) -> 1 drawImage je Effekt ----
    var pw = this.P.power || 0;
    if (pw && !opts.saver) {
      if (!this.spr) this._mkSprites();
      var lv = spk ? (.5 + .5 * this.level) : .25, hc = this.spr.hc, Rr = this.spr.R;
      x.save(); x.beginPath(); x.rect(hc[0] - Rr * 1.6, hc[1] - Rr * 1.6, Rr * 3.2, Rr * 3.2); var ov2 = P.oval; x.moveTo(ov2[0][0], ov2[0][1]); for (var oi = ov2.length - 1; oi >= 0; oi--) x.lineTo(ov2[oi][0], ov2[oi][1]); x.closePath(); x.clip('evenodd');
      x.globalAlpha = Math.min(1, pw * (.55 + .45 * lv) * (.85 + .15 * Math.sin(this.t * 1.3)));
      x.translate(hc[0], hc[1]); x.rotate(this.t * .05 * (this.P.headSlow ? .6 : 1)); x.drawImage(this.spr.rays, -Rr * 1.5, -Rr * 1.5, Rr * 3, Rr * 3); x.restore();
      for (var ci = 0; ci < P.crown.length; ci++) { var cr = P.crown[ci], cp = .5 + .5 * Math.sin(this.t * (this.P.headSlow ? 1.6 : 2.4) + ci * 1.3), cR = cr[2] * 1.8 + P.W * .01;
        x.globalAlpha = Math.min(1, pw * (.18 + .34 * cp) * (.7 + .5 * lv) * 1.6); x.drawImage(this.spr.glint, cr[0] - cR, cr[1] - cR, cR * 2, cR * 2); }
      x.globalAlpha = 1;
    }
    x.setTransform(1, 0, 0, 1, 0, 0);
    if (!opts.saver) { var by = ((this.t * .12) % 1) * h; var bg = x.createLinearGradient(0, by - h * .03, 0, by + h * .03); bg.addColorStop(0, this.glowCss + '0)'); bg.addColorStop(.5, this.glowCss + '.10)'); bg.addColorStop(1, this.glowCss + '0)'); x.fillStyle = bg; x.fillRect(0, by - h * .03, w, h * .06); }
    x.globalCompositeOperation = 'source-over';
    if (st.act < .98) { x.fillStyle = 'rgba(214,226,238,' + ((1 - st.act) * .9).toFixed(3) + ')'; x.fillRect(0, 0, w, h); }
    this.ms = this.ms * .9 + (performance.now() - t0) * .1;
  };
  function loop() { if (!raf) raf = requestAnimationFrame(frame); }
  function frame(now) {
    raf = 0; if (document.hidden) { lastT = 0; return; }
    var dt = lastT ? Math.min(.1, (now - lastT) / 1000) : 1 / 60; var cap = opts.saver ? 30 : opts.fpsCap;
    if (lastT && now - lastT < 1000 / cap - 2) { raf = requestAnimationFrame(frame); return; } lastT = now;
    perf.frames++; perf.acc += dt; if (perf.acc >= 1) { perf.fps = perf.frames / perf.acc; perf.frames = 0; perf.acc = 0;
      var tot = 0; for (var q = 0; q < all.length; q++) tot += all[q].ms; perf.ms = tot; if (tot > opts.budgetMs) { perf.over = (perf.over || 0) + 1; if (perf.over >= 2) { if (perf.scale > .5) perf.scale = Math.max(.5, perf.scale - .15); else opts.saver = true; perf.over = 0; } } else perf.over = 0;
      if (perf.fps < cap * .8) { perf.slow++; perf.fast = 0; if (perf.slow >= 2 && perf.scale > .5) { perf.scale = Math.max(.5, perf.scale - .15); perf.slow = 0; } }
      else { perf.fast++; perf.slow = 0; if (perf.fast >= 5 && perf.scale < 1) { perf.scale = Math.min(1, perf.scale + .1); perf.fast = 0; } } }
    for (var i = 0; i < all.length; i++) { var a = all[i]; a._tick(dt, now); if (a.vis) { if (a.g2) { if (a.ready) a._draw2d(); } else a._draw(); } }
    raf = requestAnimationFrame(frame);
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) loop(); });

  function Stage(list) { this.list = list; this.speaker = null; var self = this;
    setInterval(function () { var s = self.byId(self.speaker); if (!s) return; self.list.forEach(function (a) { if (a !== s && s.words && !s.inWord && Math.random() < .25) a.nod(); }); }, 900); }
  Stage.prototype.byId = function (id) { return this.list.filter(function (a) { return a.id === id; })[0]; };
  Stage.prototype.setSpeaker = function (id) {
    this.speaker = id; var L = this.list, si = L.indexOf(this.byId(id));
    L.forEach(function (a, i) { var act = id == null || i === si; a.setActive(act ? 1 : .55); a.el.classList.toggle('nxl-active', !!(id && i === si)); a.el.classList.toggle('nxl-listener', !!(id && i !== si));
      if (!act) { a.bias = (si > i ? .45 : -.45); a.lookAt(a.bias, .05); a.setState('listening'); } else { a.bias = 0; if (id) a.setState('speaking'); } });
  };
  function glSelfTest() { try { var c = document.createElement('canvas'); c.width = c.height = 64; var gl = c.getContext('webgl', { preserveDrawingBuffer: true }); if (!gl) return false;
    var t = performance.now(); for (var i = 0; i < 30; i++) { gl.clearColor(i / 30, .5, .5, 1); gl.clear(gl.COLOR_BUFFER_BIT); } var px = new Uint8Array(4); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    var ok = gl.getError() === 0 && px[1] > 100 && performance.now() - t < 50; var ext = gl.getExtension('WEBGL_lose_context'); if (ext) ext.loseContext(); return ok; } catch (e) { return false; } }
  G.NXLive = { glSelfTest: glSelfTest, mount: function (el, o) { return new Avatar(el, o); }, stage: function (l) { return new Stage(l); }, perf: perf, opts: opts, audioCtx: audioCtx, PROFILES: PROFILES };
})(window);
