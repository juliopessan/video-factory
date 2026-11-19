#!/usr/bin/env bash
# Clipper: uso rápido.
#   ./run_clipper.sh run minha-live.mp4      -> gera os cortes
#   ./run_clipper.sh serve                   -> abre a fila de revisão em http://127.0.0.1:8100
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
fi
./.venv/bin/pip install --quiet -r requirements-clipper.txt

exec ./.venv/bin/python -m clipper "$@"
