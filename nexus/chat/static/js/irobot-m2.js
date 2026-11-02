/* NEXUS I-Robot v8 – Enhancer für /m2. Fügt NUR Optik hinzu (Erde, Ringe, Partikel, Parallax, Agentenleiste, Boot, Offline-Banner).
   Alle Aktionen laufen weiter über die vorhandenen m2.js-Handler (data-go / data-agent / #chatTarget change). Keine eigenen Daten. */
(function(){"use strict";const $=s=>document.querySelector(s),NX=window.NXR;if(!NX)return;
NX.earthTex='/static/assets/irobot/earth_bm.webp';NX.noGL=true;/* 1.7.1: CSS-Kugel statt WebGL (Freeze-Risiko Mali/MIUI) */
const A='/static/assets/irobot/',FACE='/static/assets/avatar/nexus.webp';
document.body.insertAdjacentHTML('afterbegin','<div class="nx-bg"></div><canvas id="nxParticles"></canvas><div class="nx-boot" id="nxBoot"><div><img src="'+FACE+'" alt=""><b>NEXUS</b></div></div>');
const hero=$('.hero');if(hero){hero.insertAdjacentHTML('beforeend','<div class="nx-scan"></div><div class="nx-base"></div><canvas class="nx-earth" id="nxEarth"></canvas><div class="nx-holo"><i></i><i></i><i></i></div><span class="nx-credit">Erde: NASA Blue Marble NG</span>');}
const top=$('.top');if(top)top.insertAdjacentHTML('afterend','<div class="nx-offline" id="nxOff">Offline – NEXUS auf WoZiPC nicht erreichbar. Gespeicherte Ansicht bleibt nutzbar.</div>');
/* Agentenwahl: echte Ziele aus #chatTarget (keine erfundenen Agenten) */
const sel=$('#chatTarget');if(sel){const IMG={nexus:FACE,hermes:'/static/assets/avatar/hermes_card.webp',aether:'/static/assets/avatar/aether_card.webp',gpt:'/static/assets/avatar/gpt_card.webp'};const bar=document.createElement('div');bar.className='nx-agentbar';
 [...sel.options].forEach(o=>{const b=document.createElement('button');b.type='button';b.dataset.t=o.value;b.innerHTML=(IMG[o.value]?'<img src="'+IMG[o.value]+'" alt="">':'')+o.textContent;b.onclick=()=>{sel.value=o.value;sel.dispatchEvent(new Event('change'));sync();};bar.appendChild(b);});
 const vh=sel.closest('.vhead');vh&&vh.insertAdjacentElement('afterend',bar);const sync=()=>bar.querySelectorAll('button').forEach(b=>b.classList.toggle('on',b.dataset.t===sel.value));sel.addEventListener('change',sync);
 new MutationObserver(sync).observe($('#chatTitle')||sel,{childList:true,characterData:true,subtree:true});sync();}
/* Offline: aus dem echten Health-Ergebnis von m2.js (#health.bad) bzw. navigator.onLine */
const h=$('#health');const off=()=>NX.setOffline(!navigator.onLine||(h&&h.classList.contains('bad')));if(h)new MutationObserver(off).observe(h,{attributes:true,childList:true,characterData:true,subtree:true});addEventListener('online',off);addEventListener('offline',off);
/* Nav-Markierung */
const nav=()=>{const v=document.querySelector('.view.active');document.querySelectorAll('.nav [data-go]').forEach(b=>b.classList.toggle('on',v&&b.dataset.go===v.id));};
document.querySelectorAll('.view').forEach(v=>new MutationObserver(nav).observe(v,{attributes:true,attributeFilter:['class']}));nav();
NX.evalLite().then(()=>{NX.parallax();const e=$('#nxEarth');if(e)NX.earth(e);NX.tilt('.agent,.w');});
addEventListener('load',()=>setTimeout(()=>{const b=$('#nxBoot');b&&b.classList.add('done');setTimeout(()=>b&&b.remove(),700);},350));
})();
