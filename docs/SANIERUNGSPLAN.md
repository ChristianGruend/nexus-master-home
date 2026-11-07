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

**Windows und Linux sind beide vollwertige Zielplattformen.** Windows-Code wird nicht entfernt, sondern hinter eine Plattform-Schicht gelegt; jede Stufe muss auf beiden Systemen grün sein (CI-Matrix ab Stufe 0).

**Kein Neuschreiben, sondern Umbau nach Strangler-Muster.** 939 grüne Tests sind das wertvollste Asset – sie sichern jeden Schritt ab. Reihenfolge: erst Zugang dicht, dann Ausführung einsperren, dann Plattform, dann Struktur, dann Aufräumen. Jede Stufe endet mit grüner Testsuite und einem Commit auf einem eigenen Branch.

Modellwahl je Schritt: **H** = haiku, **S** = sonnet, **O** = opus (Planung/Review im Hauptthread).

---

## Stufe 0 – Sicherheitsnetz (½ Tag)

| # | Schritt | Modell | Fertig wenn |
|---|---|---|---|
| 0.1 | `start_nexus.sh` **und** `start_nexus.ps1` (`$HostAddr`, Z. 5): Default `127.0.0.1`, LAN/Tailscale-Bind nur explizit | H | beide Skripte, README angepasst |
| 0.2 | Autobuild-Autostart aus (`server.py:1745`), nur per Flag `NEXUS_AUTOBUILD=1` | H | Server startet ohne Autobuild-Thread |
| 0.3 | Test-Baseline festhalten: Liste der 68 Fails als `tests/KNOWN_FAILURES.txt`, `pytest.mark.ollama` registrieren | H | `pytest` reproduzierbar 939/68 |
| 0.4 | `pyproject.toml` mit `ruff` (nur Fehlerregeln, kein Formatieren) + Baseline | H | `ruff check` läuft, Baseline committet |
| 0.5 | Phantomordner `G:\meine-ki\…\tmp` löschen, `accept_studio_*` auf `tmp_path` umstellen | H | Ordner weg, kein Neuanlegen |
| 0.6 | **CI-Matrix** (GitHub Actions `ubuntu-latest` + `windows-latest`, Python 3.11 + 3.14) mit der Baseline aus 0.3 als erwarteten Fails je Plattform | H | beide Jobs laufen |

## Stufe 1 – Zugang dicht (2–3 Tage)

| # | Schritt | Modell | Fertig wenn |
|---|---|---|---|
| 1.1 | **Routing-Tabelle** statt if-Ketten: `@route("POST", "/api/shell/run", policy=...)`; Handler bleiben, nur die Verteilung wird deklarativ. Mechanisch, Route für Route | O plant, S führt aus | Alle ~150 Routen registriert, Tests grün |
| 1.2 | **Auth-Middleware, deny by default:** jede `/api/*` braucht Session-Cookie (UI) oder Bearer (Handy/Bridge); Ausnahmen explizit in der Tabelle. Loopback über Peer-Adresse, nie über Header | S | Test: jede Route ohne Credentials → 401, außer Allowlist |
| 1.3 | Host-Header-Allowlist (DNS-Rebinding), Body-Limit, 400 bei kaputtem JSON, generische Fehlerantworten + Request-ID | S | Tests für alle vier |
| 1.4 | Static/File-Serving auf `Path.resolve()` + `is_relative_to` | H | Traversal-Test |
| 1.5 | XSS: alle `innerHTML` mit Fremddaten → `textContent`/`createElement`; strikte CSP auf allen UI-Seiten | S | grep findet keine unescapte Verkettung; CSP-Header gesetzt |
| 1.6 | Off-Schalter der Guards entfernen (`NEXUS_AGENT_GUARD`, `NEXUS_PHASE2_*`) | H | Flags weg, Tests grün |

## Stufe 2 – Ausführung einsperren (3–4 Tage)

| # | Schritt | Modell | Fertig wenn |
|---|---|---|---|
| 2.1 | **Eine Policy-Engine** statt drei: Tool-Registry mit Schema + Stufe (READ / SAFE_WRITE / EXEC / DANGER), Konfig außerhalb des beschreibbaren Workspace | O plant, S baut | `bridge/permissions`, `terminal/guard`, swarm-Level delegieren dorthin |
| 2.2 | **Terminal:** argv statt `sh -c`, Allowlist (Binary + Argumentmuster via `shlex`), alles andere nur per Freigabe. getrennte Allowlists für POSIX-Shell und PowerShell. Sandbox je Plattform: Linux **bubblewrap** (Mount-NS, nur Workspace rw, Netz optional aus), Windows **AppContainer + Job Object** (vorhandenes `bridge/appcontainer.py` ausbauen) | S | Tests je Plattform: `sudo`/`rm -rf`/`$(…)` bzw. `Remove-Item -Recurse`/`Start-Process -Verb RunAs`/`iex` → Freigabe/Block |
| 2.3 | **Autobuild:** Pfad-Allowlist (`nexus/` minus `safety/`, `terminal/guard`, `autobuild/`), jede Änderung als Commit in einem **Git-Worktree**, Tests dort in der Plattform-Sandbox aus 2.2 ohne Netz/Secrets, Merge nur durch dich | O plant, S baut | Autobuild kann Guard-Dateien nachweislich nicht ändern |
| 2.4 | Kindprozesse mit minimalem Env (Allowlist), Bridge-Token nicht mehr in `os.environ`, Secret-Dateien `0600` erzwingen | S | Test: Subprozess sieht keinen Token |
| 2.5 | `downloads/`: SSRF-Schutz (private Netze sperren), Streaming, Größenlimit – oder in `nexusall` aufgehen lassen | H | Test gegen 127.0.0.1/192.168.x |

