"""Hermes-Webentwicklung: Ziel → Plan → HTML/CSS/JS → Browserprüfung → Korrektur → Vorschau.

NEXUS steuert, Hermes (lokales Modell) plant und programmiert. Jeder Schritt läuft über
registrierte Bridge-Aufgaben mit Beleg:
  create_file/read_file  Projektdateien im Codebereich (bytegenau zurückgelesen)
  hermes_check_web       objektive Browserprüfung (Fehler, Canvas, Animation, Tastatur)
  patch_file + read_file Korrektur anhand der ECHTEN Prüfausgabe (mit Sicherung)
  hermes_publish_preview nur bei bestandener, hash-identischer Prüfung → /preview/<projekt>/

Begrenzt: höchstens MAX_FIX_ROUNDS Korrekturen und MAX_MODEL_CALLS Modellaufrufe pro
Auftrag. Fortschritt liegt in workspace/state/hermes-web-order-<hash>.json; eine
Wiederaufnahme überspringt belegte Schritte und schreibt nichts doppelt.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "nexus.hermes-web-order.v1"
STATE_PREFIX = "hermes-web-order-"
CODE_DIR = "workspace/projects/hermes/code"
MAX_FIX_ROUNDS = 4
MAX_REGENERATIONS = 2
MAX_TOTAL_ROUNDS = 12  # absolute Obergrenze an Prüfrunden je Auftrag
MAX_MODEL_CALLS = 12
MAX_JS_BYTES = 30_000  # patch_file-Grenze für old_text (32 KB) mit Reserve

WEB_SIGNAL = re.compile(
    r"\b(html5?|css|javascript|js|canvas|"
    r"browser[- ]?(?:game|spiel|projekt|anwendung|app|oberfläche|oberflaeche)\w*|"
    r"webseite|website|web-?app\w*|webanwendung\w*|spiel\w*|game\w*|musikplayer|player)\b", re.I)
PYTHON_SIGNAL = re.compile(r"\bpython\b|\.py\b", re.I)
_WEB_NEGATED_BEFORE = re.compile(
    r"\b(?:kein\w*|ohne|weder|statt|anstatt|nicht)\s+"
    r"(?:(?!nur\b)[\w/-]+(?:\s+|/)){0,3}$", re.I)
_WEB_NEGATED_AFTER = re.compile(
    r"\s+(?:(?:ist|sind|wird|werden)\s+)?(?:nicht|keinesfalls)\s+"
    r"(?:benötigt|benoetigt|erforderlich|gebraucht|gewünscht|gewuenscht)\b", re.I)


def _has_web_target(text: str) -> bool:
    """Nur ein bejahtes Web-Artefakt ist ein Entwicklungsziel.

    Eine Browser-Erwähnung beschreibt oft eine Einschränkung oder den Zustand
    eines anderen Werkzeugs. Sie macht lokale Integrationen nicht zu Webprojekten.
    """
    for match in WEB_SIGNAL.finditer(text):
        before = re.split(r"[.!?\n;,]", text[:match.start()])[-1]
        if _WEB_NEGATED_BEFORE.search(before) or _WEB_NEGATED_AFTER.match(text, match.end()):
            continue
        return True
    return False


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_web_order(text: str) -> bool:
    """Spiel-/Web-Entwicklungsauftrag ohne ausdrückliches Python-Ziel."""
    from nexus.master.code_order import affirmative_build, write_negated, CREATE_INTENT
    low = str(text or "").lower()
    if not CREATE_INTENT.search(low) or write_negated(low) or PYTHON_SIGNAL.search(low):
        return False
    if re.match(r"\s*(?:wie|was|warum|wieso|welche[rs]?|kann(?:st)?\s+(?:du|man|ich)|erkl(?:ä|ae)r\w*)\b", low)             or low.rstrip().endswith("?"):
        return False
    return affirmative_build(low) and _has_web_target(low)


def _state_path(root: Path, goal: str) -> Path:
    directory = root / "workspace" / "state"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / (STATE_PREFIX + hashlib.sha256(goal.encode("utf-8")).hexdigest()[:16] + ".json")


def _load(path: Path):
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and value.get("schema") == SCHEMA else False
    except (OSError, ValueError):
        return False


def _save(path: Path, state: dict) -> None:
    from nexus.common.atomic import atomic_write_json
    state["updated_at"] = _utc()
    atomic_write_json(path, state)


def _stage_rounds(state: dict) -> int:
    """Korrekturrunden der aktuellen Kriterienstufe (nicht aller Stufen zusammen)."""
    rounds = state.get("rounds") or []
    base = state.get("round_base", 0)
    # Nur Hermes-Korrekturen zählen gegen das Budget; externe Eingriffe werden getrennt ausgewiesen.
    return sum(1 for r in rounds[base:] if r.get("patch")
               and not str(r["patch"].get("method") or "").startswith("external_agent"))


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower().replace("ä", "ae").replace("ö", "oe")
                   .replace("ü", "ue").replace("ß", "ss")).strip("-")
    return value[:40].strip("-") or "web-projekt"


INDEX_TEMPLATE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<main>
<h1>{title}</h1>
<canvas id="game" width="960" height="600" tabindex="0"></canvas>
<p class="hint">{controls}</p>
</main>
<script src="game.js"></script>
</body>
</html>
"""

STYLE_TEMPLATE = """html, body { margin: 0; height: 100%; background: #05060f; color: #d8f6ff;
  font-family: "Segoe UI", system-ui, sans-serif; }
main { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 12px; }
h1 { margin: 4px 0; font-size: 22px; letter-spacing: 4px; color: #6ff3ff;
  text-shadow: 0 0 12px #00e5ff, 0 0 24px #7a3cff; }
canvas { max-width: 100%; height: auto; border: 1px solid #1e7bff; border-radius: 6px;
  box-shadow: 0 0 24px rgba(0, 229, 255, .35), inset 0 0 40px rgba(122, 60, 255, .2); background: #000; outline: none; }
.hint { margin: 0; font-size: 13px; color: #8fb8ff; opacity: .85; }
"""

