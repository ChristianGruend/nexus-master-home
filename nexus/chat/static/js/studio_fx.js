/* NEXUS Studio FX UI (ST02-ST04, 20261028): Filter-Galerie mit Live-Vorschau, Farbregler + Histogramm, Zitat-/Social-Generator.
   Nutzt globale Helfer aus studio.html ($, api, S, log, need, refreshSel). Speichern = neue Version, Original bleibt. */
(function(){
const FX={filter:null,adj:{},t:null};
const host=document.querySelector("#panel");
const sec=document.createElement("section");sec.className="pan";sec.id="fx";
sec.innerHTML=`<h2>Filter &amp; Farbe <span class="sub">Live-Vorschau rechts im Nachher-Bild &ndash; Speichern legt eine neue Version an</span></h2>
<div id="fxFilters" style="display:flex;flex-wrap:wrap;gap:6px;margin:8px 0"></div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px"><div id="fxSliders"></div>
<div><canvas id="fxHist" width="256" height="90" style="width:100%;height:90px;background:rgba(0,0,0,.25);border-radius:10px"></canvas>
<div style="display:flex;gap:6px;margin-top:8px"><button class="btn" id="fxSave">Als neue Version speichern</button><button class="btn g" id="fxReset">Zur&uuml;cksetzen</button></div></div></div>
<h2 style="margin-top:16px">Zitat- &amp; Social-Generator</h2>
<div style="display:grid;grid-template-columns:1fr 220px;gap:12px"><div>
<textarea id="soText" rows="3" style="width:100%;background:rgba(255,255,255,.06);color:inherit;border:1px solid rgba(255,255,255,.15);border-radius:10px;padding:8px">Gib niemals auf &ndash; jeder Tag ist ein neuer Anfang.</textarea>
<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px"><select id="soFmt"></select><select id="soTpl"></select><select id="soFont"></select>
<select id="soPos"><option value="center">Mitte</option><option value="top">Oben</option><option value="bottom">Unten</option></select>
<input id="soAuthor" placeholder="Autor" style="width:110px"><input id="soWm" placeholder="Wasserzeichen" style="width:120px">
<label class="sub"><input type="checkbox" id="soBg" checked> gew&auml;hltes Foto als Hintergrund</label></div>
<div style="display:flex;gap:6px;margin-top:8px"><button class="btn g" id="soPrev">Vorschau</button><button class="btn" id="soMake">Erstellen &amp; speichern</button></div></div>
<img id="soImg" alt="" style="width:220px;border-radius:12px;background:rgba(0,0,0,.25);min-height:120px"></div>`;
host.parentNode.insertBefore(sec,host);
const labels={exposure:"Belichtung",contrast:"Kontrast",saturation:"S\u00e4ttigung",temperature:"Temperatur",tint:"Tint",highlights:"Lichter",shadows:"Tiefen"};
function hist(h){const c=document.querySelector("#fxHist"),g=c.getContext("2d");g.clearRect(0,0,256,90);if(!h)return;
 const m=Math.max(...h.r,...h.g,...h.b)||1;[["r","rgba(255,80,80,.6)"],["g","rgba(80,255,120,.5)"],["b","rgba(90,140,255,.6)"]].forEach(([k,col])=>{g.strokeStyle=col;g.beginPath();h[k].forEach((v,i)=>{const y=90-v/m*88;i?g.lineTo(i,y):g.moveTo(i,y)});g.stroke()})}
async function preview(){if(!S.sel)return;let r;
 if(FX.filter)r=await api("/api/studio/filter",{id:S.sel.id,name:FX.filter,preview:true});
 else r=await api("/api/studio/adjust",{id:S.sel.id,params:FX.adj,preview:true});
 if(r.success){document.querySelector("#imgA").src=r.preview;document.querySelector("#aftLb").textContent="Vorschau ("+(FX.filter||"Regler")+")";if(r.histogram)hist(r.histogram)}else log("Vorschau: "+r.error)}
const deb=()=>{clearTimeout(FX.t);FX.t=setTimeout(preview,250)};
api("/api/studio/fx").then(d=>{if(!d.success)return;
 const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
 document.querySelector("#fxFilters").innerHTML=Object.entries(d.filters).map(([k,l])=>`<button class="btn g" data-fx="${esc(k)}">${esc(l)}</button>`).join("");
 document.querySelectorAll("[data-fx]").forEach(b=>b.onclick=()=>{if(!need())return;FX.filter=b.dataset.fx;document.querySelectorAll("[data-fx]").forEach(x=>x.classList.toggle("sel",x===b));preview()});
 document.querySelector("#fxSliders").innerHTML=d.adjust.map(k=>`<label class="sub" style="display:flex;align-items:center;gap:8px">${esc(labels[k]||k)}<input type="range" min="-100" max="100" value="0" data-adj="${esc(k)}" style="flex:1"><span id="adjv_${esc(k)}">0</span></label>`).join("");
 document.querySelectorAll("[data-adj]").forEach(s=>s.oninput=()=>{if(!need())return;FX.filter=null;document.querySelectorAll("[data-fx]").forEach(x=>x.classList.remove("sel"));FX.adj[s.dataset.adj]=+s.value;document.querySelector("#adjv_"+s.dataset.adj).textContent=s.value;deb()});
 const opt=(sel,arr)=>document.querySelector(sel).innerHTML=arr.map(([v,l])=>`<option value="${esc(v)}">${esc(l)}</option>`).join("");
 opt("#soFmt",Object.entries(d.formats).map(([k,v])=>[k,k+" "+v.join("\u00d7")]));opt("#soTpl",d.templates.map(t=>[t,t]));opt("#soFont",d.fonts.map(t=>[t,t]))});
document.querySelector("#fxReset").onclick=()=>{FX.filter=null;FX.adj={};document.querySelectorAll("[data-adj]").forEach(s=>{s.value=0;document.querySelector("#adjv_"+s.dataset.adj).textContent="0"});document.querySelectorAll("[data-fx]").forEach(x=>x.classList.remove("sel"));if(S.sel)preview()};
document.querySelector("#fxSave").onclick=async()=>{if(!need())return;const r=FX.filter?await api("/api/studio/filter",{id:S.sel.id,name:FX.filter}):await api("/api/studio/adjust",{id:S.sel.id,params:FX.adj});
 log(r.success?`Neue Version v${r.version.n} (${r.version.op}) gespeichert - Original unver\u00e4ndert`:"Speichern: "+r.error);if(r.success)refreshSel()};
function soBody(p){return{text:document.querySelector("#soText").value,format:document.querySelector("#soFmt").value,template:document.querySelector("#soTpl").value,font:document.querySelector("#soFont").value,position:document.querySelector("#soPos").value,author:document.querySelector("#soAuthor").value,watermark:document.querySelector("#soWm").value,bg_id:(document.querySelector("#soBg").checked&&S.sel)?S.sel.id:null,preview:p}}
document.querySelector("#soPrev").onclick=async()=>{const r=await api("/api/studio/social",soBody(true));if(r.success)document.querySelector("#soImg").src=r.preview;else log("Social: "+r.error)};
document.querySelector("#soMake").onclick=async()=>{const r=await api("/api/studio/social",soBody(false));if(r.success){document.querySelector("#soImg").src="/api/studio/social/"+r.file;log(`Social-Grafik ${r.file} (${r.size.join("\u00d7")}) gespeichert`)}else log("Social: "+r.error)};
})();
