#!/usr/bin/env bash
# One command to run KataPilot: sets up the Python env, builds the dashboard if needed, starts the backend.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d backend/.venv ]; then
  echo "› Creating Python environment…"
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi
if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  echo "› Created backend/.env — add your ANTHROPIC_API_KEY (and optionally a search key), then re-run."
fi
if [ ! -f dashboard/dist/index.html ] || [ "${1:-}" = "--rebuild" ]; then
  echo "› Building dashboard…"
  (cd dashboard && npm install --silent && npm run build --silent)
fi
PORT="$(grep -E '^PORT=' backend/.env | cut -d= -f2 || true)"
echo "› KataPilot dashboard: http://127.0.0.1:${PORT:-8765}"
cd backend && exec .venv/bin/python -m app.main
