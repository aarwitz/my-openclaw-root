#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/aaron/.openclaw/workspace/mcp/task-manager-mcp"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
pip install -r requirements.txt >/dev/null

exec python server.py
