/* NEXUS I-Robot Runtime v0.7 – Parallax (Gyro + Fallback), 3D-Tilt, Partikel, Holo-Erde, Holo-Wellen, Audio-Pegel, Sparmodus, Pause im Hintergrund. */
(function(w,d){"use strict";const NX=w.NXR={},R=d.documentElement,loops=new Set();let raf=0,paused=false;
function run(t){raf=0;if(paused)return;for(const f of loops)f(t);if(loops.size)raf=requestAnimationFrame(run);}
NX.loop=f=>{loops.add(f);if(!raf&&!paused)raf=requestAnimationFrame(run);return()=>loops.delete(f);};
d.addEventListener('visibilitychange',()=>{paused=d.hidden;R.classList.toggle('nx-paused',paused);if(!paused&&loops.size&&!raf)raf=requestAnimationFrame(run);});
NX.evalLite=async function(){let why=[];
 if(w.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches)why.push('reduced-motion');
 if((navigator.hardwareConcurrency||8)<=4)why.push('cpu');if((navigator.deviceMemory||8)<=3)why.push('ram');
 if(navigator.connection&&navigator.connection.saveData)why.push('save-data');
 try{if(navigator.getBattery){const b=await navigator.getBattery();if(!b.charging&&b.level<.2)why.push('akku');}}catch(e){}
 const u=localStorage.getItem('nx.lite');if(u==='1')why.push('user');if(u==='0')why=[];
 NX.lite=why.length>0;R.classList.toggle('nx-lite',NX.lite);NX.liteReason=why.join(',');return NX.liteReason;};
/* Parallax: Gyroskop, sonst Zeiger, sonst sanfte Eigenbewegung */
NX.parallax=function(){let tx=0,ty=0,gx=null,gy=null,src='auto';
 w.addEventListener('deviceorientation',e=>{if(e.gamma==null)return;src='gyro';gx=Math.max(-1,Math.min(1,e.gamma/30));gy=Math.max(-1,Math.min(1,(e.beta-40)/30));},{passive:true});
 w.addEventListener('pointermove',e=>{if(src==='gyro')return;src='ptr';gx=e.clientX/innerWidth*2-1;gy=e.clientY/innerHeight*2-1;},{passive:true});
 NX.loop(t=>{if(NX.lite)return;const X=gx==null?Math.sin(t/3100)*.5:gx,Y=gy==null?Math.cos(t/3700)*.4:gy;tx+=(X-tx)*.08;ty+=(Y-ty)*.08;R.style.setProperty('--tx',tx.toFixed(3));R.style.setProperty('--ty',ty.toFixed(3));
  d.querySelectorAll('.nx-tilt').forEach(el=>{if(!el._p)el.style.transform='rotateX('+(-ty*5).toFixed(2)+'deg) rotateY('+(tx*6).toFixed(2)+'deg)';});});};
/* Touch-Tilt pro Karte */
NX.tilt=function(sel){d.querySelectorAll(sel).forEach(el=>{el.classList.add('nx-tilt');
 el.addEventListener('pointermove',e=>{const r=el.getBoundingClientRect(),x=(e.clientX-r.left)/r.width*2-1,y=(e.clientY-r.top)/r.height*2-1;el._p=1;el.style.transform='rotateX('+(-y*9)+'deg) rotateY('+(x*10)+'deg) translateZ(8px)';});
 el.addEventListener('pointerleave',()=>{el._p=0;});});};
/* Partikel (leichte Eisblau-Punkte, steigen auf) */
NX.particles=function(cv){if(NX.lite)return;const c=cv.getContext('2d'),P=[];const dp=Math.min(2,w.devicePixelRatio||1);
 function sz(){cv.width=innerWidth*dp;cv.height=innerHeight*dp;}sz();w.addEventListener('resize',sz);
 for(let i=0;i<46;i++)P.push({x:Math.random(),y:Math.random(),r:.6+Math.random()*1.8,v:.00012+Math.random()*.0004,a:Math.random()*6});
 NX.loop(t=>{c.clearRect(0,0,cv.width,cv.height);for(const p of P){p.y-=p.v*16;if(p.y<-.02){p.y=1.02;p.x=Math.random();}
  const al=.25+.35*Math.sin(t/900+p.a);c.fillStyle='rgba(79,195,255,'+al+')';c.beginPath();c.arc(p.x*cv.width,p.y*cv.height,p.r*dp,0,7);c.fill();}});};
/* Holo-Erde v7b: NASA Blue Marble NG (gemeinfrei) als echte Kugel. WebGL-Shader (Sphere-Mapping, Eisblau-Atmosphaere, Terminator),
   Fallback: CSS-Kugel mit laufender Textur. Sparmodus: statisch. */
NX.earth=function(cv,tex){tex=tex||NX.earthTex||'assets/earth_bm.webp';const dp=Math.min(2,w.devicePixelRatio||1),S=Math.round(cv.clientWidth*dp);cv.width=cv.height=S;
 const gl=!NX.lite&&!NX.noGL&&cv.getContext('webgl',{premultipliedAlpha:true,alpha:true,antialias:true});
 if(!gl){const f=d.createElement('div');f.className='nx-earth-css'+(NX.lite?' static':'');f.style.backgroundImage='url('+(NX.lite?tex.replace('.webp','_static.webp'):tex)+')';cv.replaceWith(f);return;}
 const vs='attribute vec2 p;varying vec2 v;void main(){v=p;gl_Position=vec4(p,0.,1.);}',
 fs='precision mediump float;varying vec2 v;uniform sampler2D t;uniform float r;void main(){float R=.78;vec2 q=v/R;float d=dot(q,q);'+
 'float glow=smoothstep(1.32,1.0,length(v)/R)*(1.-smoothstep(.98,1.0,sqrt(d)))*0.;'+
 'if(d>1.){float l=length(v)/R;float a=smoothstep(1.28,1.0,l);gl_FragColor=vec4(vec3(.31,.76,1.)*a*.85,a*.85);return;}'+
 'float z=sqrt(1.-d);vec3 n=vec3(q.x,q.y,z);float tl=.38;vec3 m=vec3(n.x,n.y*cos(tl)-n.z*sin(tl),n.y*sin(tl)+n.z*cos(tl));'+
 'float lon=atan(m.x,m.z)+r;float lat=asin(clamp(m.y,-1.,1.));vec2 uv=vec2(fract(lon/6.28318+.5),.5-lat/3.14159);vec3 c=texture2D(t,uv).rgb;'+
 'vec3 L=normalize(vec3(-.55,.45,.75));float dif=clamp(dot(n,L)*1.15+.12,0.,1.);float term=smoothstep(-.05,.25,dot(n,L));'+
 'c=c*(.62+.95*dif)+vec3(.04,.10,.20)*(1.-dot(c,vec3(.33)));c=mix(c*vec3(.55,.7,.95),c,term);float rim=pow(1.-z,2.2);c=mix(c,vec3(.55,.85,1.),rim*.75);'+
 'float spec=pow(max(dot(reflect(-L,n),vec3(0,0,1)),0.),24.)*.35;gl_FragColor=vec4(c+spec,1.);}';
 const sh=(ty,src)=>{const x=gl.createShader(ty);gl.shaderSource(x,src);gl.compileShader(x);return x;},pr=gl.createProgram();
 gl.attachShader(pr,sh(gl.VERTEX_SHADER,vs));gl.attachShader(pr,sh(gl.FRAGMENT_SHADER,fs));gl.linkProgram(pr);
 if(!gl.getProgramParameter(pr,gl.LINK_STATUS)){NX.lite=true;return NX.earth(cv,tex);}gl.useProgram(pr);
 const bf=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,bf);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW);
 const pl=gl.getAttribLocation(pr,'p');gl.enableVertexAttribArray(pl);gl.vertexAttribPointer(pl,2,gl.FLOAT,false,0,0);const ur=gl.getUniformLocation(pr,'r');
 const tx=gl.createTexture(),img=new Image();gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);
 img.onload=()=>{gl.bindTexture(gl.TEXTURE_2D,tx);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB,gl.RGB,gl.UNSIGNED_BYTE,img);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
  NX.loop(t=>{gl.viewport(0,0,S,S);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);gl.uniform1f(ur,t/14000);gl.drawArrays(gl.TRIANGLE_STRIP,0,4);});};img.src=tex;};