JS_RULES = (
    "Write ONE complete, self-contained browser game file game.js in plain modern JavaScript (no modules, "
    "no imports, no libraries, no external assets, no images, no audio files, no fetch, no XMLHttpRequest, "
    "no localStorage, no cookies, no eval). The page already contains <canvas id=\"game\" width=\"960\" "
    "height=\"600\"> and loads game.js at the end of <body>. Requirements:\n"
    "1. const canvas = document.getElementById('game'); const ctx = canvas.getContext('2d');\n"
    "2. Keyboard: window.addEventListener('keydown'/'keyup') with e.key / e.code; support Arrow keys AND "
    "WASD for movement, Space to shoot, Enter or R to restart after game over. Call e.preventDefault() for "
    "arrows and space.\n"
    "3. The game starts running immediately (no click needed) and the main loop uses requestAnimationFrame "
    "every frame, drawing the whole scene every frame (also on the game-over screen).\n"
    "4. Draw everything with canvas shapes: moving star background, player ship, enemies/asteroids, "
    "lasers, particle explosions, HUD with score, lives/energy and level. Difficulty increases over time.\n"
    "5. Collision detection, game over when lives reach 0, restart resets the state.\n"
    "6. Keep it robust: declare every variable before use, no undefined functions, guard against "
    "NaN, wrap nothing in try/catch that hides errors. About 250-450 lines.\n"
    "7. Use `let` (not const) for every variable that is reassigned later (arrays that are reset, score, "
    "lives, level, state).\n"
    "9. Timers: use accumulators, e.g. state.spawnTimer += dt; if (state.spawnTimer >= 0.8) { "
    "state.spawnTimer = 0; spawnEnemy(); } and the same for the shot cooldown. NEVER test float time with "
    "`% x === 0`. The first enemy must appear within 1 second.\n"
    "8. Never destructure `state`. Always access game data as state.player, state.enemies, state.lasers, "
    "state.particles, state.stars, state.score ... in update() AND draw().\n"
    "Mandatory structure (extend it into the full game, keep these function names):\n"
    "const canvas = document.getElementById('game'); const ctx = canvas.getContext('2d');\n"
    "const keys = {}; window.addEventListener('keydown', e => { keys[e.code] = true; "
    "if (['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Space'].includes(e.code)) e.preventDefault(); "
    "if ((e.code === 'Enter' || e.code === 'KeyR') && state.over) reset(); });\n"
    "window.addEventListener('keyup', e => { keys[e.code] = false; });\n"
    "let state; function reset() { state = { player: { x: 480, y: 520 }, enemies: [], lasers: [], particles: [], "
    "stars: [/* ~80 stars {x,y,s} */], score: 0, lives: 3, level: 1, over: false, spawnTimer: 0, shotTimer: 0, "
    "time: 0 }; }\n"
    "function hit(a, b, r) { return Math.abs(a.x - b.x) < r && Math.abs(a.y - b.y) < r; }\n"
    "function update(dt) {\n"
    "  state.time += dt; const p = state.player, sp = 320 * dt;  // FIXED speed per second, never accumulate\n"
    "  if (keys.ArrowLeft || keys.KeyA) p.x -= sp; if (keys.ArrowRight || keys.KeyD) p.x += sp;\n"
    "  if (keys.ArrowUp || keys.KeyW) p.y -= sp; if (keys.ArrowDown || keys.KeyS) p.y += sp;\n"
    "  p.x = Math.max(15, Math.min(945, p.x)); p.y = Math.max(15, Math.min(585, p.y));\n"
    "  state.shotTimer += dt; if (keys.Space && state.shotTimer >= 0.15) { state.shotTimer = 0; "
    "state.lasers.push({ x: p.x, y: p.y - 12 }); }\n"
    "  state.spawnTimer += dt; if (state.spawnTimer >= 0.8) { state.spawnTimer = 0; "
    "state.enemies.push({ x: 20 + Math.random() * 920, y: 10, speed: 100 + state.level * 15 }); }\n"
    "  for (const s of state.stars) { s.y += s.s * dt; if (s.y > 600) { s.y = 0; s.x = Math.random() * 960; } }\n"
    "  for (const l of state.lasers) l.y -= 520 * dt;      // lasers MUST move up every frame\n"
    "  for (const e of state.enemies) e.y += e.speed * dt; // enemies MUST move down every frame\n"
    "  for (const e of state.enemies) { for (const l of state.lasers) { if (!e.dead && !l.dead && hit(e, l, 18)) { "
    "e.dead = l.dead = true; state.score += 10; /* spawn explosion particles */ } }\n"
    "    if (!e.dead && hit(e, p, 22)) { e.dead = true; state.lives -= 1; if (state.lives <= 0) state.over = true; } }\n"
    "  state.enemies = state.enemies.filter(e => !e.dead && e.y < 620); state.lasers = state.lasers.filter(l => !l.dead "
    "&& l.y > -10);\n"
    "  /* update particles, level from score */\n"
    "}\n"
    "Keep this mechanic exactly (it is tested in the browser: shooting an enemy must raise the score and remove it, "
    "an enemy touching the player must cost a life, movement must stop when the key is released). Make the game "
    "your own with visuals: neon ship shape, glowing enemies/asteroids, star field, explosion particles, HUD.\n"
    "function draw() { /* clear + draw background, objects, particles, HUD; game-over text if state.over */ }\n"
    "let last = performance.now(); function loop(now) { const dt = Math.min(0.05, (now - last) / 1000) || 0.016; "
    "last = now; if (!state.over) update(dt); else state.time += dt; draw(); requestAnimationFrame(loop); }\n"
    "reset(); requestAnimationFrame(loop);\n"
    "Output ONLY the raw JavaScript source. No markdown fence, no explanation."
)


def _model(master, state: dict, messages: list, *, num_predict: int, json_mode: bool = False) -> dict:
    if state["model_calls"] >= MAX_MODEL_CALLS:
        return {"success": False, "error": "MODEL_CALL_LIMIT"}
    state["model_calls"] += 1
    llm = getattr(master, "llm", None)
    if llm is None:
        return {"success": False, "error": "MODEL_UNAVAILABLE"}
    kwargs = {"temperature": 0.2, "num_predict": num_predict}
    if json_mode:
        kwargs["format"] = "json"
    try:
        return llm.chat(messages, **kwargs)
    except TypeError:
        return llm.chat(messages)
    except Exception as exc:
        return {"success": False, "error": type(exc).__name__}


def _strip_fence(text: str) -> str:
    value = str(text or "").strip()
    fence = re.match(r"^```[A-Za-z0-9_+-]*\s*\n([\s\S]*?)\n?```\s*$", value)
    if fence:
        return fence.group(1).strip("\n") + "\n"
    inner = re.search(r"```(?:javascript|js)?\s*\n([\s\S]*?)\n```", value)
    return (inner.group(1) if inner else value).strip("\n") + "\n"


