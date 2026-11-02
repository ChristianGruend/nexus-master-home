/* 003A UI (Nexus-UI, uiwork17): Hermes ALL-MODUS - nur Anzeige + Einmal-ID.
   Quelle: Feld all_mode in GET /api/nexus-os/status. Fehlt es -> alles unsichtbar.
   Bestaetigen sendet NUR {request_id} an all_mode.confirm_endpoint (nur gleiche Origin, Pfad mit "/").
   Kein Token, keine Rechte, kein Einschalten per Sprache. Freigabe macht Admin-Dienst/Windows Hello.
   "Beenden" schickt "/hermes normal" ueber den normalen Chat-Sendeweg (#nxInput + #btnSend). */
(function(){
  var ORANGE='#e07e12', GREY='#64748b';
  var root=null, bar=null, card=null, logTile=null, last=null, sentFor={}, infoFor={}, cardKey='';
  function mk(tag, css, txt){ var e=document.createElement(tag); if(css) e.style.cssText=css; if(txt!=null) e.textContent=txt; return e; }
  function ensure(){
    if(root) return;
    root=mk('div','position:fixed;top:0;left:0;right:0;z-index:2000;pointer-events:none;font:13px system-ui,sans-serif;color:#1e3550');
    root.id='nx-allmode-root';
    bar=mk('div','display:none;pointer-events:auto;background:'+ORANGE+';color:#111;padding:6px 12px;align-items:center;gap:12px;font-weight:600');
    card=mk('div','display:none;pointer-events:auto;margin:10px auto;max-width:520px;background:rgba(250,252,255,.96);border:2px solid '+ORANGE+';border-radius:10px;padding:12px 14px;box-shadow:0 10px 30px rgba(35,80,120,.18)');
    logTile=mk('div','display:none;pointer-events:auto;position:fixed;right:12px;bottom:12px;width:420px;max-height:240px;overflow:auto;background:rgba(250,252,255,.96);border:1px solid rgba(150,190,225,.55);border-radius:10px;padding:8px 10px;font-size:12px;box-shadow:0 10px 30px rgba(35,80,120,.15)');
    root.appendChild(bar); root.appendChild(card); document.body.appendChild(root); document.body.appendChild(logTile);
  }
  function fmtLeft(iso){ if(!iso) return '?'; var ms=Date.parse(iso)-Date.now(); if(isNaN(ms)) return '?'; if(ms<0) ms=0; var s=Math.floor(ms/1000); return Math.floor(s/60)+':'+String(s%60).padStart(2,'0'); }
  function gateInfo(g){
    // uiwork16: t8_gate als Objekt {pass, note, source} oder String. Gesperrt, wenn pass nicht true (wie Core).
    if(g && typeof g==='object'){ var p=g.pass===true?'PASS':(g.pass===false?'nicht bestanden':'unbekannt');
      var extra=[g.note,g.source].filter(function(x){return typeof x==='string'&&x;}).join(' | ');
      return {text:p+(extra?' | '+extra:''), locked:g.pass!==true}; }
    if(typeof g==='string' && g) return {text:g, locked:!/PASS/i.test(g)};
    return {text:'unbekannt', locked:true};
  }
  function list(v){ return Array.isArray(v)?v.map(String).join(', '):(typeof v==='string'?v:''); }
  function sendNormal(){
    var inp=document.getElementById('nxInput'), btn=document.getElementById('btnSend');
    if(!inp||!btn){ alert('Chat nicht verfuegbar - bitte "/hermes normal" manuell senden.'); return; }
    inp.value='/hermes normal'; inp.dispatchEvent(new Event('input',{bubbles:true})); btn.click();
  }
  async function confirmReq(a, btn, info){
    var ep=a.confirm_endpoint, rid=a.request_id;
    if(typeof ep!=='string' || ep.charAt(0)!=='/' || ep.charAt(1)==='/' || !rid){ info.textContent='Bestaetigung nicht moeglich: Endpunkt oder request_id fehlt/ungueltig.'; return; }
    if(sentFor[rid]){ info.textContent='Diese request_id wurde bereits gesendet.'; return; }
    sentFor[rid]=1; btn.disabled=true; info.textContent='Sende Einmal-ID ...';
    try{
      var r=await fetch(ep,{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({request_id:String(rid)})});
      var j=await r.json().catch(function(){return {};});
      infoFor[rid] = info.textContent = j.accepted ? ('Angenommen - ' + (j.state||'warte auf Windows Hello') + '. Freigabe erfolgt am PC.') : ('Abgelehnt: ' + (j.error||('HTTP '+r.status)));
    }catch(e){ infoFor[rid] = info.textContent='Bestaetigung fehlgeschlagen: '+(e&&e.message||e); }
  }
  function renderCard(a){
    card.innerHTML='';
    var gi=gateInfo(a.t8_gate); var locked = a.state==='LOCKED_T8' || gi.locked;
    card.appendChild(mk('div','color:'+ORANGE+';font-weight:700;margin-bottom:6px', locked ? 'Hermes ALL-MODUS - gesperrt bis T8 PASS' : 'Hermes ALL-MODUS - Anfrage'));
    var rows=[['Bereiche',list(a.scope)||'?'],['Dauer',(a.duration_min!=null?a.duration_min+' min':'?')],['Bleibt gesperrt',list(a.still_blocked)||'?'],['T8',gi.text]];
    if(!locked) rows.push(['Anfrage laeuft ab in',fmtLeft(a.expires_at),'nx-allmode-left']);
    rows.forEach(function(r){ var d=mk('div','display:flex;justify-content:space-between;gap:10px'); d.appendChild(mk('span','color:#54708e',r[0])); var v=mk('span','text-align:right',r[1]); if(r[2]) v.id=r[2]; d.appendChild(v); card.appendChild(d); });
    card.appendChild(mk('div','color:#54708e;font-size:11px;margin-top:6px','Der Knopf sendet nur eine Einmal-ID. Die Freigabe erfolgt durch den Admin-Dienst per Windows Hello/PIN am PC.'));
    var btn=mk('button','margin-top:8px;padding:6px 14px;border-radius:8px;border:0;font-weight:600;cursor:pointer;background:'+(locked?GREY:ORANGE)+';color:#111', locked?'Gesperrt bis T8 PASS':'Anfrage bestaetigen');
    btn.type='button'; btn.disabled = locked || !!sentFor[a.request_id];
    var info=mk('div','margin-top:6px;font-size:12px',infoFor[a.request_id]||''); info.id='nx-allmode-info';
    if(!locked) btn.onclick=function(){ confirmReq(a, btn, info); };
    card.appendChild(btn); card.appendChild(info);
  }
  function renderLog(a){
    logTile.innerHTML=''; logTile.appendChild(mk('div','color:#0b6fb8;font-weight:600;margin-bottom:4px','ALL-MODUS Protokoll (nur lesen)'));
    if(!Array.isArray(a.log)){ logTile.appendChild(mk('div','color:'+GREY,'Protokoll nicht verfuegbar (kein Feld all_mode.log)')); return; }
    if(!a.log.length){ logTile.appendChild(mk('div','color:'+GREY,'Keine Vorgaenge.')); return; }
    a.log.slice(-50).reverse().forEach(function(x){ logTile.appendChild(mk('div','border-top:1px solid #c9d9ea;padding:2px 0',(x.ts||'?')+' | '+(x.user||'?')+' | '+(x.action||'?')+' | '+(x.path||'?')+' | '+(x.result||'?'))); });
  }
  function render(){
    ensure(); var a=last;
    if(!a || typeof a!=='object' || !a.state || a.state==='OFF'){ bar.style.display='none'; card.style.display='none'; logTile.style.display = (a && a.state==='OFF' && Array.isArray(a.log) && a.log.length) ? 'block' : 'none'; if(logTile.style.display==='block') renderLog(a); return; }
    if(a.state==='ACTIVE'){
      bar.innerHTML=''; bar.style.display='flex'; card.style.display='none';
      bar.appendChild(mk('span',null,'Hermes ALL-MODUS'));
      bar.appendChild(mk('span',null,'Restzeit '+fmtLeft(a.ends_at)));
      var b=mk('button','margin-left:auto;padding:4px 16px;font-size:14px;font-weight:700;border-radius:8px;border:2px solid #111;background:#fff;color:#111;cursor:pointer','Beenden'); b.type='button'; b.onclick=sendNormal; bar.appendChild(b);
    } else if(a.state==='PENDING_CONFIRMATION' || a.state==='LOCKED_T8' || a.state==='AWAITING_WINDOWS_HELLO'){
      // uiwork17: Karte nur, wenn Admin-Dienst 005 bereit ist und die Anfrage nicht abgelaufen ist. Sonst gar nichts.
      var exp=Date.parse(a.expires_at||''); var expired=!isNaN(exp) && exp<=Date.now();
      if(a.admin_service_ready!==true || a.state==='LOCKED_T8' || expired){ bar.style.display='none'; card.style.display='none'; logTile.style.display='none'; cardKey=''; return; }
      bar.style.display='none'; card.style.display='block';
      var key=JSON.stringify([a.state,a.request_id,a.scope,a.duration_min,a.still_blocked,a.t8_gate,a.expires_at,infoFor[a.request_id]||'']);
      if(key!==cardKey){ cardKey=key; renderCard(a); }
    } else { bar.style.display='none'; card.style.display='none'; }
    logTile.style.display='block'; renderLog(a);
  }
  async function poll(){
    try{ var r=await fetch('/api/nexus-os/status',{cache:'no-store'}); var j=await r.json(); last = (j && Object.prototype.hasOwnProperty.call(j,'all_mode')) ? j.all_mode : null; }catch(e){ last=null; }
    render();
  }
  function start(){ poll(); setInterval(poll,5000); setInterval(function(){ if(!last) return; if(last.state==='ACTIVE') render(); else if(last.state==='PENDING_CONFIRMATION'||last.state==='AWAITING_WINDOWS_HELLO'){ var x=Date.parse(last.expires_at||''); if(!isNaN(x) && x<=Date.now()) { render(); return; } var e=document.getElementById('nx-allmode-left'); if(e) e.textContent=fmtLeft(last.expires_at); } },1000); }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start); else start();
})();
