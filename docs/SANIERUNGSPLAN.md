# NEXUS Master Home – Sanierungsplan

Stand 2026-11-07 · Basis: Branch `fix/backend-security` (3 Commits vor `main`) · Analyse durch 4 Subagenten, Kernbefunde gegengeprüft.

## Ausgangslage

| Kennzahl | Wert |
|---|---|
| Python-Code | ~55k Zeilen in 30 Paketen + 4,4k Top-Level |
| Frontend | `app.js` 3,5k Zeilen (ein IIFE), 9 HTML-Seiten |
| Tests | 1033 gesammelt · **939 passed · 68 failed · 26 skipped · 4 Collection-Errors** · 125 s |
| Größte Brocken | `master/controller.py` 5,5k (eine Methode 2,3k Zeilen) · `chat/server.py` 1,8k (~150 Routen in 3 if-Ketten) |

**Ursachen der 68 Fails:** ~30 Windows-Annahmen (PowerShell-Worker, C:/G:-Sperren) · ~16 fehlende Dateien in `workspace/` (Tests nicht isoliert) · 3 Netz-Defaults · Rest ungeklärt/echt.
**Collection-Errors:** `scipy`, `cv2`, `win32crypt` fehlen; `skills`-Import kaputt.

### Kritische Befunde (Sicherheit)

1. **Keine Auth auf gefährlichen Routen:** `/api/chat` (Agent-Loop mit Shell/Datei-Tools), `/api/shell/run`, `/api/files/write`, `/api/os/*`, `/api/contacts` u. a. — und `start_nexus.sh:9` bindet standardmäßig an `0.0.0.0`.
2. **Terminal-Guard ist Windows-Regex:** auf Linux läuft `sh -c <string>`; `sudo`, `rm -rf`, `dd`, `ssh` gelten als `SAFE_WRITE` (`terminal/guard.py:72-147`, `service.py:165-177`).
3. **Autobuild darf jede Datei schreiben** – inkl. Guards, Tests, sich selbst; Schreibschutz kennt nur Laufwerk C: (`autobuild/loop.py:155,172`, `safety/paths.py:36-43`). Startet automatisch mit dem Server (`server.py:1745`). LLM-Testcode läuft ungesandboxt.
4. **XSS in einer Origin, die Shell-APIs rufen darf:** ~6 `innerHTML`-Stellen mit Dateinamen/Fehlertexten (`app.js:1339,1973,2009,2145,2221,2264`), keine CSP.
5. **Guards per Env abschaltbar** (`NEXUS_AGENT_GUARD=0`, `NEXUS_PHASE2_*`), Loopback-Erkennung über fehlende Proxy-Header, Static-Serving mit `startswith`-Präfixprüfung, kein Host-Check (DNS-Rebinding), kein Body-Limit.
6. **Secrets in jedem Kindprozess:** Bridge-Token wird in `os.environ` gesetzt und vererbt (`bridge/auth.py:78,87`).

### Strukturbefunde

- 3 parallele Rechtesysteme (`bridge/permissions`, `terminal/guard`, `swarm/core`), 5 Orchestrierungen (bridge-tasks, tasks, crew, swarm, autobuild) + Hermes-Loop
- 8 eigene Ollama-Clients, `11434` und `qwen3.5:9b` vielfach hartkodiert, kein echter Fallback
- Kein `logging`, ~36 verschluckte Exceptions, `str(e)` an Clients
- Zustand als JSON; `atomic.py` gut, aber ≥10 Stellen schreiben nicht atomar
- Daemon-Threads ohne geordnetes Shutdown
- Stubs: `media/`, `downloads/` (SSRF, alles im RAM), `generation/`, `multimodal/`, `files/`; Dublette `integration/` vs. `integrations/`
- Linux: keine `*_all.sh`, kein systemd, kein Offline-TTS, `health_watch` ruft `.ps1`

---

## Grundsatzentscheidung

**Windows und Linux sind beide vollwertige Zielplattformen.** Windows-Code wird nicht entfernt. Der Kern läuft als Docker-Container auf beiden Systemen identisch; alles Plattformspezifische wandert in einen nativen Host-Agent (siehe Zielarchitektur); jede Stufe muss auf beiden Systemen grün sein (CI-Matrix ab Stufe 0).

