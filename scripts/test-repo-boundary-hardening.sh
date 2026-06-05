#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/repo-boundary-policy.sh"

export OPENCLAW_EWAG_OWNER_AGENTS="ewag-pm"

assert_ok() {
  local name="$1"
  shift
  if "$@"; then
    echo "PASS: $name"
  else
    echo "FAIL: $name" >&2
    exit 1
  fi
}

assert_fail() {
  local name="$1"
  shift
  if "$@"; then
    echo "FAIL: $name (unexpected success)" >&2
    exit 1
  else
    echo "PASS: $name"
  fi
}

assert_ok "EWAG owner detection" is_ewag_owner_agent "ewag-pm"
assert_fail "RSL owner is not EWAG" is_ewag_owner_agent "dwight"

assert_ok "EWAG allow list accepts lidi" \
  enforce_repo_owner_policy "ewag-pm" "/home/aaron/repos/lidi-task-manager"

assert_fail "EWAG owner blocked from RSL repo" \
  enforce_repo_owner_policy "ewag-pm" "/home/aaron/repos/Task-Manager"

assert_fail "RSL owner blocked from EWAG repo" \
  enforce_repo_owner_policy "dwight" "/home/aaron/repos/lidi-task-manager"

mapped_repo="$(resolve_ewag_container_repo_path "/home/aaron/repos/lidi-task-manager")"
if [[ "$mapped_repo" != "/work/lidi-task-manager" ]]; then
  echo "FAIL: mapping for lidi-task-manager expected /work/lidi-task-manager, got $mapped_repo" >&2
  exit 1
fi

echo "PASS: mapping for lidi-task-manager"

mapped_subpath="$(resolve_ewag_container_repo_path "/home/aaron/repos/EWAG-dev-iosApp/src")"
if [[ "$mapped_subpath" != "/work/ewagios-dev/src" ]]; then
  echo "FAIL: mapping for EWAG-dev-iosApp/src expected /work/ewagios-dev/src, got $mapped_subpath" >&2
  exit 1
fi

echo "PASS: mapping for EWAG-dev-iosApp/src"

echo "All repo boundary hardening checks passed."
