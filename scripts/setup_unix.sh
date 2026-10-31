#!/usr/bin/env bash
# One-time setup for ChopLab AI on Linux (and other Unix-likes): backend (uv + Python 3.12), frontend (npm), build.
# Usage: scripts/setup_unix.sh [--separation]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }

step "Checking uv"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found - installing it (https://docs.astral.sh/uv/)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

step "Checking Node.js (20+)"
command -v node >/dev/null 2>&1 || { echo "Node.js 20+ is required: https://nodejs.org" >&2; exit 1; }
major="$(node --version | sed 's/^v//' | cut -d. -f1)"
[ "$major" -ge 20 ] || { echo "Node.js 20+ required (found $(node --version))" >&2; exit 1; }
node --version

step "Installing backend (uv sync)"
cd "$ROOT/backend"
if [ "${1:-}" = "--separation" ]; then uv sync --extra separation; else uv sync; fi

step "Installing and building frontend"
cd "$ROOT/frontend"
npm ci --no-audit --no-fund
npm run build

step "Environment check"
cd "$ROOT/backend"
uv run --no-sync python "$ROOT/scripts/check_environment.py" || true

printf '\n\033[32mSetup complete. Start ChopLab with: scripts/start_unix.sh\033[0m\n'
