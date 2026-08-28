#!/usr/bin/env bash
# Sobe o Video Factory localmente em http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

exec ./.venv/bin/python -m uvicorn app.main:app --host "${VF_HOST:-127.0.0.1}" --port "${VF_PORT:-8000}" --reload
