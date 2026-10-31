#!/usr/bin/env bash
# Start ChopLab AI. Usage: scripts/start_unix.sh [--dev]
#   --dev  run the Vite dev server (http://localhost:5173) with hot reload alongside the backend
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"
export CHOPLAB_PORT="${CHOPLAB_PORT:-8765}"
[ -d "$ROOT/backend/.venv" ] || { echo "Not set up yet - run scripts/setup_unix.sh first." >&2; exit 1; }

VITE_PID=""
cleanup() { if [ -n "$VITE_PID" ]; then kill "$VITE_PID" 2>/dev/null || true; fi; }
trap cleanup EXIT

if [ "${1:-}" = "--dev" ]; then
  (cd "$ROOT/frontend" && npm run dev) &
  VITE_PID=$!
  URL="http://localhost:5173"
else
  [ -f "$ROOT/frontend/dist/index.html" ] || (cd "$ROOT/frontend" && npm run build)
  URL="http://127.0.0.1:$CHOPLAB_PORT"
fi

# open a browser once the API is up (best effort)
(
  for _ in $(seq 1 120); do
    if curl -fs "http://127.0.0.1:$CHOPLAB_PORT/api/health" >/dev/null 2>&1; then
      (command -v xdg-open >/dev/null && xdg-open "$URL") || (command -v open >/dev/null && open "$URL") || true
      exit 0
    fi
    sleep 0.5
  done
) >/dev/null 2>&1 &

echo "ChopLab AI -> $URL   (Ctrl+C to stop)"
cd "$ROOT/backend"
uv run choplab
