#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

# --reload restarts on every file save, which clears the in-memory route and
# search caches — so the same search costs its full quota again. Opt in with
# DEV_RELOAD=1 when you are actually editing.
RELOAD=""
[ -n "${DEV_RELOAD:-}" ] && RELOAD="--reload"

exec ./.venv/bin/uvicorn backend.main:app $RELOAD --port "${PORT:-8000}"