**Kein Neuschreiben, sondern Umbau nach Strangler-Muster.** 939 grüne Tests sind das wertvollste Asset – sie sichern jeden Schritt ab. Reihenfolge: erst Zugang dicht, dann Ausführung einsperren, dann Plattform, dann Struktur, dann Aufräumen. Jede Stufe endet mit grüner Testsuite und einem Commit auf einem eigenen Branch.

Modellwahl je Schritt: **L** = lokales Modell zuerst (`hermes-qwen3.8-27b-coder` über `.local-learning/local_first.py`), Claude prüft und korrigiert; jeder Lauf landet als Trainingsdatum in `.local-learning/data/runs.jsonl` · **H** = haiku, **S** = sonnet, **O** = opus (Planung/Review im Hauptthread).

---

## Stufe 0 – Sicherheitsnetz (½ Tag)

| # | Schritt | Modell | Fertig wenn |
|---|---|---|---|
| 0.1 | ✅ `e1841c1` – `start_nexus.sh` **und** `start_nexus.ps1` (`$HostAddr`, Z. 5): Default `127.0.0.1`, LAN/Tailscale-Bind nur explizit | H | beide Skripte, README angepasst |
| 0.2 | ✅ `8cf32f9` – Autobuild-Autostart aus (`server.py:1745`), nur per Flag `NEXUS_AUTOBUILD=1` | H | Server startet ohne Autobuild-Thread |
| 0.3 | ✅ `3178f13` – Test-Baseline festhalten: Liste der 68 Fails als `tests/KNOWN_FAILURES.txt`, `pytest.mark.ollama` registrieren | H | `pytest` reproduzierbar 939/68 |
| 0.4 | ✅ `b129ae8` – `pyproject.toml` mit `ruff` (nur Fehlerregeln, kein Formatieren) + Baseline | H | `ruff check` läuft, Baseline committet |
| 0.5 | ✅ `b428185` – Phantomordner `G:\meine-ki\…\tmp` löschen, `accept_studio_*` auf `tmp_path` umstellen | H | Ordner weg, kein Neuanlegen |
| 0.6 | ✅ `ee2db13` – **CI-Matrix** (GitHub Actions `ubuntu-latest` + `windows-latest`, Python 3.11 + 3.14) mit der Baseline aus 0.3 als erwarteten Fails je Plattform | H | beide Jobs laufen |

## Stufe 1 – Zugang dicht ✅ (2026-11-07, Branch `sanierung/stufe-1`)

Ergebnis Smoke-Test über LAN-IP ohne Login: UI-Seiten 200, `/api/memory`, `/api/files/list`, `/api/contacts`, `/media/…`, `POST /api/shell/run` → 401; fremder Host → 421; kaputtes JSON → 400; mit Token bzw. nach `/login` → 200. Nebenbei gefunden und behoben: Clipboard-Tool-Absturz (`93c2c2d`).

| # | Schritt | Modell | Fertig wenn |
|---|---|---|---|
| 1.1 | ➡️ **verschoben nach 4.6** (Auth läuft jetzt über eine Pfad-Policy in `api_auth.check`, die Tabelle kommt zusammen mit dem Framework-Wechsel) – **Routing-Tabelle** statt if-Ketten: `@route("POST", "/api/shell/run", policy=...)`; Handler bleiben, nur die Verteilung wird deklarativ. Mechanisch, Route für Route | O plant, S führt aus | Alle ~150 Routen registriert, Tests grün |
| 1.2 | ✅ `6a0a862, 1b4f50f` – **Auth-Middleware, deny by default:** jede `/api/*` braucht Session-Cookie (UI) oder Bearer (Handy/Bridge); Ausnahmen explizit in der Tabelle. Loopback über Peer-Adresse, nie über Header | S | Test: jede Route ohne Credentials → 401, außer Allowlist |
| 1.3 | ✅ `70fc2c8` – Host-Header-Allowlist (DNS-Rebinding), Body-Limit, 400 bei kaputtem JSON, generische Fehlerantworten + Request-ID | S | Tests für alle vier |
| 1.4 | ✅ `e0928e2` – Static/File-Serving auf `Path.resolve()` + `is_relative_to` | L | Traversal-Test |
| 1.5 | ✅ `4fdd1f4, 231062e, c86b3c7` – XSS: alle `innerHTML` mit Fremddaten → `textContent`/`createElement`; strikte CSP auf allen UI-Seiten | S | grep findet keine unescapte Verkettung; CSP-Header gesetzt |
| 1.6 | ✅ `5163257` – Off-Schalter der Guards entfernen (`NEXUS_AGENT_GUARD`, `NEXUS_PHASE2_*`) | H | Flags weg, Tests grün |