## Stufe 3 – Plattform-Schicht Windows + Linux (4–5 Tage)

| # | Schritt | Modell | Fertig wenn |
|---|---|---|---|
| 3.1 | **Zentrale Config** (eine dataclass, lädt `.env`, validiert beim Start); alle ~47 verstreuten `os.environ`-Zugriffe darauf umstellen; Modell/Ollama-URL nur dort | S | grep `11434`/`qwen3.5` nur noch in Config |
| 3.2 | **Plattform-Schicht** `nexus/platform/` mit Backends `windows.py` / `posix.py`: ProcessRunner, Shell (PowerShell-Worker bzw. bash), Prozess-Kill (`taskkill` bzw. `killpg`), App-Start (`os.startfile` bzw. `xdg-open`), Desktop-Automation, Secrets (DPAPI bzw. `keyring`/Secret Service). Aller Code mit `os.name`/`sys.platform` (56 Stellen) zieht dorthin | O plant, S baut | keine Plattform-Weiche mehr außerhalb `nexus/platform/` |
| 3.2b | **Geschützte Pfade statt C:-Sperre:** `safety/paths.py` bekommt pro Plattform konfigurierbare `protected_roots` (Windows: `C:\Windows`, Profile …; Linux: `/etc`, `/usr`, `~/.ssh` …) und `allowed_roots` (Workspace), geprüft mit `resolve()` + `is_relative_to` | S | Gruppe B (~14 Fails) auf beiden Plattformen grün |
| 3.3 | Abhängigkeiten in `pyproject.toml`: Kern + Extras (`[voice]`, `[studio]`, `[mail]`), Windows-Pakete mit Marker (`pywin32; sys_platform=='win32'`); Imports von `cv2`/`scipy`/`win32crypt` lazy | H | 0 Collection-Errors auf beiden Plattformen |
| 3.4 | Tests isolieren: `NEXUS_ROOT`/Workspace per Fixture auf `tmp_path`, Marker `integration`/`ollama`/`windows_only`/`posix_only` (skip statt fail auf der falschen Plattform); Windows-Tests (PowerShell-Worker, C:-Logik) bleiben und laufen im Windows-CI-Job | S | Gruppen A+C behoben, Lauf ohne echten `workspace/` |
| 3.5 | **Betrieb mit gleichem Funktionsumfang:** Linux systemd-User-Units (nexus, gateway, wakeword, `Restart=on-failure`) + `*_all.sh` mit gleichen Funktionen wie die `.ps1`; Windows behält `*_all.ps1`, optional Aufgabenplanung für Autostart. `health_watch` ruft plattformabhängig das richtige Restart. Workspace über `NEXUS_HOME` (`platformdirs`: `%LOCALAPPDATA%\nexus` bzw. `~/.local/share/nexus`) | S | Start/Stop/Status/Restart auf beiden Systemen identisch |
| 3.6 | Offline-TTS als Backend in der Plattform-Schicht: **piper** auf beiden Systemen (gleiche Stimme), SAPI bleibt Windows-Fallback; SAPI-Dublette (`tts.py`/`speak_pc.py`) zusammenlegen | H | TTS ohne Internet auf beiden |
| 3.7 | Hartkodierte Pfade (`C:\Users\gustl\…`, `G:\meine-ki\…`, WinGet-ffmpeg) → Config bzw. `shutil.which` | H | grep findet keine Benutzer-/Laufwerkspfade im Code |

## Stufe 4 – Struktur (1–2 Wochen, inkrementell)

| # | Schritt | Modell | Fertig wenn |
|---|---|---|---|
| 4.1 | `logging` überall, `except Exception: pass` → mindestens `log.exception` | H (Sweep) | 0 stumme Excepts im Kern |
| 4.2 | **Ein `LLMClient`** (httpx, Timeout, Retry mit Backoff, Streaming, echter Fallback); die 8 Clients darauf umstellen | S | ein Client, Tests grün |
| 4.3 | **Store-Schicht:** SQLite (WAL) für Zustand + Memory (FTS5 statt Voll-Reload-BM25, Vektoren per numpy/sqlite-vec) | O plant, S baut | Memory-Suche ohne Datei-Reload |
| 4.4 | **`controller.py` zerlegen:** Intent-Routing / Agent-Loop / Tool-Executor / Verifier / Streaming; Tool-Calls über Ollama Function-Calling statt Regex-Parser | O plant, S in Etappen | keine Methode > 150 Zeilen |
| 4.5 | **Eine Orchestrierung:** Task-Queue + Worker-Pool mit Lifecycle (Stop-Event, join, Prozesse killen); crew/swarm/tasks werden Agent-Profile darauf | O plant, S baut | sauberes Shutdown bei SIGTERM |
| 4.6 | Optional: Wechsel auf Starlette/FastAPI, sobald die Routing-Tabelle (1.1) steht – dann fast mechanisch | S | – |

## Stufe 5 – Aufräumen (laufend)

- Stubs löschen oder ausbauen (`media/`, `generation/`, `multimodal/`, `files/`), `integration/` → `integrations/`
- Dubletten: adb-Wrapper, Netz-Allowlist, Gefahrenwort-Regex, SAPI-Code
- Tests nach Modul benennen statt `_20261029`/`_008`
- `app.js` in ES-Module pro Panel aufteilen (kein Build-Schritt nötig)
- Hartkodierte Personendaten (`Eddie`, `WoZiPC`, `C:\Users\gustl`) in Config/Prompts verschieben

---

## Entschieden

- 2026-11-07: **Windows und Linux vollwertig.** Kein Windows-Code wird gelöscht; Abstraktion über `nexus/platform/`, CI-Matrix auf beiden Systemen. PRs nach upstream (`Eddylike/nexus-master-home`) bleiben möglich.