def _plan(master, state: dict) -> dict | None:
    from nexus.integrations.hermes_brain import _json_object
    reply = _model(master, state, [
        {"role": "system", "content":
            "You are Hermes, the NEXUS development planner. Plan a small browser game/app for the goal. "
            "Answer with exactly ONE JSON object, no prose: {\"title\":\"<short title>\",\"slug\":\"<a-z0-9->\","
            "\"concept\":\"<2 German sentences>\",\"controls\":\"<German one-line control hint>\","
            "\"features\":[\"<German feature>\", ...]} . Files are fixed: index.html, style.css, game.js "
            "(HTML5 canvas + plain JavaScript, no libraries, no network)."},
        {"role": "user", "content": state["goal"][:3000]}], num_predict=600, json_mode=True)
    data = _json_object(str(reply.get("reply") or "")) if reply.get("success") else None
    if not isinstance(data, dict):
        return None
    title = str(data.get("title") or "").strip()[:60] or "Hermes Webprojekt"
    named = re.search(r"\b(NEXUS\s+[A-ZÄÖÜ][A-ZÄÖÜ0-9 ]{2,40}[A-ZÄÖÜ0-9])\b", state["goal"])
    if named:
        title = named.group(1).strip()
    features = [str(f)[:120] for f in (data.get("features") or []) if isinstance(f, (str, int))][:8]
    return {"title": title, "slug": _slug(title), "concept": str(data.get("concept") or "")[:400],
            "controls": str(data.get("controls") or "Pfeiltasten/WASD bewegen, Leertaste schießt, "
                                                   "Enter startet neu.")[:160],
            "features": features, "model": reply.get("model")}


def _generate_js(master, state: dict, plan: dict, feedback: str = "") -> str | None:
    reply = _model(master, state, [
        {"role": "system", "content": JS_RULES},
        {"role": "user", "content": "Goal: " + state["goal"][:2500] + "\n\nTitle: " + plan["title"]
         + "\nConcept: " + plan["concept"] + "\nFeatures: " + "; ".join(plan["features"])
         + (("\n\nA previous version FAILED a real browser test. Avoid these problems:\n" + feedback)
            if feedback else "")}],
        num_predict=7000)
    if not reply.get("success"):
        return None
    js = _strip_fence(reply.get("reply"))
    return js if len(js.strip()) > 200 else None


_ERROR_HINTS = (
    (r"Assignment to constant variable", "A variable declared with const is reassigned. Change that declaration "
     "from const to let."),
    (r"is not defined", "A name is used but never declared/defined. Declare it (let/const/function) before use."),
    (r"Cannot read propert(?:y|ies) of (?:undefined|null)", "An object is undefined/null when accessed. Initialize "
     "it before the loop or guard the access."),
    (r"is not a function", "Something is called that is not a function (typo or wrong method name). Fix the call."),
    (r"before initialization", "A let/const/class is used before its declaration line. Move the declaration up."),
    (r"Unexpected token|Unexpected end|missing \)|Invalid or unexpected", "Syntax error: fix brackets/quotes."),
)
_FAILED_HINTS = {
    "canvas_not_blank": "Nothing visible is drawn on the canvas. Draw background and objects every frame.",
    "animates": "The canvas does not change over time: the loop must UPDATE the game state (move stars, enemies, "
                "lasers, player) and then redraw, every frame, via requestAnimationFrame(loop).",
    "keyboard_controls": "No keyboard listener. Add window.addEventListener('keydown', ...) and 'keyup'.",
    "alive_after_input": "The loop stops after keyboard input (crash or no requestAnimationFrame in a branch). "
                         "Always call requestAnimationFrame(loop) at the end of every frame, also on game over.",
    "canvas_present": "Use the existing <canvas id=\"game\" width=\"960\" height=\"600\">.",
    "input_changes_game": "Keyboard input has no visible effect. In update(dt) move the player with "
                          "keys.ArrowLeft||keys.KeyA, keys.ArrowRight||keys.KeyD, keys.ArrowUp||keys.KeyW, "
                          "keys.ArrowDown||keys.KeyS (keys is filled with e.code) and fire lasers on keys.Space.",
    "game_state_readable": "Keep ALL game data in the single top-level `let state = {...}` object "
                           "(state.player with x/y, state.enemies array, state.lasers array, state.score, state.lives).",
    "enemies_appear": "No enemy is ever VISIBLE on the canvas (0..960 x 0..600). A check like `state.time % 0.8 === 0` "
                      "is never true for float time. Use an accumulator in update(dt): state.spawnTimer += dt; "
                      "if (state.spawnTimer >= 0.8) { state.spawnTimer = 0; state.enemies.push({x: 20 + Math.random()*920, "
                      "y: 10, speed: 120}); } and move every enemy DOWN with e.y += e.speed * dt (not sideways).",
    "shooting_works": "Space does not create lasers. Use a cooldown accumulator: state.shotTimer = "
                      "(state.shotTimer || 0) + dt; if (keys.Space && state.shotTimer >= 0.2) { state.shotTimer = 0; "
                      "state.lasers.push({x: state.player.x, y: state.player.y, speed: 500}); } (no energy limit "
                      "that blocks the first shot).",
    "player_stays_in_view": "The player leaves the screen. Move the player with a fixed speed per second while a "
                            "key is held (no accumulating velocity) and clamp x to 0..960 and y to 0..600.",
    "score_logic_present": "There is no scoring. When a laser hits an enemy remove both, spawn particles and do "
                           "state.score += 10; when an enemy hits the player do state.lives -= 1.",
    "laser_hits_enemy": "A fired laser never reaches an enemy. In update(dt) lasers MUST move every frame: "
                        "for (const l of state.lasers) l.y -= 520 * dt; and enemies: for (const e of state.enemies) "
                        "e.y += e.speed * dt; then for each enemy/laser pair use hit(e, l, 18) -> e.dead = l.dead = true; "
                        "state.score += 10; and filter out dead objects.",
    "movement_stops_on_release": "Movement must use a FIXED speed per second while the key is held "
                                 "(p.x += 320 * dt), no accumulating dx/dy velocity, so the ship stops when the key is "
                                 "released; clamp p.x to 15..945 and p.y to 15..585.",
    "enemy_collision_costs_life": "An enemy touching the player must cost a life: if (hit(e, state.player, 22)) "
                                  "{ e.dead = true; state.lives -= 1; if (state.lives <= 0) state.over = true; }",
    "visual_content": "The scene is too plain. Draw a star field (many small white/blue dots), a colored player "
                      "ship, colored enemies/asteroids, lasers, particles and HUD text in bright colors "
                      "(e.g. ctx.fillStyle = '#6ff3ff').",
}