/* Audio-Pegel -> --lvl am Avatar + Holo-Wellen. Ohne echte Audioquelle bleibt alles ruhig. */
NX.voice=function(avEl,waveCv){let an=null,lv=0,fd=null;const c=waveCv&&waveCv.getContext('2d'),dp=Math.min(2,w.devicePixelRatio||1);
 if(waveCv){waveCv.width=waveCv.clientWidth*dp;waveCv.height=waveCv.clientHeight*dp;}
 function draw(t){if(an){const a=new Uint8Array(an.fftSize);an.getByteTimeDomainData(a);let m=0;for(const v of a){const x=(v-128)/128;m+=x*x;}const tg=Math.min(1,Math.sqrt(m/a.length)*4);lv+=(tg-lv)*.35;}else lv*=.9;
  avEl.style.setProperty('--lvl',lv.toFixed(3));if(!c)return;const W=waveCv.width,H=waveCv.height;c.clearRect(0,0,W,H);
  for(let k=0;k<4;k++){c.beginPath();const amp=H*(.04+lv*.42)*(1-k*.2),ph=t/(260+k*90)+k;
   for(let x=0;x<=W;x+=4*dp){const e=Math.sin(Math.PI*x/W);c.lineTo(x,H/2+Math.sin(x/(W/(3+k))*Math.PI*2+ph)*amp*e);}
   c.strokeStyle='rgba('+(k%2?'31,134,255':'79,195,255')+','+(.85-k*.17)+')';c.lineWidth=(2.6-k*.4)*dp;c.shadowColor='#4fc3ff';c.shadowBlur=12*dp;c.stroke();}c.shadowBlur=0;}
 NX.loop(draw);
 const api={attachAudioEl(el){const ac=new(w.AudioContext||w.webkitAudioContext)(),s=ac.createMediaElementSource(el);an=ac.createAnalyser();an.fftSize=512;s.connect(an);an.connect(ac.destination);return ac;},
  attachStream(st){const ac=new(w.AudioContext||w.webkitAudioContext)(),s=ac.createMediaStreamSource(st);an=ac.createAnalyser();an.fftSize=512;s.connect(an);return ac;},
  async listenMic(){return api.attachStream(await navigator.mediaDevices.getUserMedia({audio:true}));},stop(){an=null;}};return api;};
NX.setOffline=o=>R.classList.toggle('nx-is-offline',!!o);w.addEventListener('offline',()=>NX.setOffline(true));
NX.trackFetch=function(m){if(NX._tf)return;NX._tf=1;const of=w.fetch.bind(w);w.fetch=(u,o)=>{const p=of(u,o);if(m(String(u&&u.url||u)))p.then(()=>NX.setOffline(false),()=>NX.setOffline(true));return p;};};
NX.evalLite();})(window,document);