## Zielarchitektur (ab Stufe 2)

```
Browser / Handy ──Token──► nexus-core        (Docker, Linux-Image, auf Windows + Linux identisch)
                              │  Web/API, Agenten, Policy, Memory, LLM-Client, piper-TTS
                              ├──► Ollama            (nativ auf dem Host – GPU)            host.docker.internal:11434
                              ├──► nexus-runner      (Docker, ohne Internet, ohne Secrets) Terminal, Code-Ausführung, Autobuild-Tests
                              └──► nexus-host-agent  (nativ, Windows bzw. Linux)            Desktop, Apps, Audio, adb, Host-Shell
```

- **Core** bekommt **keinen** Docker-Socket und keine Host-Rechte. Alles, was den Rechner anfasst, geht über den Host-Agent – mit Token, Capability-Liste und Freigabe.
- **Runner** ist die eine Sandbox für beide Betriebssysteme (ersetzt bubblewrap/AppContainer): `cap_drop: ALL`, `no-new-privileges`, Read-only-Rootfs, nur `/work` beschreibbar, Limits für CPU/RAM/PIDs, nur über ein internes Netz mit Core verbunden.
- **Host-Agent** ist die Plattform-Schicht als eigener Prozess: einziger Ort mit `os.name`/`sys.platform`-Weichen, Backends `windows.py` / `posix.py`. Fehlt er (z. B. reiner Server), läuft Core mit reduzierten Fähigkeiten weiter.
- Voraussetzung Windows: Docker Desktop mit WSL2 (oder Podman). Workspace als **benanntes Volume**, nicht als NTFS-Bind-Mount (Performance, Dateirechte).

## Stufe 2 – Container & Ausführung einsperren (4–5 Tage) · Branch `sanierung/stufe-2`

**Lokal:** rootless Podman 6.1 + podman-compose auf `gusCachyBuero` (seit 2026-11-07). Image baut und startet wie in der CI (`podman compose build`; Podman ignoriert `HEALTHCHECK` im OCI-Format → `BUILDAH_FORMAT=docker`).

