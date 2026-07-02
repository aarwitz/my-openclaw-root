#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/repo-boundary-policy.sh"

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

assert_ok "Allowed owner accepted" \
  enforce_repo_owner_policy "dwight" "/home/aaron/repos/lidi-task-manager"

assert_fail "Unknown owner blocked" \
  enforce_repo_owner_policy "unknown-owner" "/home/aaron/repos/lidi-task-manager"

resolved_repo="$(realpath -m "/home/aaron/repos/lidi-task-manager")"
if [[ "$resolved_repo" != "/home/aaron/repos/lidi-task-manager" ]]; then
  echo "FAIL: repo path resolution mismatch: $resolved_repo" >&2
  exit 1
fi

echo "PASS: host repo path resolution"

echo "All repo boundary hardening checks passed."
