/* Schritt 5 (Nexus-UI, uiwork19): Hermes-Status als immer sichtbarer Chip in #statusChips. Nur lesend, nur ASCII.
   Quelle: GET /api/hermes/health. BUSY und Auftragsliste haben keinen Lese-Endpunkt -> ehrlich "nicht verfuegbar". */
(function(){
  function chip(){
    var c = document.getElementById('nx-hermes-step5'); if(c) return c;
    var host = document.getElementById('statusChips'); if(!host) return null;
    c = document.createElement('span'); c.id = 'nx-hermes-step5'; c.className = 'chip';
    c.textContent = 'Hermes ...'; host.appendChild(c); return c;
  }
  function since(iso){ var t=Date.parse(iso||''); if(isNaN(t)) return '?'; var s=Math.max(0,Math.floor((Date.now()-t)/1000)); return Math.floor(s/60)+':'+String(s%60).padStart(2,'0'); }
  async function tick(){
    var c = chip(); if(!c) return;
    var h = null;
    try{ var r = await fetch('/api/hermes/health', {cache:'no-store'}); h = await r.json(); }catch(e){ h = null; }
    var tip = ['Quelle: GET /api/hermes/health',
               (h && typeof h.busy==='boolean') ? ('Hermes BUSY: ' + (h.busy ? ('ja, seit ' + since(h.busy_since) + (h.request_id ? ' | ' + h.request_id : '')) : 'nein')) : 'Hermes BUSY: nicht verfuegbar (Feld fehlt)',
               'Hermes-Auftraege: nicht verfuegbar (kein Lese-Endpunkt fuer hermes_tasks)'];
    if(!h){ c.textContent = 'Hermes: nicht verfuegbar'; c.className = 'chip warn'; }
    else if(h.reachable === true){ c.textContent = 'Hermes CLI bereit | ' + (h.model || '?') + ' | run_as ' + (h.run_as || '?') + (h.busy===true ? ' | BUSY ' + since(h.busy_since) : ''); c.className = 'chip ' + (h.busy===true ? 'warn' : 'ok'); }
    else { c.textContent = 'Hermes HERMES_UNAVAILABLE' + (h.detail ? ' | ' + h.detail : ''); c.className = 'chip warn'; }
    c.title = tip.join('\n');
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', tick); else tick();
  setInterval(tick, 30000);
})();
