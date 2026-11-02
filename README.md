# NEXUS Master Home 4D

**Dein lokales KI-Zuhause.** NEXUS Master Home ist eine selbst gehostete Oberfläche, in der mehrere KI-Agenten zusammenarbeiten: komplett auf deinem eigenen Rechner, mit lokalem Sprachmodell, ohne Cloud-Zwang und ohne laufende Kosten.

## Features

- **4D-Dämmerungs-Design**: dunkles Glas-Layout mit warmen Akzenten, gut lesbar, mit ruhigen Animationen.
- **Agenten-Team**
  - **NEXUS**: Zentrale, KI-Hub und Steuerung.
  - **Hermes**: Planung, Ausführung und Werkzeuge.
  - **Aether**: separate Prüfung und Qualitätskontrolle.
  - **NEXUS GPT**: Chat, Analyse, Wissen und Beratung.
- **Live-Hub**: Live-Ereignisstrom aller Agenten, filterbar nach Agent.
- **Terminal**: eingebettetes, einklappbares Live-Terminal über dem Eingabefeld, zeigt die echten Befehlsausgaben der Agenten.
- **Dock und Apps**: Schnellstart für Dateien, Mail, Studio, Medien und mehr.
- **Autobuild**: Backlog-gesteuerte Selbstverbesserung mit Tests, Guards und Rollback.
- **Sicherheitsgrenzen**: Guards, Freigabelisten und Audit, damit nichts unkontrolliert ausgeführt wird.

## Voraussetzungen

- Windows 10/11 (PowerShell) oder Linux
- Python 3.11 oder neuer
- [Ollama](https://ollama.com) lokal installiert
- Modell: `ollama pull qwen3.5:9b`
- Empfohlen: 16 GB RAM, eine GPU mit 8 GB oder mehr

## Installation und Start

```powershell
git clone https://github.com/Eddylike/nexus-master-home.git
cd nexus-master-home
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # Werte anpassen
ollama serve             # falls Ollama noch nicht läuft
.\start_nexus_all.ps1    # oder: python run_server.py
```

Linux: `./start_nexus.sh`, Status mit `./status_nexus.sh`, Stopp mit `./stop_nexus.sh`.

Danach im Browser öffnen: **http://127.0.0.1:8299/**

## Ports

| Dienst | Port |
|---|---|
| NEXUS Master Home (Web und API) | 8299 |
| Ollama | 11434 |

## Tests

```powershell
python -m pytest -q
```

## Sicherheitshinweise

- Standardmäßig lauscht der Server nur auf `127.0.0.1`. Gib ihn nicht ungeschützt ins Internet frei.
- Geheimnisse wie Tokens, App-Passwörter und Geräteadressen gehören ausschließlich in `.env`. Diese Datei wird nicht committet.
- IP-Adressen im Code sind Platzhalter aus dem Dokumentationsbereich (`192.0.2.x`). Trage deine eigenen Geräte über `.env` bzw. die Konfiguration ein.
- Chatverläufe, Gedächtnisdaten, Logs und Uploads (`workspace/`) sind nicht Teil des Repos und bleiben lokal.
- Große 3D-Avatar- (`.vrm`) und Videodateien sind nicht enthalten. Die Oberfläche funktioniert ohne sie, mit Fallback.

## Lizenz

Privat © Eddylike. Alle Rechte vorbehalten.