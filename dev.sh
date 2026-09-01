#!/usr/bin/env bash
# Run with invented routes — no API key, no quota. See tools/dev_server.py.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then set -a; . ./.env; set +a; fi
# The offline engine ignores it, but an unset key would trip the 503 guard.
export ORS_API_KEY="${ORS_API_KEY:-offline}"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

exec ./.venv/bin/python -m tools.dev_server