| # | Schritt | Modell | Fertig wenn |
|---|---|---|---|
| 2.1 | **Eine Policy-Engine** statt drei: Tool-Registry mit Schema + Stufe (READ / SAFE_WRITE / EXEC / DANGER) und **Ziel** (`runner` / `host`), Konfig außerhalb des beschreibbaren Workspace | O plant, S baut | `bridge/permissions`, `terminal/guard`, swarm-Level delegieren dorthin |
| 2.1a | ✅ Bridge-Freigaben (approve/deny) nur durch Menschen im UI: same-origin + `X-NEXUS-UI` + HttpOnly-Cookie (`nx_term_ui` lokal, `nx_session` entfernt) – vorher konnte ein Agent mit dem Bridge-Token seine eigene PRIVILEGED-Anfrage genehmigen | O | `test_bridge_human_approval.py`, Live-Test 403/200 |
| 2.1b | ✅ Geschützte Pfade (`nexus/safety/protected.py`): Secrets, Policy-Dateien, Tokens, Approvals, Autobuild-Gitdirs, `~/.ssh` u. a. für **alle** Agenten-Dateiwerkzeuge tabu (Bridge, Hermes, AETHER, Master/ProjectFS, Swarm, Code-Sandbox, Datei-API, `/media`), auch über Symlinks/`..`/Groß-Klein. **Grenze:** Shell/Python-Code kann per Pfadprüfung nicht abgesichert werden – im Container-Modus läuft er im Runner (sieht keine Secrets), nativ bleibt nur der Regex-Guard | L → S verdrahtet | `test_safety_protected.py`, `test_protected_wiring.py` |
| 2.2 | ✅ `07dbb6a`, `724efea` Image + Compose (Linux, host-Netz), CI-Smoke-Test grün (Start nach 2 s, non-root, read-only); Windows-Bridge-Modus offen – **Core-Image + `compose.yaml`:** `python:3.11-slim`, Non-root-User, `NEXUS_HOME=/data` (Volume), Port nur `127.0.0.1:8299`, Ollama über `host.docker.internal` (Linux: `extra_hosts: host-gateway`), Healthcheck, `restart: unless-stopped` | L → S prüft | `docker compose up -d` startet Core auf Linux; Windows-Lauf in CI |
| 2.2a | ✅ `8b8d363` – **Übergang:** Terminal-Guard kennt POSIX-Systembefehle und Secret-Pfade (vorher SAFE_WRITE) | L | Tests `test_terminal_guard_posix.py` |
| 2.3 | ✅ Runner ohne Netz (`network_mode: none`), Exec-API über Unix-Socket im Volume `runner-sock`, NDJSON-Streaming, Abbruch kappt die Verbindung → Prozessgruppe weg; Terminal fail-closed im Runner-Modus (`NEXUS_RUNNER_SOCKET`); lokal mit Podman + CI geprüft. `code_sandbox` folgt mit 2.4 – **Runner-Container** mit kleiner Exec-API (Token, nur internes Netz, ohne Internet); Terminal und `code_sandbox` führen dort aus statt per `sh -c` auf dem Host. Runner jederzeit per `compose restart` zurücksetzbar | O plant, S baut | Test: Befehl im Runner sieht weder Host-Dateisystem noch Netz noch Secrets |
| 2.4 | ✅ Arbeitskopie unter `workspace/runner/autobuild/<id>` (flacher Klon bzw. Kopie), Git-Verzeichnis nur im Core (`--git-dir`/`--work-tree`, fsmonitor/hooks aus), Allowlist `nexus/autobuild/scope.py` vor dem Schreiben **und** vor dem Commit (inkl. `.git`-Einschleusung, Symlinks), Tests im Runner, Ergebnis Branch `autobuild/<id>` bzw. Patch (Container ohne `.git`) – **Autobuild im Runner:** Git-Worktree auf dem Volume, Pfad-Allowlist (`nexus/` minus `safety/`, Policy, `autobuild/`), Tests im Runner, Ergebnis als Branch – **Merge nur durch dich** | O plant, S baut | Autobuild kann Guard-Dateien nachweislich nicht ändern |
| 2.5 | ✅ `5284727` – Secrets nur im Core (Compose-Secrets / `env_file`), Runner bekommt keine; Bridge-Token nicht mehr in `os.environ`; Kindprozesse mit Env-Allowlist | L → S prüft | Test: Runner-Prozess sieht keinen Token |
| 2.6 | ✅ `0901a4f`, `08c09a4` (auch HTTP-Tool des Agenten und Aether-Fetch) – `downloads/`: SSRF-Schutz (private Netze sperren), Streaming, Größenlimit – oder in `nexusall` aufgehen lassen | L | Test gegen 127.0.0.1/192.168.x |

## Stufe 3 – Host-Agent Windows + Linux (5–6 Tage)

| # | Schritt | Modell | Fertig wenn |
|---|---|---|---|
| 3.1 | **Zentrale Config** (eine dataclass, lädt `.env`, validiert beim Start) für Core und Host-Agent; ~47 verstreute `os.environ`-Zugriffe darauf umstellen; Modell/Ollama-URL nur dort | S | grep `11434`/`qwen3.5` nur noch in Config |
| 3.2 | **Host-Agent-Gerüst** `nexus_host/`: eigener Prozess, Token-Auth, Capability-Endpoint (`/caps`), Backends `windows.py`/`posix.py`. Bindet an Loopback bzw. auf Linux an die Docker-Bridge-IP, nie an `0.0.0.0` | O plant, S baut | Core fragt `/caps` ab, Agent läuft auf beiden Systemen |
| 3.3 | **Fähigkeiten umziehen** (je ein Schritt): Desktop/Maus/Tastatur/Zwischenablage (`computer/`), Apps & URLs (`os_actions`), Medientasten, adb (`phone_actions` + `nexus_os` – ein adb-Client), Audio-Ein/Ausgabe + Wakeword (`voice/`), Host-Shell nur mit Allowlist + Freigabe (PowerShell bzw. bash) | L → S prüft, je Fähigkeit | Core enthält keine Plattform-Weiche mehr |
| 3.4 | **Geschützte Pfade statt C:-Sperre** im Host-Agent: `protected_roots`/`allowed_roots` je Plattform, `resolve()` + `is_relative_to` | L → S prüft | ehemalige Gruppe-B-Fails grün auf beiden Systemen |
| 3.5 | **TTS:** piper im Core erzeugt Audio, Wiedergabe über Host-Agent; SAPI bleibt Fallback im Windows-Agent; SAPI-Dublette (`tts.py`/`speak_pc.py`) zusammenlegen | L → S prüft | Sprachausgabe ohne Internet auf beiden |
| 3.6 | Abhängigkeiten: Core komplett im Image; Host-Agent schlank mit Extras je OS (`pywin32; sys_platform=='win32'`); `cv2`/`scipy`/`win32crypt` lazy | L | 0 Collection-Errors |
| 3.7 | Tests isolieren: Workspace per Fixture auf `tmp_path`, Marker `integration`/`ollama`/`windows_only`/`posix_only`; CI: Core-Tests **im Container**, Host-Agent-Tests in der Matrix Ubuntu + Windows | S | Gruppen A+C behoben, Lauf ohne echten `workspace/` |
| 3.8 | **Betrieb:** `docker compose up -d` auf beiden Systemen; Host-Agent-Autostart per systemd-User-Unit bzw. Windows-Aufgabenplanung; `start/stop/status_nexus.{sh,ps1}` werden dünne Wrapper um compose + Agent; `health_watch` entfällt (Compose-Healthcheck + Restart) | L → S prüft | Start/Stop/Status identisch auf beiden Systemen |
| 3.9 | Hartkodierte Pfade (`C:\Users\gustl\…`, `G:\meine-ki\…`, WinGet-ffmpeg) → Config bzw. `shutil.which` | L | grep findet keine Benutzer-/Laufwerkspfade |