def _context(current: str, errors: list) -> str:
    """Genau die Codezeilen um die vom Browser gemeldeten Fehlerstellen, nummeriert."""
    lines = current.splitlines()
    parts = []
    for err in errors[:3]:
        m = re.search(r"game\.js:(\d+):(\d+)", str(err))
        if not m:
            continue
        n = int(m.group(1))
        lo, hi = max(1, n - 8), min(len(lines), n + 4)
        parts.append(f"--- around line {n} ({err}) ---\n" + "\n".join(
            f"{i:4d}{'>>' if i == n else '| '} {lines[i - 1]}" for i in range(lo, hi + 1)))
    return "\n".join(parts)


def deterministic_repair(current: str, errors: list) -> tuple[str, list[str]]:
    """Nachvollziehbare Standardreparatur für eindeutig erkennbare Fehler (wie ein Linter-Autofix).

    Aktuell: "Assignment to constant variable" an Zeile:Spalte → die const-Deklaration genau
    dieses Bezeichners wird zu let. Gibt den neuen Text und die angewendeten Schritte zurück.
    """
    lines = current.splitlines(keepends=True)
    applied = []
    for err in errors:
        # "X is not defined", X wird an anderer Stelle aus einem Objekt destrukturiert
        # ({ ..., X, ... } = state): dieselbe Destrukturierung in der betroffenen Funktion ergänzen.
        nd = re.search(r"ReferenceError: ([A-Za-z_$][\w$]*) is not defined.*?game\.js:(\d+):\d+", str(err))
        if nd:
            name, row = nd.group(1), int(nd.group(2))
            source = names = None
            for line in lines:
                m = re.search(r"\b(?:let|const|var)\s*\{([^}]*)\}\s*=\s*([A-Za-z_$][\w$.]*)", line)
                if m and re.search(r"\b" + re.escape(name) + r"\b", m.group(1)):
                    source, names = m.group(2), m.group(1).strip()
                    break
            if source and 1 <= row <= len(lines):
                for i in range(row - 1, -1, -1):
                    fn = re.match(r"(\s*)(?:async\s+)?function\s+[\w$]+\s*\([^)]*\)\s*\{\s*$", lines[i])
                    if fn:
                        indent = fn.group(1) + "    "
                        lines.insert(i + 1, f"{indent}let {{ {names} }} = {source};\n")
                        applied.append(f"{{ {names} }} in Funktion ab Zeile {i + 1} aus {source} übernommen")
                        break
            continue
        m = re.search(r"Assignment to constant variable.*?game\.js:(\d+):(\d+)", str(err))
        if not m:
            continue
        row, col = int(m.group(1)), int(m.group(2))
        if not 1 <= row <= len(lines):
            continue
        text = lines[row - 1]
        # V8 zeigt auf den Zuweisungsoperator; der Bezeichner steht direkt davor (sonst ab der Spalte).
        before = re.search(r"([A-Za-z_$][\w$]*)\s*(?:[-+*/%]?=|\+\+|--)?\s*$", text[:max(0, col - 1)])
        ident = before or re.match(r"[A-Za-z_$][\w$]*", text[max(0, col - 1):])
        if not ident:
            continue
        name = ident.group(1) if before else ident.group(0)
        ident_re = r"\b" + re.escape(name) + r"\b"
        decl = re.compile(r"\bconst(\s+" + re.escape(name) + r"\s*=|\s*\{[^}]*" + ident_re + r"[^}]*\}\s*="
                          r"|\s*\[[^\]]*" + ident_re + r"[^\]]*\]\s*=)")
        # Nächstgelegene Deklaration vor der Fehlerzeile (Gültigkeitsbereich), sonst irgendwo davor/danach.
        order = list(range(row - 1, -1, -1)) + list(range(row, len(lines)))
        for i in order:
            if decl.search(lines[i]):
                lines[i] = decl.sub(r"let\1", lines[i], count=1)
                applied.append(f"const → let für {name} (Zeile {i + 1})")
                break
    return "".join(lines), applied


def _fix_js(master, state: dict, current: str, check: dict) -> str | None:
    failed = [k for k, v in (check.get("criteria") or {}).items() if v is False]
    syntax = [s.get("error") for s in check.get("syntax") or [] if s.get("ok") is False]
    errors = list(check.get("errors") or []) + [e for e in syntax if e]
    hints = [h for pat, h in _ERROR_HINTS if any(re.search(pat, str(e)) for e in errors)]
    if not errors:
        hints += [_FAILED_HINTS[k] for k in failed if k in _FAILED_HINTS]
    findings = {"runtime_errors": check.get("errors"), "syntax_errors": syntax, "failed_criteria": failed,
                "console_errors": check.get("console_errors"), "frames": check.get("frames"),
                "key_listeners": check.get("key_listeners"), "how_to_fix": hints,
                # Gemessene Werte der Spielszenarien (z. B. Position vor/nach dem Loslassen der Taste,
                # Leben vor/nach Kollision) und sichtbare Gegner je Zeitpunkt: echte Ursachenhinweise.
                "measured_scenarios": check.get("scenarios"), "game_samples": check.get("game_samples")}
    focus = _context(current, errors)
    numbered = (("Exact failing lines (>> marks the reported line):\n" + focus + "\n\n") if focus else "") + \
        "\n".join(f"{i + 1:4d}| {line}" for i, line in enumerate(current.splitlines()))
    # Kleine, gezielte Änderungen (SEARCH/REPLACE) gelingen einem kleinen lokalen Modell deutlich
    # zuverlässiger als eine komplette Neuausgabe der Datei.
    reply = _model(master, state, [
        {"role": "system", "content": "You are Hermes fixing a bug in an existing browser game file game.js. "
         "A real headless-browser test produced the findings below. Fix the ROOT CAUSE at the reported lines. "
         "Return ONLY edit blocks in exactly this format (one or more), nothing else:\n"
         "<<<<<<< SEARCH\n<exact lines copied verbatim from game.js, without line numbers>\n=======\n"
         "<corrected lines>\n>>>>>>> REPLACE\n"
         "SEARCH must match the file exactly and be unique. Keep the game logic; plain JavaScript only; "
         "no network, no localStorage."},
        {"role": "user", "content": "Browser test findings (real output):\n" + json.dumps(findings, ensure_ascii=False)
         + "\n\n" + numbered[:24000]}], num_predict=2500)
    if not reply.get("success"):
        return None
    from nexus.master.nexus_order import EDIT_BLOCK
    text = str(reply.get("reply") or "").replace("\r\n", "\n")
    blocks = EDIT_BLOCK.findall(text)
    new = current
    numbered_line = re.compile(r"(?m)^ *\d+(?:>>|\|) ?")
    for search, replace in blocks:
        if new.count(search) != 1:
            # Häufiger Modellfehler: Zeilennummern der Anzeige mitkopiert -> aus SEARCH UND REPLACE entfernen.
            stripped = numbered_line.sub("", search)
            if new.count(stripped) != 1:
                continue
            search, replace = stripped, numbered_line.sub("", replace)
        new = new.replace(search, replace, 1)
    if new != current:
        return new
    # Fallback: vollständige Datei, falls das Modell trotzdem eine ganze Datei liefert.
    js = _strip_fence(text)
    return js if len(js.strip()) > 200 and "<<<<<<<" not in js and js != current else None


