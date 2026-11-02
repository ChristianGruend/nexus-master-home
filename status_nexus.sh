#!/usr/bin/env bash
# NEXUS portable status — no secrets, no Bridge token printed
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${NEXUS_PORT:-8299}"
GW="${NEXUS_GATEWAY_PORT:-8300}"
probe() {
  local url="$1"
  local code
  code="$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 --max-time 4 "$url" 2>/dev/null || echo 000)"
  echo "$code"
}
o="$(probe "http://127.0.0.1:${PORT}/api/health")"
b="$(probe "http://127.0.0.1:${PORT}/api/bridge/health")"
g="$(probe "http://127.0.0.1:${GW}/api/bridge/health")"
echo "NEXUS_STATUS_PORTABLE"
echo "ROOT=$ROOT"
echo "ORIGIN_${PORT}=$( [[ "$o" == "200" ]] && echo UP || echo "DOWN($o)" )"
echo "BRIDGE_AUTH_PROBE=$( [[ "$b" == "200" || "$b" == "401" ]] && echo "OK($b)" || echo "FAIL($b)" )"
echo "GATEWAY_${GW}=$( [[ "$g" == "200" || "$g" == "401" ]] && echo "UP($g)" || echo "DOWN($g)" )"
echo "PLATFORM=$(uname -s 2>/dev/null || echo unknown)"