## Stufe 4 – Struktur (1–2 Wochen, inkrementell)

| # | Schritt | Modell | Fertig wenn |
|---|---|---|---|
| 4.1 | `logging` überall, `except Exception: pass` → mindestens `log.exception` | H (Sweep) | 0 stumme Excepts im Kern |
| 4.2 | **Ein `LLMClient`** (httpx, Timeout, Retry mit Backoff, Streaming, echter Fallback); die 8 Clients darauf umstellen | S | ein Client, Tests grün |
| 4.3 | **Store-Schicht:** SQLite (WAL) für Zustand + Memory (FTS5 statt Voll-Reload-BM25, Vektoren per numpy/sqlite-vec) | O plant, S baut | Memory-Suche ohne Datei-Reload |
| 4.4 | **`controller.py` zerlegen:** Intent-Routing / Agent-Loop / Tool-Executor / Verifier / Streaming; Tool-Calls über Ollama Function-Calling statt Regex-Parser | O plant, S in Etappen | keine Methode > 150 Zeilen |
| 4.5 | **Eine Orchestrierung:** Task-Queue + Worker-Pool mit Lifecycle (Stop-Event, join, Prozesse killen); crew/swarm/tasks werden Agent-Profile darauf | O plant, S baut | sauberes Shutdown bei SIGTERM |
| 4.6 | **Routing-Tabelle + Wechsel auf Starlette/FastAPI** (aus 1.1): Route für Route aus den if-Ketten in Router-Funktionen; `api_auth.check` wird Middleware/Dependency | O plant, S/L in Etappen | `server.py` ohne if-Ketten, Tests grün |

## Stufe 5 – Aufräumen (laufend)

- Stubs löschen oder ausbauen (`media/`, `generation/`, `multimodal/`, `files/`), `integration/` → `integrations/`
- Dubletten: adb-Wrapper, Netz-Allowlist, Gefahrenwort-Regex, SAPI-Code
- Tests nach Modul benennen statt `_20261029`/`_008`
- `app.js` in ES-Module pro Panel aufteilen (kein Build-Schritt nötig)
- **Strikte CSP:** Inline-`<script>`-Blöcke (7 in `index.html`, je 1–2 in den anderen Seiten) und `onclick`-Attribute in Dateien/`addEventListener` verlegen, dann `'unsafe-inline'` aus `script-src` streichen
- Hartkodierte Personendaten (`Eddie`, `WoZiPC`, `C:\Users\gustl`) in Config/Prompts verschieben

---

## Entschieden

- 2026-11-07: **Windows und Linux vollwertig.** Kein Windows-Code wird gelöscht; Abstraktion über `nexus/platform/`, CI-Matrix auf beiden Systemen. PRs nach upstream (`Eddylike/nexus-master-home`) bleiben möglich.
- 2026-11-07: **Docker-Kern + nativer Host-Agent.** Core und Sandbox (Runner) als Container, Desktop/Audio/adb/Host-Shell über einen Host-Agent je OS. Ersetzt bubblewrap + AppContainer durch eine Sandbox für beide Systeme.
- 2026-11-07: **Lokal zuerst:** Ausführbare Schritte zuerst an das lokale Qwen, Claude prüft; Läufe werden als SFT/DPO-Daten gesammelt.
