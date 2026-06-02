#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# Regression checks for lane selection and launcher contract construction.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROUTER="$SCRIPT_DIR/select-coding-lane.sh"
LAUNCHER="$SCRIPT_DIR/launch-coding-task.sh"
ASSIGNER="$SCRIPT_DIR/dwight-assign-coding-task.sh"

pass_count=0
fail_count=0

pass() {
  echo "PASS: $1"
  pass_count=$((pass_count + 1))
}

fail() {
  echo "FAIL: $1"
  fail_count=$((fail_count + 1))
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local label="$3"
  if echo "$haystack" | grep -Fq "$needle"; then
    pass "$label"
  else
    fail "$label (missing '$needle')"
    echo "--- output ---"
    echo "$haystack"
    echo "--------------"
  fi
}

echo "== Coding Lane Regression =="

a="$(bash "$ROUTER" --scope low --expected-files 1 --risk low --acp-available false --tag-heavy false)"
assert_contains "$a" '"lane":"inline"' "Router: low/acp-unavailable -> inline"

b="$(bash "$ROUTER" --scope medium --expected-files 2 --risk medium --acp-available false --tag-heavy false)"
assert_contains "$b" '"lane":"codex-subagent"' "Router: medium/acp-unavailable -> codex-subagent"

c="$(bash "$ROUTER" --scope high --expected-files 12 --risk high --acp-available true --tag-heavy true)"
assert_contains "$c" '"lane":"codex-subagent"' "Router: heavy tag -> codex-subagent (ACP disabled)"
assert_contains "$c" '"fallbackLane":"inline"' "Router: codex-subagent fallback -> inline"

d="$(bash "$LAUNCHER" --task-id TST-100 --owner-agent main --repo "$HOME/.openclaw" --goal "low scope" --scope low --expected-files 1 --risk low --acp-available false --tag-heavy false)"
assert_contains "$d" 'selected=inline' "Launcher: inline dry-run decision"


e="$(bash "$LAUNCHER" --task-id TST-101 --owner-agent main --repo "$HOME/.openclaw" --goal "medium scope" --scope medium --expected-files 2 --risk medium --acp-available false --tag-heavy false)"
assert_contains "$e" 'selected=codex-subagent' "Launcher: codex-subagent dry-run decision"
assert_contains "$e" 'spawnAgentUsed' "Launcher: codex-subagent contract hint present"

f="$(bash "$ASSIGNER" --owner-agent resi --task-id TST-102 --repo "$HOME/.openclaw" --goal "heavy" --scope high --expected-files 12 --risk high --tag-heavy true --acp-available true 2>&1 || true)"
if echo "$f" | grep -Eq 'selected=codex-subagent'; then
  pass "Assigner: heavy route selected as codex-subagent (ACP disabled)"
else
  fail "Assigner: heavy route selection missing"
fi
if echo "$f" | grep -Eq 'ACP is disabled by policy|selected=codex-subagent'; then
  pass "Assigner: ACP compatibility flags are ignored by policy"
else
  fail "Assigner: no-ACP policy signal missing"
fi

echo
echo "Summary: pass=$pass_count fail=$fail_count"

if (( fail_count > 0 )); then
  exit 1
fi

exit 0
