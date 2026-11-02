#!/usr/bin/env bash
# NEXUS portable stop (Linux / Termux / macOS)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${NEXUS_PORT:-8299}"
PIDFILE="$ROOT/workspace/logs/nexus.pid"
if [[ -f "$PIDFILE" ]]; then
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "${pid:-}" ]]; then
    kill "$pid" 2>/dev/null || true
    sleep 0.5
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
fi
# Best-effort: free port if fuser/lsof available
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" 2>/dev/null || true
elif command -v lsof >/dev/null 2>&1; then
  lsof -ti tcp:"$PORT" | xargs -r kill -9 2>/dev/null || true
fi
rm -f "$ROOT/workspace/logs/nexus_start.lock" 2>/dev/null || true
echo "NEXUS gestoppt (Port $PORT)."