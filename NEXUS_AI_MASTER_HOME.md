# NEXUS AI MASTER HOME

**Stand:** 2026-09-30 Europe/Berlin  
**Ort:** `G:\meine-ki\nexus-ai` auf WoZiPC  
**Ziel:** Perfektes lokales AI-Zuhause — Desktop-Hub + Handy-Call `/m` + Crew + Self-Improve + Health + Panels + Live-Aktivität.

Kein Theater. Kein Test-Marathon. RAM schonen (kein paralleler Schwerlast-LLM).

---

## Vision

Eddie öffnet `/` und landet im **Master Home**: sofort Anrufen, Crew, Self-Improve, Health, Live-Aktivität und alle Panels — ohne Menü-Suche. Handy bleibt `/m` (Anruf-Loop).

## Arbeitsteilung

| Spur | Wer | Fokus |
|------|-----|--------|
| **UI** | zipfeglatscha + Nexus | Home-Panel, Welcome-Link, Hub-Tiles, Activity live |
| **Tools** | Nexus-self | bestehende APIs/OS; Ollama-Timeout 300 |
| **Media** | halten | Player/Galerie/Streams nicht revertieren |
| **QA** | kurz | ein Health — kein Marathon |
| **zipfeglatscha** | Stabsführung | Plan, Timeout, Hub-Actions, Mirror, Report |
| **Nexus-self** | lokal | Brief lesen, kleine Lücken, Self-Improve |

**Nicht revertieren:** Diagnose, Crew, Voice/Call `/m`.

## Checklist

### Fundament
- [x] `NEXUS_AI_MASTER_HOME.md`
- [x] Ollama-Timeout **300** (`Config.OLLAMA_TIMEOUT` + `ollama_client`)
- [x] Live-Aktivität: `GET /api/activity` + Activity-Panel (nicht überschreiben)

### Home UI
- [x] Side-Nav **Home** + **Aktivität**
- [x] `homePanel` + `homeHubGrid` (Panel-Tiles)
- [x] Hub-Actions: **Anrufen /m**, **Self-Improve**, **Health** (zusätzlich)
- [x] Welcome-Link → Home-Hub
- [x] Cache-Bust `?v=masterhome2`

### Betrieb
- [x] Timeout-Fix ohne schweren Extra-Prozess
- [x] Nexus Chat-Brief gesendet (1x UI-Misroute; Folgeversuche unter RAM abgebrochen)
- [x] Health einmal (Score 100)
- [x] Box-Mirror sync (/workspace/nexus_ai_master/nexus-ai)

## Dateien

| Datei | Rolle |
|-------|--------|
| `NEXUS_AI_MASTER_HOME.md` | dieser Plan |
| `nexus/common/config.py` | `OLLAMA_TIMEOUT=300` |
| `nexus/common/ollama_client.py` | Default 300 |
| `nexus/chat/static/index.html` | Home/Activity Panels, Welcome |
| `nexus/chat/static/js/app.js` | `refreshHomeHub`, Activity live, Hub-Actions |
| `nexus/chat/static/css/nexus.css` | Hub-Styles |

**Ende.** Leicht bauen, echt verdrahten.
