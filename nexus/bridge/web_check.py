"""Objektive Browserprüfung für Hermes-Webprojekte (HTML/CSS/JavaScript).

Ablauf: Projektkopie in workspace/sandbox/web/<lauf>/, Prüfgerüst in eine Kopie der
index.html injizieren, Chrome/Edge headless mit virtueller Zeit starten, Ergebnis aus dem
DOM lesen, Screenshot als Beleg sichern. Das Original wird nie verändert.

Sicherheitsgrenzen der Prüfung:
  - Netzwerk: jede Anfrage (auch Loopback zu NEXUS) läuft über einen toten Proxy
    (--proxy-server=127.0.0.1:9, --proxy-bypass-list=<-loopback>) und eine CSP mit
    connect-src 'none'. Belegt im Bericht durch einen eigenen Netzwerkversuch.
  - Der Seitencode läuft im Chromium-Renderer-Sandbox-Prozess (kein --no-sandbox),
    file://-Seiten dürfen keine anderen lokalen Dateien lesen (kein
    --allow-file-access-from-files), frisches Profil pro Lauf, harte Laufzeitgrenze.
  - JavaScript-Syntax wird zusätzlich mit `node --check` nur geparst, nie ausgeführt.

Bestanden ist eine Prüfung nur, wenn alle Kriterien objektiv erfüllt sind; die Kriterien
belegen ein lauffähiges, animiertes, per Tastatur bedienbares Canvas-Programm, nicht
Spielspaß oder Spielregeln.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from nexus.bridge.code_sandbox import CODE_ROOT, code_path, _inside, SandboxError

ROOT = Path(__file__).resolve().parents[2]
WEB_RUNS = ROOT / "workspace" / "sandbox" / "web"
EVIDENCE_ROOT = "workspace/projects/hermes/preview_evidence"
WEB_EXT = {"html", "css", "js", "json", "md"}
BROWSERS = (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
NODE = r"C:\Program Files\nodejs\node.exe"
VIRTUAL_MS = 10000
WALL_SECONDS = 60
CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline' file:; style-src 'self' 'unsafe-inline' file:; "
       "img-src 'self' data: blob: file:; font-src 'self' data: file:; media-src 'self' data: blob: file:; "
       "connect-src 'none'; base-uri 'none'; form-action 'none'")

HARNESS = r"""(function(){
var SEND_INPUT=__SEND_INPUT__;
/* Fester Zufallsstartwert: Läufe mit/ohne Eingabe sind sonst identisch -> Eingabewirkung messbar. */
var seed=0x9e3779b9;Math.random=function(){seed|=0;seed=seed+0x6D2B79F5|0;var t=Math.imul(seed^seed>>>15,1|seed);
  t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296};
var R={errors:[],console_errors:[],frames:0,key_listeners:0,pointer_listeners:0,keys_sent:0,samples:[],net:'pending'};
window.__nexusReport=R;
window.addEventListener('error',function(e){R.errors.push(String(e.message||'error')+' @'+String(e.filename||'').split('/').pop()+':'+e.lineno+':'+e.colno)});
window.addEventListener('unhandledrejection',function(e){R.errors.push('unhandledrejection: '+String(e.reason&&e.reason.message||e.reason))});
var ce=console.error;console.error=function(){R.console_errors.push(Array.prototype.join.call(arguments,' ').slice(0,300));try{return ce.apply(console,arguments)}catch(x){}};
window.requestAnimationFrame=function(cb){return setTimeout(function(){R.frames++;cb(performance.now())},16)};
window.cancelAnimationFrame=function(id){clearTimeout(id)};
var add=EventTarget.prototype.addEventListener;
EventTarget.prototype.addEventListener=function(t,f,o){
  if(/^key(down|up|press)$/.test(t))R.key_listeners++;
  if(/^(touchstart|pointerdown|mousedown|click)$/.test(t))R.pointer_listeners++;
  return add.call(this,t,f,o)};
try{Object.defineProperty(window,'localStorage',{configurable:true,get:function(){throw new DOMException('storage disabled in NEXUS preview','SecurityError')}})}catch(x){}
function canvasInfo(){var cs=document.getElementsByTagName('canvas'),c=null;
  for(var i=0;i<cs.length;i++){if(!c||cs[i].width*cs[i].height>c.width*c.height)c=cs[i]}
  if(!c)return null;var h=0,vals={},n=0,d=null;
  try{var x=c.getContext('2d');if(x)d=x.getImageData(0,0,c.width,c.height).data}catch(e){}
  if(d){for(var j=0;j<d.length;j+=4){var v=(d[j]<<16)|(d[j+1]<<8)|d[j+2];h=(h*31+v)|0;if(n<64&&!vals[v]){vals[v]=1;n++}}}
  else{var u=c.toDataURL();for(var k=0;k<u.length;k+=7){h=(h*31+u.charCodeAt(k))|0}n=u.length>2000?8:1}
  return {w:c.width,h:c.height,hash:h,distinct:n}}
/* Spielzustand (Vertrag: globales `state` aus der vorgegebenen Struktur) zur Laufzeit lesen. */
function gameState(){try{if(typeof state==='undefined'||!state)return null;var p=state.player||{};
  var n=function(v){return Array.isArray(v)?v.length:(typeof v==='number'?v:null)};
  var vis=0;(Array.isArray(state.enemies)?state.enemies:[]).forEach(function(e){if(e&&typeof e.x==='number'&&typeof e.y==='number'&&e.x>=0&&e.x<=960&&e.y>=0&&e.y<=600)vis++});
  return {enemies:n(state.enemies),enemies_visible:vis,lasers:n(state.lasers),score:typeof state.score==='number'?state.score:null,
    lives:typeof state.lives==='number'?state.lives:null,over:!!state.over,px:typeof p.x==='number'?p.x:null,py:typeof p.y==='number'?p.y:null}}
  catch(e){return null}}
/* Spielszenarien über den Zustandsvertrag: echte Mechanik statt Momentaufnahmen. */
var TPL=null;function remember(){try{if(state&&Array.isArray(state.enemies)&&state.enemies[0])TPL=JSON.parse(JSON.stringify(state.enemies[0]))}catch(e){}}
function enemyAt(x,y){var e=TPL?JSON.parse(JSON.stringify(TPL)):{};e.x=x;e.y=y;
  ['speed','dx','dy','vx','vy','vel','velocity'].forEach(function(k){if(typeof e[k]==='number')e[k]=0});
  if(e.vel&&typeof e.vel==='object'){e.vel.x=0;e.vel.y=0}return e}
function scen(){var S={};R.scenarios=S;
  function ok(){return typeof state!=='undefined'&&state&&state.player&&Array.isArray(state.enemies)}
  setTimeout(function(){if(!ok())return;remember();state.over=false;state.lives=3;state.player.x=480;state.player.y=480;
    if(state.player.dx!==undefined)state.player.dx=0;if(state.player.dy!==undefined)state.player.dy=0;
    if(Array.isArray(state.lasers))state.lasers.length=0;state.enemies.length=0;
    S.hit={score0:state.score};var tgt=enemyAt(480,300);tgt.__nexus_target=true;state.enemies.push(tgt);
    key('keydown',' ','Space',32)},7000);
  setTimeout(function(){key('keyup',' ','Space',32)},7300);
  setTimeout(function(){if(!ok()||!S.hit)return;
    /* Treffer = Punkte steigen ODER genau der markierte Gegner wurde aus dem Spiel entfernt (nicht nur weggedriftet). */
    var alive=state.enemies.some(function(e){return e&&e.__nexus_target});
    S.hit.target_removed=!alive;S.hit.score1=state.score;
    S.hit.passed=(typeof state.score==='number'&&state.score>S.hit.score0)||!alive},8000);
  setTimeout(function(){if(!ok())return;state.over=false;state.enemies.length=0;state.player.x=300;state.player.y=480;
    if(state.player.dx!==undefined)state.player.dx=0;if(state.player.dy!==undefined)state.player.dy=0;
    S.move={x0:state.player.x};key('keydown','ArrowRight','ArrowRight',39)},8100);
  setTimeout(function(){key('keyup','ArrowRight','ArrowRight',39);if(ok()&&S.move)S.move.x1=state.player.x},8400);
  setTimeout(function(){if(!ok()||!S.move)return;S.move.x2=state.player.x;var d=S.move.x1-S.move.x0;
    /* 0,3 s Taste: plausible Strecke (kein Hochschaukeln bis zum Rand) und Stillstand nach dem Loslassen. */
    S.move.passed=d>5&&d<400&&Math.abs(S.move.x2-S.move.x1)<15},8900);
  setTimeout(function(){if(!ok())return;state.over=false;state.lives=3;state.enemies.length=0;
    S.crash={lives0:state.lives};state.enemies.push(enemyAt(state.player.x,state.player.y))},9000);
  setTimeout(function(){if(!ok()||!S.crash)return;S.crash.lives1=state.lives;
    S.crash.passed=typeof state.lives==='number'&&state.lives<S.crash.lives0},9400);
}
function sample(tag){var i=canvasInfo();R.samples.push({t:tag,frames:R.frames,canvas:i,game:gameState()})}
function key(type,k,code,kc){var e=new KeyboardEvent(type,{key:k,code:code,keyCode:kc,which:kc,bubbles:true,cancelable:true});
  try{Object.defineProperty(e,'keyCode',{get:function(){return kc}});Object.defineProperty(e,'which',{get:function(){return kc}})}catch(x){}
  (document.activeElement||document.body||document).dispatchEvent(e)}
/* Asymmetrische Sequenz (Taste, Code, keyCode, Start-ms, Haltedauer-ms): Bewegungen heben sich nicht auf. */
var KEYS=[['Enter','Enter',13,1800,120],[' ','Space',32,1950,120],['ArrowRight','ArrowRight',39,2100,700],
  ['ArrowUp','ArrowUp',38,2850,400],[' ','Space',32,3300,150],['d','KeyD',68,3500,400],['w','KeyW',87,3950,300],
  [' ','Space',32,4300,120],['ArrowLeft','ArrowLeft',37,4450,100],['a','KeyA',65,4600,80],['s','KeyS',83,4700,60]];
setTimeout(function(){sample('t1000');try{fetch('http://127.0.0.1:8299/api/health').then(function(){R.net='OPEN'},function(){R.net='BLOCKED'})}catch(e){R.net='BLOCKED'}},1000);
setTimeout(function(){sample('t1600')},1600);
if(SEND_INPUT){
setTimeout(function(){var c=document.getElementsByTagName('canvas')[0];
  if(c){var r=c.getBoundingClientRect();['pointerdown','mousedown','click'].forEach(function(t){try{c.dispatchEvent(new MouseEvent(t,{bubbles:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2}))}catch(e){}})}},1700);
KEYS.forEach(function(k){setTimeout(function(){key('keydown',k[0],k[1],k[2]);R.keys_sent++},k[3]);
  setTimeout(function(){key('keyup',k[0],k[1],k[2])},k[3]+k[4])});
}
setTimeout(function(){sample('t3400');remember()},3400);
setTimeout(function(){sample('t4800');remember()},4800);
setTimeout(function(){sample('t6800');remember()},6800);
if(SEND_INPUT)scen();
setTimeout(function(){var p=document.createElement('pre');p.id='__nexus_report';p.style.display='none';
  p.textContent=JSON.stringify(R);(document.body||document.documentElement).appendChild(p)},9600);
})();"""


def _browser() -> str | None:
    return next((b for b in BROWSERS if Path(b).is_file()), None)


def _remap(errors: list, offsets: dict) -> list:
    """Dokumentzeilen eingebetteter Skripte auf die Zeilen der Originaldatei zurückrechnen."""
    out = []
    for err in errors:
        def fix(m):
            name, line, col = m.group(1), int(m.group(2)), m.group(3)
            start = offsets.get(name)
            return f"@{name}:{line - start + 1 if start and line >= start else line}:{col}"
        text = re.sub(r"@([\w.-]+\.js):(\d+):(\d+)", fix, str(err))

        def fix_html(m):
            # Parse-Fehler eingebetteter Skripte meldet Chrome mit der HTML-Datei.
            line = int(m.group(2))
            for start, end, name in offsets.get("__ranges__", []):
                if start <= line <= end:
                    return f"@{name}:{line - start + 1}:{m.group(3)}"
            return m.group(0)
        out.append(re.sub(r"@([\w.-]+\.html?):(\d+):(\d+)", fix_html, text))
    return out


def _inline_scripts(source: str, run_dir: Path, offsets: dict | None = None) -> str:
    """Lokale <script src> in der Prüfkopie einbetten: file://-Skripte gelten als fremde Herkunft,
    Chrome meldet deren Fehler sonst nur als "Script error." ohne Datei und Zeile."""
    def repl(match):
        src = match.group(2)
        if re.match(r"^[a-z]+:", src, re.I) or src.startswith("//"):
            return match.group(0)
        target = (run_dir / src.split("?")[0]).resolve()
        if run_dir.resolve() not in target.parents or not target.is_file():
            return match.group(0)
        body = target.read_text(encoding="utf-8", errors="replace").replace("</script", "<\\/script")
        return "<script" + match.group(1) + match.group(3) + ">" + body + "\n//# sourceURL=" + src + "\n</script>"
    pattern = re.compile(r"<script([^>]*?)\ssrc=[\"']([^\"']+)[\"']([^>]*)>\s*</script>", re.I)
    out, last = [], 0
    for match in pattern.finditer(source):
        out.append(source[last:match.start()])
        replaced = repl(match)
        if offsets is not None and replaced != match.group(0):
            # Der Skriptkörper beginnt in derselben Zeile wie das <script>-Tag.
            start = "".join(out).count("\n") + 1
            name = match.group(2).split("/")[-1].split("?")[0]
            offsets[name] = start
            offsets.setdefault("__ranges__", []).append((start, start + replaced.count("\n"), name))
        out.append(replaced)
        last = match.end()
    out.append(source[last:])
    return "".join(out)


def _inject(source: str, harness: str = "__nexus_harness.js") -> str:
    tags = ('<meta http-equiv="Content-Security-Policy" content="' + html.escape(CSP, quote=True) + '">'
            '<script src="' + harness + '"></script>')
    match = re.search(r"<head[^>]*>", source, re.I)
    if match:
        return source[:match.end()] + tags + source[match.end():]
    match = re.search(r"<html[^>]*>", source, re.I)
    if match:
        return source[:match.end()] + "<head>" + tags + "</head>" + source[match.end():]
    return tags + source


def _syntax(run_dir: Path, files: list[str]) -> list[dict]:
    out = []
    if not Path(NODE).is_file():
        return [{"file": "*", "ok": None, "error": "NODE_UNAVAILABLE"}]
    for rel in files:
        if not rel.lower().endswith(".js"):
            continue
        proc = subprocess.run([NODE, "--check", rel], cwd=str(run_dir), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        err = "\n".join(l for l in (proc.stderr or "").splitlines() if l.strip())[:1500]
        out.append({"file": rel, "ok": proc.returncode == 0, "error": err if proc.returncode else ""})
    return out


def _chrome(browser: str, run_dir: Path, args: list[str], proxy_bypass: bool = False) -> subprocess.CompletedProcess:
    profile = run_dir / (".profile-" + uuid.uuid4().hex[:6])
    argv = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-sync", "--disable-background-networking", "--mute-audio",
            "--proxy-server=http://127.0.0.1:9",
            *([] if proxy_bypass else ["--proxy-bypass-list=<-loopback>"]),
            "--user-data-dir=" + str(profile), *args]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    try:
        out, err = proc.communicate(timeout=WALL_SECONDS)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out, err = proc.communicate(timeout=10)
        return subprocess.CompletedProcess(argv, -1, out, err)
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def _report(dom: subprocess.CompletedProcess) -> dict:
    text = (dom.stdout or b"").decode("utf-8", "replace")
    match = re.search(r'<pre id="__nexus_report"[^>]*>(.*?)</pre>', text, re.S)
    if not match:
        return {}
    try:
        return json.loads(html.unescape(match.group(1)))
    except ValueError:
        return {}


CRITERIA_VERSION = 3


def _gameplay(samples: list[dict], scenarios: dict) -> dict:
    """Spielmechanik über echte Szenarien im laufenden Spiel (Zustandsvertrag `state`).

    Momentaufnahmen allein lassen sich umgehen (Gegner außerhalb des Bildes, Punkte ohne Treffer);
    deshalb werden Treffer, Steuerung und Kollision gezielt herbeigeführt und ihre Wirkung gemessen.
    """
    games = {s.get("t"): s.get("game") for s in samples if s.get("game")}
    sc = scenarios or {}

    def in_view(g):
        return (g and isinstance(g.get("px"), (int, float)) and isinstance(g.get("py"), (int, float))
                and -5 <= g["px"] <= 965 and -5 <= g["py"] <= 605)
    return {
        "game_state_readable": bool(games),
        "enemies_appear": any((g.get("enemies_visible") or 0) >= 1 for g in games.values()),
        "shooting_works": any((g.get("lasers") or 0) >= 1 for g in games.values()),
        "player_stays_in_view": bool(games.get("t4800")) and bool(games.get("t6800"))
                                and in_view(games["t4800"]) and in_view(games["t6800"]),
        "laser_hits_enemy": (sc.get("hit") or {}).get("passed") is True,
        "movement_stops_on_release": (sc.get("move") or {}).get("passed") is True,
        "enemy_collision_costs_life": (sc.get("crash") or {}).get("passed") is True,
    }


def _criteria(report: dict, syntax: list[dict], idle: dict | None = None, sources: str = "") -> dict:
    samples = report.get("samples") or []
    idle_t = {s.get("t"): (s.get("canvas") or {}).get("hash") for s in (idle or {}).get("samples") or []}
    canvases = [s.get("canvas") for s in samples if s.get("canvas")]
    first = canvases[0] if canvases else None
    hashes = {c.get("hash") for c in canvases}
    frames = {s.get("t"): s.get("frames", 0) for s in samples}
    by_t = {s.get("t"): (s.get("canvas") or {}).get("hash") for s in samples}
    # Animation = sichtbare Canvas-Veränderung über die Zeit (rAF- oder Timer-Schleife).
    return {
        "no_syntax_errors": all(s.get("ok") is not False for s in syntax),
        "no_runtime_errors": not report.get("errors"),
        "canvas_present": bool(first) and first.get("w", 0) >= 200 and first.get("h", 0) >= 150,
        "canvas_not_blank": any((c.get("distinct") or 0) >= 2 for c in canvases),
        "animates": len(hashes) >= 2 and by_t.get("t1600") != by_t.get("t1000"),
        "keyboard_controls": (report.get("key_listeners") or 0) >= 1 and (report.get("keys_sent") or 0) >= 10,
        "alive_after_input": (by_t.get("t6800") != by_t.get("t4800")
                              or frames.get("t6800", 0) > frames.get("t4800", 0) + 10),
        "network_blocked": report.get("net") in ("BLOCKED", "pending"),
        # Hintergrund + mindestens zwei Vordergrundfarben (headless rendert ohne Kantenglättung).
        "visual_content": any((c.get("distinct") or 0) >= 3 for c in canvases),
        # Gleicher Zufallsstartwert und gleiche virtuelle Zeit: unterscheidet sich das Bild mit
        # Eingaben vom Lauf ohne Eingaben, wirkt die Steuerung tatsächlich auf das Spiel.
        "input_changes_game": bool(idle_t) and any(by_t.get(t) is not None and idle_t.get(t) is not None
                                                   and by_t.get(t) != idle_t.get(t) for t in ("t4800", "t6800")),
        **_gameplay(samples, report.get("scenarios") or {}),
    }


def check_web(root, rel: str) -> dict:
    clean = code_path(rel)
    if clean is None or not clean.lower().endswith(".html"):
        raise SandboxError("WEB_ENTRY_NOT_ALLOWED")
    base = Path(root).resolve()
    entry = _inside(base, clean)
    if entry is None or not entry.is_file():
        raise SandboxError("WEB_ENTRY_MISSING")
    browser = _browser()
    if not browser:
        raise SandboxError("BROWSER_UNAVAILABLE")
    project = entry.parent
    run_dir = WEB_RUNS / ("WEB-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    run_dir.mkdir(parents=True)
    try:
        files, hashes = [], {}
        for item in sorted(project.rglob("*")):
            if item.is_symlink() or not item.is_file() or item.suffix.lower().lstrip(".") not in WEB_EXT:
                continue
            rel_item = item.relative_to(project).as_posix()
            if len(files) >= 40:
                raise SandboxError("PROJECT_TOO_LARGE")
            target = run_dir / rel_item
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, target)
            files.append(rel_item)
            hashes[rel_item] = hashlib.sha256(item.read_bytes()).hexdigest()
        syntax = _syntax(run_dir, files)
        (run_dir / "__nexus_harness.js").write_text(HARNESS.replace("__SEND_INPUT__", "true"), encoding="utf-8")
        (run_dir / "__nexus_idle.js").write_text(HARNESS.replace("__SEND_INPUT__", "false"), encoding="utf-8")
        page = run_dir / entry.name
        offsets: dict = {}
        inlined = _inline_scripts(entry.read_text(encoding="utf-8", errors="replace"), run_dir, offsets)
        page.write_text(_inject(inlined), encoding="utf-8")
        idle_page = run_dir / ("__nexus_idle_" + entry.name)
        idle_page.write_text(_inject(inlined, "__nexus_idle.js"), encoding="utf-8")
        url = page.resolve().as_uri()
        started = time.monotonic()
        dom = _chrome(browser, run_dir, ["--virtual-time-budget=" + str(VIRTUAL_MS), "--dump-dom", url])
        report = _report(dom)
        idle = _report(_chrome(browser, run_dir, ["--virtual-time-budget=" + str(VIRTUAL_MS), "--dump-dom",
                                                   idle_page.resolve().as_uri()])) if report else {}
        evidence_dir = base / EVIDENCE_ROOT / project.name
        evidence_dir.mkdir(parents=True, exist_ok=True)
        shot = evidence_dir / ("check-" + time.strftime("%Y%m%d-%H%M%S") + ".png")
        _chrome(browser, run_dir, ["--virtual-time-budget=3500", "--window-size=960,720",
                                   "--screenshot=" + str(shot), url])
        if report:
            report["errors"] = _remap(report.get("errors") or [], offsets)
        sources = "\n".join((project / f).read_text(encoding="utf-8", errors="replace")
                            for f in files if f.lower().endswith(".js"))
        criteria = _criteria(report, syntax, idle, sources) if report else {"report_collected": False}
        passed = bool(report) and all(criteria.values())
        return {
            "path": clean, "project": project.name, "browser": Path(browser).name, "files": files,
            "sha256": hashes, "report_collected": bool(report), "passed": passed, "criteria": criteria,
            "errors": (report.get("errors") or [])[:20], "console_errors": (report.get("console_errors") or [])[:20],
            "syntax": syntax, "frames": report.get("frames"), "samples": report.get("samples"),
            "scenarios": report.get("scenarios"),
            "key_listeners": report.get("key_listeners"), "keys_sent": report.get("keys_sent"),
            "network": report.get("net"), "screenshot": shot.relative_to(base).as_posix() if shot.is_file() else None,
            "screenshot_sha256": hashlib.sha256(shot.read_bytes()).hexdigest() if shot.is_file() else None,
            "browser_exit": dom.returncode, "duration_s": round(time.monotonic() - started, 2),
            "isolation": "chromium_renderer_sandbox+dead_proxy+csp_connect_none+fresh_profile",
            "criteria_version": CRITERIA_VERSION,
        }
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def verify_served(url: str, evidence_dir: Path) -> dict:
    """Ausgelieferte Vorschau unter ihrer echten CSP im Browser laden.

    Nur genau die NEXUS-Vorschau ist erreichbar (Proxy-Ausnahme für diesen Host:Port);
    jedes andere Ziel läuft in den toten Proxy. Erfasst Konsolen-/CSP-Fehler und Screenshot.
    """
    from urllib.parse import urlparse
    browser = _browser()
    if not browser:
        raise SandboxError("BROWSER_UNAVAILABLE")
    parsed = urlparse(url)
    if parsed.hostname != "127.0.0.1" or not parsed.path.startswith("/preview/"):
        raise SandboxError("PREVIEW_URL_NOT_ALLOWED")
    run_dir = WEB_RUNS / ("SRV-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    run_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    shot = evidence_dir / ("preview-" + time.strftime("%Y%m%d-%H%M%S") + ".png")
    try:
        bypass = ["--proxy-bypass-list=" + parsed.hostname + ":" + str(parsed.port)]
        logs = _chrome(browser, run_dir, [*bypass, "--enable-logging=stderr", "--v=0",
                                          "--virtual-time-budget=4000", "--dump-dom", url], proxy_bypass=True)
        _chrome(browser, run_dir, [*bypass, "--virtual-time-budget=4000", "--window-size=960,720",
                                   "--screenshot=" + str(shot), url], proxy_bypass=True)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    err = (logs.stderr or b"").decode("utf-8", "replace")
    console = [l.strip()[:300] for l in err.splitlines()
               if "CONSOLE" in l and re.search(r"Uncaught|Refused|Error|blocked", l)]
    dom = (logs.stdout or b"").decode("utf-8", "replace")
    return {"url": url, "loaded": "<canvas" in dom.lower() or "<body" in dom.lower(),
            "console_errors": console[:20], "passed": ("<body" in dom.lower()) and not console,
            "screenshot": shot.relative_to(ROOT).as_posix() if shot.is_file() else None,
            "screenshot_sha256": hashlib.sha256(shot.read_bytes()).hexdigest() if shot.is_file() else None}
