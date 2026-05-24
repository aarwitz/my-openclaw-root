#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/aaron/.openclaw"
WINDOW_MINUTES="${1:-240}"

echo "openclaw_root: $ROOT"
echo "window_minutes: $WINDOW_MINUTES"
echo "---"

echo "[1] High-signal file modifications"
find "$ROOT" -type f -mmin "-$WINDOW_MINUTES" \
  \( -name '*.json' -o -name '*.py' -o -name '*.md' -o -name '*.sh' \) \
  ! -path "$ROOT/agents/*/sessions/*" \
  ! -path "$ROOT/agents/*/agent/codex-home/.tmp/*" \
  ! -path "$ROOT/agents/*/agent/codex-home/cache/*" \
  ! -path "$ROOT/agents/*/agent/codex-home/skills/*" \
  ! -path "$ROOT/tmp/*" \
  -printf '%TY-%Tm-%Td %TH:%TM:%TS %p\n' | sort
echo "---"

echo "[2] Config drift check: openclaw.json vs openclaw.json.last-good"
if [[ -f "$ROOT/openclaw.json" && -f "$ROOT/openclaw.json.last-good" ]]; then
  if diff -q "$ROOT/openclaw.json.last-good" "$ROOT/openclaw.json" >/dev/null; then
    echo "status: identical"
  else
    echo "status: different"
    diff -u "$ROOT/openclaw.json.last-good" "$ROOT/openclaw.json" | sed -n '1,200p'
  fi
else
  echo "status: skipped (missing openclaw.json or openclaw.json.last-good)"
fi
echo "---"

echo "[3] Critical-file checksums"
sha256sum \
  "$ROOT/openclaw.json" \
  "$ROOT/openclaw.json.last-good" \
  "$ROOT/scripts/show-last-telegram-turn.py" \
  "$ROOT/scripts/dwight-launch-from-issue.py" \
  "$ROOT/scripts/launch-coding-task.sh" \
  "$ROOT/scripts/select-coding-lane.sh" \
  2>/dev/null || true
