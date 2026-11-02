/* Punkt 6 (Nexus-UI, uiwork22): Hermes-Auftragsliste (nur lesen) + Memory-Hinweis.
   Quellen: GET /api/orders (orders[], active) und GET /api/tasks (tasks[]). Keine schreibenden Aufrufe.
   Memory: Anzeigen/Korrigieren macht app.js bereits (/api/memory, /api/memory/correct). Loeschen: kein Endpunkt -> "nicht verfuegbar". */
(function(){
  function mk(tag, css, txt){ var e=document.createElement(tag); if(css) e.style.cssText=css; if(txt!=null) e.textContent=String(txt); return e; }
  var panel=null, chip=null, open=false;
  async function jget(u){ try{ var r=await fetch(u,{cache:'no-store'}); if(!r.ok) return null; return await r.json(); }catch(e){ return null; } }
  // uiwork20: Kachel im Schema der anderen Workspace-Panels (panel float-panel workspace-panel, panel-head, body, Overlay).
  var body=null;
  function closePanel(){ open=false; if(panel) panel.classList.remove('show'); var ov=document.getElementById('nxOverlay'); if(ov && !document.querySelector('.workspace-panel.show')){ ov.classList.remove('show'); ov.onclick=null; } }
  function openPanel(){
    document.querySelectorAll('.workspace-panel.show').forEach(function(x){ if(x!==panel) x.classList.remove('show'); });
    if(panel.parentElement!==document.body) document.body.appendChild(panel);
    panel.classList.add('show'); open=true;
    var ov=document.getElementById('nxOverlay');
    if(ov){ if(ov.parentElement!==document.body) document.body.appendChild(ov); ov.classList.add('show'); ov.onclick=function(e){ if(e.target!==ov) return; closePanel(); var ch=document.getElementById('nx-hermes-tasks-chip'); if(window.nxForwardOverlayClick){ var t0=document.elementFromPoint(e.clientX,e.clientY); if(t0 && ch && ch.contains(t0)) return; window.nxForwardOverlayClick(e,null,'hermesTasksPanel'); } }; }
    refresh();
  }
  function ensure(){
    if(!panel){
      panel=mk('div'); panel.className='panel float-panel workspace-panel'; panel.id='hermesTasksPanel';
      var head=mk('div'); head.className='panel-head'; head.appendChild(mk('h3',null,'Hermes-Auftraege'));
      var x=mk('button',null,'\u00d7'); x.type='button'; x.className='icon-btn'; x.id='btnCloseHermesTasks'; x.title='Schliessen'; x.onclick=closePanel; head.appendChild(x);
      body=mk('div'); body.className='body';
      panel.appendChild(head); panel.appendChild(body);
      ['click','mousedown','pointerdown'].forEach(function(ev){ panel.addEventListener(ev,function(e){ e.stopPropagation(); }); });
      document.body.appendChild(panel);
      new MutationObserver(function(){ if(!panel.classList.contains('show')) open=false; }).observe(panel,{attributes:true,attributeFilter:['class']});
    }
    if(!chip){ var host=document.getElementById('statusChips'); if(!host) return false;
      chip=mk('button',null,'Hermes-Auftraege ...'); chip.type='button'; chip.className='chip'; chip.id='nx-hermes-tasks-chip';
      chip.style.position='relative'; chip.style.zIndex='10050';
      chip.onclick=function(e){ if(e) e.stopPropagation(); if(open) closePanel(); else openPanel(); }; host.appendChild(chip);
      // uiwork21: liegt das Overlay einer anderen Kachel ueber dem Chip, Klick abfangen: andere schliessen, dann Hermes oeffnen.
      window.addEventListener('click',function(e){ var ov=document.getElementById('nxOverlay'); if(!ov||e.target!==ov||!chip) return; var r=chip.getBoundingClientRect(); if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom) return; e.preventDefault(); e.stopImmediatePropagation(); if(open) closePanel(); else openPanel(); },true); }
    return true;
  }
  function row(k,v,c){ var d=mk('div','display:flex;justify-content:space-between;gap:10px'); d.appendChild(mk('span','color:#54708e',k)); d.appendChild(mk('span','text-align:right;color:'+(c||'#1e3550'),v)); return d; }
  function col(s){ return s==='PASS'?'#0f9d58':(s==='BLOCKED'||s==='FAIL'||s==='FAILED')?'#d14343':(s==='RUNNING'||s==='ACTIVE')?'#b45309':'#64748b'; }
  function since(iso){ var t=Date.parse(iso||''); if(isNaN(t)) return '?'; var s=Math.max(0,Math.floor((Date.now()-t)/1000)); return Math.floor(s/60)+':'+String(s%60).padStart(2,'0'); }
  async function refresh(){
    if(!ensure()) return;
    // uiwork21: orders/tasks sofort, health parallel und fuellt die BUSY-Zeile nach.
    var hp=open?jget('/api/hermes/health'):null; var op=jget('/api/orders'), tp=jget('/api/tasks');
    var o=await op, t=await tp;
    var n=(o&&Array.isArray(o.orders))?o.orders.length:null;
    chip.textContent='Hermes-Auftraege '+(n==null?'nicht verfuegbar':'('+n+')');
    chip.className='chip'+(n==null?' warn':'');
    if(!open) return;
    body.innerHTML='';
    var note=mk('p',null,'Nur lesen. Quellen: GET /api/orders, /api/tasks, /api/hermes/health.'); note.className='streams-note'; body.appendChild(note);
    var acts=mk('div'); acts.className='streams-actions'; var rb=mk('button',null,'Aktualisieren'); rb.type='button'; rb.className='primary'; rb.onclick=function(){ refresh(); }; acts.appendChild(rb); body.appendChild(acts);
    var panelOut=mk('div'); panelOut.className='files-list'; body.appendChild(panelOut);
    var busyRow=row('HERMES BUSY','laedt ...','#64748b'); panelOut.appendChild(busyRow);
    if(hp) hp.then(function(hh){ var nr = (hh && typeof hh.busy==='boolean') ? row('HERMES BUSY', hh.busy ? ('ja, seit '+since(hh.busy_since)+(hh.request_id?' | '+hh.request_id:'')) : 'nein', hh.busy?'#b45309':'#0f9d58') : row('HERMES BUSY','nicht verfuegbar (Feld fehlt)','#64748b'); if(busyRow.parentNode) busyRow.parentNode.replaceChild(nr,busyRow); });
    if(!o){ panelOut.appendChild(row('Auftraege','nicht verfuegbar (/api/orders nicht erreichbar)','#b45309')); }
    else {
      var a=o.active; panelOut.appendChild(row('Aktiver Lauf', a?((a.status||'?')+(a.phase?' | '+a.phase:'')+(a.run_id?' | '+a.run_id:'')+(a.error?' | '+a.error:'')):'keiner', a?col(a.status):'#64748b'));
      if(o.pending_count!=null) panelOut.appendChild(row('Offene Freigaben', String(o.pending_count)+(o.approval_error?' | '+o.approval_error:'')));
      var list=(o.orders||[]).slice().sort(function(p,q){ return String(q.updated_at||'').localeCompare(String(p.updated_at||'')); });
      if(!list.length) panelOut.appendChild(mk('div','color:#64748b;margin-top:6px','Keine Auftraege.'));
      list.forEach(function(it){
        var c=mk('div','border-top:1px solid #c9d9ea;padding:6px 0');
        c.appendChild(row(it.updated_at||'?', (it.status||'?')+(it.phase?' | '+it.phase:'')+(it.total!=null?' | '+(it.completed!=null?it.completed:'?')+'/'+it.total:''), col(it.status)));
        c.appendChild(mk('div','white-space:pre-wrap;word-break:break-word;margin-top:2px', it.task||it.name||'?'));
        if(it.error) c.appendChild(mk('div','color:#d14343;margin-top:2px','Fehler: '+it.error));
        if(Array.isArray(it.artifacts)&&it.artifacts.length) c.appendChild(mk('div','color:#54708e;margin-top:2px','Ergebnis: '+it.artifacts.map(function(z){return typeof z==='string'?z:(z&&(z.path||z.name))||JSON.stringify(z);}).join(', ')));
        panelOut.appendChild(c);
      });
    }
    var tl=(t&&Array.isArray(t.tasks))?t.tasks:null;
    panelOut.appendChild(row('NEXUS-Tasks (/api/tasks)', tl==null?'nicht verfuegbar':String(tl.length), tl==null?'#b45309':null));
    (tl||[]).forEach(function(k){ panelOut.appendChild(mk('div','padding-left:8px',(k.status||'?')+' | '+(k.title||k.id||'?'))); });
  }
  // uiwork19: Loeschen ueber POST /api/memory/delete {id}. Hinweis "nicht verfuegbar" nur solange der Endpunkt 404 liefert.
  var DEL_KEY='nx_mem_delete_state'; // 'unknown' | 'missing' | 'ok'
  function delState(){ try{ return localStorage.getItem(DEL_KEY)||'unknown'; }catch(e){ return 'unknown'; } }
  function setDelState(v){ try{ localStorage.setItem(DEL_KEY,v); }catch(e){} memNote(); }
  function memNote(){
    var mp=document.getElementById('memoryPanel'); if(!mp) return;
    var n=document.getElementById('nx-mem-delete-note');
    var st=delState();
    if(st==='ok'){ if(n) n.remove(); return; }
    if(!n){ n=mk('p','color:#64748b;font-size:12px;margin:6px 0',''); n.id='nx-mem-delete-note'; var list=document.getElementById('memoryList'); if(list&&list.parentNode) list.parentNode.insertBefore(n,list); else mp.appendChild(n); }
    n.textContent = st==='missing' ? 'Erinnerung loeschen: nicht verfuegbar (Endpunkt liefert 404, kommt nach Core-Neustart).' : 'Erinnerung loeschen: wird beim ersten Versuch geprueft (Endpunkt evtl. erst nach Core-Neustart verfuegbar).';
  }
  async function delMem(id, btn){
    if(!window.confirm('Erinnerung '+id+' wirklich loeschen? Das laesst sich nicht rueckgaengig machen.')) return;
    btn.disabled=true; var msg=btn.nextSibling;
    try{
      var r=await fetch('/api/memory/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})});
      if(r.status===404 || r.status===405){ var j404=await r.json().catch(function(){return null;}); if(j404 && j404.error==='NOT_FOUND'){ setDelState('ok'); msg.textContent='Nicht gefunden (bereits geloescht?).'; return; } setDelState('missing'); msg.textContent='Loeschen nicht verfuegbar (HTTP '+r.status+').'; btn.disabled=false; return; }
      var j=await r.json().catch(function(){return null;});
      if(j && j.success){ setDelState('ok'); msg.textContent='Geloescht.'; var rb=document.getElementById('btnMemoryRefresh'); if(rb) rb.click(); return; }
      setDelState('ok');
      msg.textContent = (j && j.error==='NOT_FOUND') ? 'Nicht gefunden (bereits geloescht?).' : ('Loeschen fehlgeschlagen: '+((j&&j.error)||('HTTP '+r.status)));
      btn.disabled=false;
    }catch(e){ msg.textContent='Loeschen fehlgeschlagen: '+(e&&e.message||e); btn.disabled=false; }
  }
  function addDeleteButtons(){
    var list=document.getElementById('memoryList'); if(!list) return;
    list.querySelectorAll('.mem-item').forEach(function(card){
      if(card.querySelector('.nx-mem-del')) return;
      var meta=card.querySelector('.mem-id'); var m=meta && /\bM-[A-Za-z0-9_-]+/.exec(meta.textContent||''); if(!m) return;
      var b=mk('button',null,'Loeschen'); b.type='button'; b.className='nx-mem-del';
      var msg=mk('span','margin-left:8px;font-size:12px;color:#b45309','');
      b.onclick=function(){ delMem(m[0], b); };
      card.appendChild(b); card.appendChild(msg);
    });
  }
  function watchMemory(){ var list=document.getElementById('memoryList'); if(!list) return; addDeleteButtons(); new MutationObserver(addDeleteButtons).observe(list,{childList:true}); }
  function start(){ memNote(); watchMemory(); refresh(); setInterval(refresh,15000); }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start); else start();
})();