def syntax_error(js: str) -> str | None:
    """Vorschlag nur parsen (node --check, keine Ausführung); Fehlertext oder None."""
    import subprocess, tempfile, os
    from nexus.bridge.web_check import NODE as node
    if not Path(node).is_file():
        return None
    fd, tmp = tempfile.mkstemp(suffix=".js", prefix="nexus-check-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(js)
        proc = subprocess.run([node, "--check", tmp], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode == 0:
            return None
        return "\n".join(l for l in proc.stderr.splitlines() if l.strip()).replace(tmp, "game.js")[:800]
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _fix_js_checked(master, state: dict, current: str, check: dict) -> str | None:
    fixed = _fix_js(master, state, current, check)
    err = syntax_error(fixed) if fixed else None
    if fixed and err:
        retry = dict(check)
        retry["errors"] = list(check.get("errors") or []) + ["Your previous edit introduced a SyntaxError: " + err]
        fixed = _fix_js(master, state, current, retry)
        if fixed and syntax_error(fixed):
            return None
    return fixed


def tidy(js: str) -> str:
    """Deterministischer Aufräumschritt: mehrfach hintereinander eingefügte identische Zeilen
    (typisches Artefakt wiederholter Modell-Patches) auf eine Zeile reduzieren."""
    import itertools
    out = []
    for line, group in itertools.groupby(js.splitlines(keepends=True)):
        count = len(list(group))
        # Erst ab drei identischen Zeilen ein Patch-Artefakt; zwei gleiche Zeilen können gewollt sein.
        out.extend([line] if (count >= 3 and line.strip()) else [line] * count)
    return "".join(out)


def _failed_count(check: dict) -> int:
    crit = (check or {}).get("criteria") or {}
    return sum(1 for v in crit.values() if v is False) if crit else 99


def _write_initial(master, files: list[dict]) -> dict:
    from nexus.integrations.hermes_brain import HermesBrain
    return HermesBrain(master).apply_requirements(files)


def _replace_file(path: str, old: str, new: str) -> dict:
    """Korrektur als nachvollziehbarer Patch (Sicherung durch die Bridge), dann bytegenau zurücklesen."""
    from nexus.integrations.hermes_client import invoke
    patch = invoke({"operation": "submit", "task": "patch_file",
                    "params": {"path": path, "old_text": old, "new_text": new}})
    read = invoke({"operation": "submit", "task": "read_file", "params": {"path": path}}) if patch.get("success") else {}
    content = (read.get("result") or {}).get("content") if isinstance(read.get("result"), dict) else None
    return {"ok": bool(patch.get("success")) and content == new, "patch_task_id": patch.get("task_id"),
            "read_task_id": read.get("task_id"), "error": patch.get("error_code") or (None if content == new else "READBACK_MISMATCH"),
            "backup": ((patch.get("result") or {}).get("backup_snapshot") if isinstance(patch.get("result"), dict) else None)}


def _check(path: str) -> dict:
    from nexus.integrations.hermes_client import invoke
    res = invoke({"operation": "submit", "task": "hermes_check_web", "params": {"path": path}})
    data = res.get("result") if isinstance(res.get("result"), dict) else {}
    return {"task_id": res.get("task_id"), "success": bool(res.get("success")), "error": res.get("error_code"),
            "passed": data.get("passed") is True, "criteria": data.get("criteria"), "errors": data.get("errors"),
            "console_errors": data.get("console_errors"), "syntax": data.get("syntax"), "frames": data.get("frames"),
            "key_listeners": data.get("key_listeners"), "screenshot": data.get("screenshot"),
            "network": data.get("network"), "sha256": data.get("sha256"),
            "criteria_version": data.get("criteria_version"), "scenarios": data.get("scenarios"),
            "game_samples": [{"t": x.get("t"), "game": x.get("game")} for x in (data.get("samples") or [])
                             if isinstance(x, dict)][:6]}


def _progress(master, kind: str, **fields) -> None:
    hook = getattr(master, "_order_progress", None)
    if callable(hook):
        try:
            hook(kind, **fields)
        except Exception:
            pass


def run(master, goal: str, session: dict) -> dict:
    from nexus.master.evidence import blocked
    from nexus.common.atomic import file_lock

    text = str(goal or "").strip()
    if not text or len(text) > 6000:
        return blocked("WEB_GOAL_REQUIRED", "Für diesen Webauftrag fehlt ein eindeutiges Ziel.")
    root = Path(str(getattr(getattr(master, "project_fs", None), "root", "") or "."))
    path = _state_path(root, text)
    # Genau ein Lauf je Auftrag: ein zweiter Aufruf (Doppelklick, "weiter" während des Laufs,
    # zweiter Prozess) startet keine parallelen Werkzeugschritte.
    try:
        with file_lock(path.with_suffix(".run"), timeout=0.5):
            try:
                return _run_locked(master, text, session, root, path)
            except Exception as exc:
                # Abbruch dokumentieren: kein hängender RUNNING-Status, Fortsetzung bleibt möglich.
                state = _load(path)
                if isinstance(state, dict) and state.get("status") == "RUNNING":
                    state["status"] = "PARTIAL" if (state.get("writes") or {}).get("status") == "PASS" else "BLOCKED"
                    state["error"] = "WEB_ORDER_INTERRUPTED:" + type(exc).__name__
                    _save(path, state)
                raise
    except TimeoutError as exc:
        if "STATE_LOCK_TIMEOUT" not in str(exc) or not str(exc).endswith(".run"):
            raise
        return blocked("WEB_ORDER_ALREADY_RUNNING", "Dieser Webauftrag läuft bereits; es wird kein zweiter Lauf gestartet.")


def _run_locked(master, text: str, session: dict, root: Path, path: Path) -> dict:
    from nexus.master.evidence import blocked
    from nexus.integrations.hermes_client import invoke
    from nexus.bridge.web_check import CRITERIA_VERSION

    state = _load(path)
    if state is False:
        return blocked("WEB_ORDER_STATE_UNREADABLE", "Der gespeicherte Fortschritt ist nicht lesbar; nichts wird neu erzeugt.")
    if state is None:
        state = {"schema": SCHEMA, "goal": text, "plan_id": "WEB-" + hashlib.sha256(text.encode()).hexdigest()[:12],
                 "created_at": _utc(), "status": "PLANNED", "model_calls": 0, "plan": None, "files": None,
                 "writes": None, "rounds": [], "publish": None, "events": []}
        _save(path, state)
    last_check = (state["rounds"][-1].get("check") or {}) if state.get("rounds") else {}
    finished = (state.get("status") == "PASS" and last_check.get("passed")
                and (last_check.get("criteria_version") or 1) == CRITERIA_VERSION
                and (state.get("publish") or {}).get("published"))
    if finished:
        # Belegt abgeschlossen: nichts neu bauen, nichts erneut prüfen. Die gespeicherte finale
        # Prüfung und Vorschau werden für die Anzeige gemeldet (Belege, keine neue Ausführung).
        last_round = state["rounds"][-1]
        _progress(master, "check", round=last_round["round"], task_id=last_check.get("task_id"),
                  passed=True, criteria=last_check.get("criteria"), errors=[],
                  screenshot=last_check.get("screenshot"), criteria_version=last_check.get("criteria_version"),
                  fix_round=_stage_rounds(state), max_fix_rounds=MAX_FIX_ROUNDS, replayed=True,
                  method=(last_round.get("patch") or {}).get("method"))
        _progress(master, "preview", **state["publish"], replayed=True)
        return _render(state)
    if state.get("status") not in (None, "PLANNED"):
        # Wiederaufnahme: ein alter Endstatus (z. B. PASS einer früheren Kriterienstufe)
        # darf während des Laufs nicht stehen bleiben.
        state["resumed_from"] = {"status": state.get("status"), "error": state.get("error"), "at": _utc()}
    state["status"] = "RUNNING"
    state.pop("error", None)
    best = state.get("best") or {}
    if best and ((best.get("check") or {}).get("criteria_version") or 1) != CRITERIA_VERSION:
        # Bester Stand einer älteren Kriterienstufe ist kein Maßstab für die aktuelle Stufe.
        state["best"] = None
    _save(path, state)
    session["hermes_order"] = {"task": text, "requirements": [], "web": True}
    persist = getattr(master, "_persist_chat_order", None)
    if persist:
        try:
            persist()
        except Exception:
            pass

    # 1. Plan (einmal; Wiederaufnahme nutzt den gespeicherten Plan)
    if not state.get("plan"):
        _progress(master, "step", name="plan", status="running", detail="Hermes plant Projekt und Dateien")
        plan = _plan(master, state)
        if not plan:
            state["status"] = "BLOCKED"; state["error"] = "WEB_PLAN_NOT_DERIVED"; _save(path, state)
            return _render(state)
        existing = root / CODE_DIR / plan["slug"]
        if existing.exists():
            plan["slug"] = (plan["slug"][:33] + "-" + state["plan_id"][4:10]).strip("-")
        state["plan"] = plan
        _save(path, state)
    plan = state["plan"]
    base = CODE_DIR + "/" + plan["slug"]
    entry = base + "/index.html"
    _progress(master, "plan", plan_id=state["plan_id"], project=plan["slug"], title=plan["title"],
              files=[base + "/index.html", base + "/style.css", base + "/game.js"], features=plan["features"])

    # 2. Dateien programmieren und bytegenau schreiben
    if not state.get("writes") or state["writes"].get("status") != "PASS":
        js = (state.get("files") or {}).get("game.js")
        if not js:
            _progress(master, "step", name="code", status="running", detail="Hermes programmiert game.js")
            js = _generate_js(master, state, plan)
            if js and syntax_error(js):
                js = _generate_js(master, state, plan)  # genau ein Neuversuch bei nicht parsebarem Code
            if not js:
                state["status"] = "BLOCKED"; state["error"] = "WEB_CODE_NOT_GENERATED"; _save(path, state)
                return _render(state)
            if len(js.encode("utf-8")) > MAX_JS_BYTES:
                state["status"] = "BLOCKED"; state["error"] = "WEB_CODE_TOO_LARGE"; _save(path, state)
                return _render(state)
            state["files"] = {"index.html": INDEX_TEMPLATE.format(title=plan["title"], controls=plan["controls"]),
                              "style.css": STYLE_TEMPLATE, "game.js": js}
            _save(path, state)
        reqs = [{"path": base + "/" + name, "content": content} for name, content in state["files"].items()]
        res = _write_initial(master, reqs)
        state["writes"] = {"status": res.get("status"), "task_ids": res.get("task_ids") or [],
                           "verification": res.get("verification") or [], "error": res.get("error")}
        _save(path, state)
        _progress(master, "files", status=res.get("status"), task_ids=state["writes"]["task_ids"],
                  files=[r["path"] for r in reqs])
        if res.get("status") != "PASS":
            state["status"] = "BLOCKED"; state["error"] = "WEB_WRITE_" + str(res.get("error")); _save(path, state)
            return _render(state)

    # 3. Prüfen → echte Fehler zurück an Hermes → Patch → erneut prüfen (begrenzt)
    while True:
        if len(state["rounds"]) >= MAX_TOTAL_ROUNDS:
            state["error"] = state.get("error") or "ROUND_LIMIT"; break  # harte Obergrenze, nie endlos
        last = state["rounds"][-1] if state["rounds"] else None
        if last and (last.get("check") or {}).get("passed"):
            if (last["check"].get("criteria_version") or 1) == CRITERIA_VERSION or last.get("version_rechecked"):
                break
            # Nach älteren Kriterien bestanden: neu prüfen, Vorschau gilt erst nach erneutem Bestehen.
            last["check"]["passed"] = False
            last["check"]["passed_under_version"] = last["check"].get("criteria_version") or 1
            last["check"]["superseded_reason"] = "criteria_version_outdated"
            state["publish"] = None
            state["rounds"].append({"round": len(state["rounds"]), "patch": None, "check": None,
                                    "version_rechecked": True})
            # Neue Kriterienstufe: eigenes Korrekturbudget (Modellaufrufe bleiben gesamt begrenzt).
            state["round_base"] = len(state["rounds"]) - 1
            state["regenerations"] = 0
            state["best"] = None  # neue Stufe: Bestwert neu ermitteln
            # Eigenes Modellbudget je Kriterienstufe; die Gesamtzahl bleibt nachvollziehbar.
            state["model_calls_total"] = state.get("model_calls_total", 0) + state["model_calls"]
            state["model_calls"] = 0
            _save(path, state)
            last = state["rounds"][-1]
        if last and not last.get("check"):
            pass  # Prüfung dieses Standes fehlt noch (Wiederaufnahme)
        elif last and _stage_rounds(state) >= MAX_FIX_ROUNDS:
            state["error"] = state.get("error") or "FIX_ROUND_LIMIT"
            break
        elif last:
            # Korrektur auf Basis der echten Prüfausgabe
            if state["model_calls"] >= MAX_MODEL_CALLS:
                state["error"] = "MODEL_CALL_LIMIT"; break
            current = (root / base / "game.js").read_text(encoding="utf-8")
            # Ausgangspunkt der Korrektur ist der beste bisher geprüfte Stand (wenigste verfehlte
            # Kriterien); ein schlechterer Neuentwurf verdrängt keinen fast fertigen Stand.
            best = state.get("best") or {}
            use_best = bool(best.get("content")) and best.get("failed", 99) < _failed_count(last["check"])
            work = best["content"] if use_best else current
            work_check = best["check"] if use_best else last["check"]
            repaired, steps = deterministic_repair(work, work_check.get("errors") or [])
            if steps:
                # Eindeutig erkennbarer Fehler: nachvollziehbare Standardreparatur ohne Modellaufruf.
                fixed, how = repaired, "deterministisch: " + "; ".join(steps)
            else:
                _progress(master, "step", name="fix", status="running", round=len(state["rounds"]),
                          detail="Hermes korrigiert game.js anhand der Browserprüfung"
                                 + (" (Basis: bester Stand Runde " + str(best.get("round")) + ")" if use_best else ""))
                check = work_check
                # Zuerst gezielte Änderung (erhält bereits bestandene Kriterien), Neuentwurf nur als Rückfall.
                fixed, how = _fix_js_checked(master, state, work, check), "hermes_model"
                if (not fixed or fixed == work) and state.get("regenerations", 0) < MAX_REGENERATIONS:
                    # Kein punktuell behebbarer Fehler (z. B. Szene bewegt sich nicht): Hermes programmiert
                    # game.js mit dem echten Prüfbefund neu, nur parsebarer Code wird übernommen.
                    state["regenerations"] = state.get("regenerations", 0) + 1
                    failed = [k for k, v in (check.get("criteria") or {}).items() if v is False]
                    feedback = "\n".join([f"- {k}: {_FAILED_HINTS.get(k, '')}" for k in failed]
                                         + [f"- error: {e}" for e in (check.get("errors") or [])[:3]])
                    _progress(master, "step", name="code", status="running", round=len(state["rounds"]),
                              detail="Hermes programmiert game.js mit dem Prüfbefund neu")
                    fixed = _generate_js(master, state, state["plan"], feedback)
                    if fixed and syntax_error(fixed):
                        fixed = None
                    how = "hermes_regenerate"
            if not fixed or fixed == current:
                state["error"] = "WEB_FIX_NOT_GENERATED"; _save(path, state); break
            if len(fixed.encode("utf-8")) > MAX_JS_BYTES or len(current.encode("utf-8")) > 32_000:
                state["error"] = "WEB_CODE_TOO_LARGE"; _save(path, state); break
            cleaned = tidy(fixed)
            if cleaned != fixed and not syntax_error(cleaned):
                fixed = cleaned
            patch = _replace_file(base + "/game.js", current, fixed)
            patch["method"] = how
            state["rounds"].append({"round": len(state["rounds"]), "patch": patch, "check": None})
            _save(path, state)
            _progress(master, "patch", round=len(state["rounds"]) - 1, **{k: patch.get(k) for k in
                      ("ok", "patch_task_id", "read_task_id", "error")})
            if not patch["ok"]:
                state["error"] = "WEB_PATCH_" + str(patch.get("error")); _save(path, state); break
        else:
            state["rounds"].append({"round": 0, "patch": None, "check": None})
        _progress(master, "step", name="check", status="running", detail="Browserprüfung (headless, ohne Netzwerk)")
        check = _check(entry)
        state["rounds"][-1]["check"] = check
        if check.get("success") and _failed_count(check) < (state.get("best") or {}).get("failed", 99):
            state["best"] = {"round": state["rounds"][-1]["round"], "failed": _failed_count(check),
                             "content": (root / base / "game.js").read_text(encoding="utf-8"), "check": check}
        _save(path, state)
        _progress(master, "check", round=state["rounds"][-1]["round"], task_id=check["task_id"],
                  passed=check["passed"], criteria=check.get("criteria"), errors=(check.get("errors") or [])[:3],
                  screenshot=check.get("screenshot"), criteria_version=check.get("criteria_version"),
                  fix_round=_stage_rounds(state), max_fix_rounds=MAX_FIX_ROUNDS)
        if not check["success"]:
            state["error"] = "WEB_CHECK_" + str(check.get("error")); _save(path, state); break

    final = state["rounds"][-1]["check"] if state["rounds"] and state["rounds"][-1].get("check") else {}
    # 4. Geprüfte Vorschau veröffentlichen (nur mit bestandener, hash-identischer Prüfung)
    if final.get("passed") and not (state.get("publish") or {}).get("published"):
        _progress(master, "step", name="publish", status="running", detail="Geprüfte Vorschau veröffentlichen")
        res = invoke({"operation": "submit", "task": "hermes_publish_preview",
                      "params": {"path": entry, "check_task_id": final["task_id"]}})
        data = res.get("result") if isinstance(res.get("result"), dict) else {}
        state["publish"] = {"task_id": res.get("task_id"), "published": data.get("published") is True and bool(res.get("success")),
                            "url": data.get("url"), "served_check": data.get("served_check"),
                            "error": res.get("error_code") or ((res.get("result") or {}).get("message") if isinstance(res.get("result"), dict) else None)}
        _save(path, state)
        _progress(master, "preview", **state["publish"])
    published = (state.get("publish") or {}).get("published") is True
    state["status"] = "PASS" if final.get("passed") and published else ("PARTIAL" if state.get("writes", {}).get("status") == "PASS" else "BLOCKED")
    if state["status"] != "PASS" and not state.get("error"):
        state["error"] = "WEB_CHECK_NOT_PASSED" if not final.get("passed") else "PREVIEW_NOT_PUBLISHED"
    if state["status"] == "PASS":
        state.pop("error", None)
    _save(path, state)
    return _render(state)


def _evidence_ids(state: dict) -> tuple[list[str], list[str]]:
    """Gültige Endbelege vs. überholte Zwischenstände (frühere Prüfungen/Versionen)."""
    writes = state.get("writes") or {}
    rounds = state.get("rounds") or []
    final, superseded = [], []
    # Je Datei gilt der letzte Schreibbeleg (Erstellung oder letzte Korrektur); alles davor ist überholt.
    latest: dict[str, list[str]] = {}
    for v in writes.get("verification") or []:
        name = str(v.get("path") or "").rsplit("/", 1)[-1]
        latest[name] = [t for t in (v.get("create_task_id"), v.get("read_task_id")) if t]
    if not latest:
        final += [t for t in writes.get("task_ids") or [] if t]
    for r in rounds:
        p = r.get("patch")
        if not p:
            continue
        name = p.get("path") or "game.js"  # Korrekturen ohne Pfadangabe betreffen game.js
        superseded += latest.get(name, [])
        latest[name] = [t for t in (p.get("patch_task_id"), p.get("read_task_id")) if t]
    for ids in latest.values():
        final += ids
    checks = [r["check"]["task_id"] for r in rounds if r.get("check") and r["check"].get("task_id")]
    final += checks[-1:]
    superseded += checks[:-1]
    if (state.get("publish") or {}).get("task_id"):
        final.append(state["publish"]["task_id"])
    return final, superseded


def _render(state: dict) -> dict:
    plan = state.get("plan") or {}
    final, superseded = _evidence_ids(state)
    rounds = state.get("rounds") or []
    last = rounds[-1]["check"] if rounds and rounds[-1].get("check") else {}
    pub = state.get("publish") or {}
    lines = [f"WEB-ENTWICKLUNGSAUFTRAG ({state.get('plan_id')}): {plan.get('title') or '-'}"]
    if plan:
        lines.append(f"Projekt: {CODE_DIR}/{plan.get('slug')}/ (index.html, style.css, game.js)")
    w = state.get("writes") or {}
    if w:
        lines.append(f"Dateien geschrieben + bytegenau zurückgelesen: {w.get('status')} – Task-IDs: {', '.join(w.get('task_ids') or []) or '-'}")
    for r in rounds:
        if r.get("patch"):
            p = r["patch"]
            lines.append(f"Korrektur {r['round']} ({p.get('method') or 'hermes_model'}): patch_file {p.get('patch_task_id')} + read_file {p.get('read_task_id')} – {'OK' if p.get('ok') else p.get('error')}")
        c = r.get("check") or {}
        if c:
            failed = [k for k, v in (c.get("criteria") or {}).items() if v is False]
            version = c.get("criteria_version") or 1
            if c.get("superseded_reason") == "criteria_version_outdated":
                verdict = (f"BESTANDEN nach Kriterien v{c.get('passed_under_version') or version} – überholt, "
                           f"weil strengere Prüfkriterien gelten (erneut geprüft)")
            else:
                verdict = ("BESTANDEN" if c.get("passed") else "NICHT BESTANDEN") + f" (Kriterien v{version})"
            lines.append(f"Browserprüfung {r['round']}: {c.get('task_id')} – {verdict}"
                         + (f" – offen: {', '.join(failed)}" if failed else "")
                         + (f" – Fehler: {'; '.join((c.get('errors') or [])[:2])}" if c.get("errors") else ""))
    if last.get("screenshot"):
        lines.append("Screenshot der Prüfung: " + str(last["screenshot"]))
    if pub:
        if pub.get("published"):
            lines.append(f"Geprüfte Vorschau: http://127.0.0.1:8299{pub.get('url')} (Task {pub.get('task_id')}, "
                         f"im Browser unter echter CSP fehlerfrei geladen)")
        else:
            lines.append(f"Vorschau nicht veröffentlicht: {pub.get('error')} (Task {pub.get('task_id')})")
    from nexus.bridge.web_check import CRITERIA_VERSION
    external = sum(1 for r in rounds if str((r.get("patch") or {}).get("method") or "").startswith("external_agent"))
    if external:
        lines.append(f"Externe Korrekturen (nicht Hermes): {external} – Herkunft im Ablauf je Korrektur angegeben")
    lines.append(f"Kriterienstufe v{CRITERIA_VERSION}: Hermes-Korrekturrunden {_stage_rounds(state)}/{MAX_FIX_ROUNDS}, "
                 f"Modellaufrufe {state.get('model_calls')}/{MAX_MODEL_CALLS}"
                 + (f" (gesamt über alle Stufen: {len([r for r in rounds if r.get('patch')])} Korrekturen, "
                    f"{state.get('model_calls_total', 0) + state.get('model_calls', 0)} Modellaufrufe)"
                    if state.get("round_base") else ""))
    lines.append("Stufe: " + ("geprüfte Vorschau (keine produktive NEXUS-Integration)" if pub.get("published")
                              else "Entwicklungsprojekt (nicht veröffentlicht)"))
    status = state.get("status") or "BLOCKED"
    error = state.get("error")
    if status == "PASS" and not (last.get("passed") and pub.get("published")):
        # Kein PASS ohne bestandene aktuelle Prüfung UND veröffentlichte Vorschau (z. B. alter
        # Endstatus eines unterbrochenen Laufs).
        status, error = "PARTIAL", error or "PASS_WITHOUT_CURRENT_EVIDENCE"
    if status == "RUNNING":
        lines.append("RUNNING: Auftrag in Bearbeitung (Zwischenstand, kein Abschluss).")
    else:
        lines.append(f"{status}" + (f": {error}" if error else ": Spiel geprüft und als Vorschau startbar."))
    return {"success": status == "PASS", "status": status, "intent": "hermes_work", "reply": "\n".join(lines),
            "error": error, "run_id": state.get("plan_id"),
            "data": {"execution_status": status, "task_ids": final, "superseded_task_ids": superseded,
                     "order_type": "web_order", "plan_id": state.get("plan_id"), "project": plan.get("slug"),
                     "preview_url": pub.get("url") if pub.get("published") else None,
                     "checks": [r.get("check") for r in rounds], "provenance": "persisted_bridge_tasks"}}
