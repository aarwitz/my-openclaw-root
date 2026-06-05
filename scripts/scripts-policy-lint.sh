#!/usr/bin/env bash
# scripts-policy-lint.sh
# Enforce that every script in a governed dir uses the require-wrapper guard.
# Reads ~/.openclaw/scripts/policy.json as the source of truth.
#
# Exit codes:
#   0 = clean
#   1 = violations found (missing wrapper, unregistered governed dir contents)
#   2 = usage / dependency error
#
# Safe to run without the wrapper (lint must be runnable by humans, CI, and pre-commit).

set -euo pipefail

POLICY_FILE="${SCRIPTS_POLICY_FILE:-$HOME/.openclaw/scripts/policy.json}"

if ! command -v jq >/dev/null 2>&1; then
  echo "scripts-policy-lint: jq is required" >&2
  exit 2
fi
if [[ ! -f "$POLICY_FILE" ]]; then
  echo "scripts-policy-lint: policy file not found: $POLICY_FILE" >&2
  exit 2
fi

expand_path() {
  local p="$1"
  printf '%s\n' "${p/#\~/$HOME}"
}

has_wrapper_guard() {
  # True if the file imports the require-wrapper guard
  grep -q -E 'require-wrapper\.sh|require_wrapper' "$1"
}

is_exempt_script() {
  local target="$1"
  local exempt
  while IFS= read -r exempt; do
    [[ -z "$exempt" ]] && continue
    local resolved
    resolved="$(expand_path "$exempt")"
    if [[ "$target" == "$resolved" ]]; then
      return 0
    fi
  done < <(jq -r '.exemptScripts[]?.path // empty' "$POLICY_FILE")
  return 1
}

matches_ignore() {
  # $1=relative path within dir, $2..=ignore patterns
  local rel="$1"; shift
  local pat
  for pat in "$@"; do
    [[ -z "$pat" ]] && continue
    if [[ "$pat" == */ ]]; then
      # directory prefix
      if [[ "$rel" == "$pat"* ]] || [[ "$rel" == "${pat%/}" ]]; then
        return 0
      fi
    else
      if [[ "$rel" == "$pat" ]]; then
        return 0
      fi
    fi
  done
  return 1
}

violations=0
checked=0

dir_count="$(jq '.governedDirs | length' "$POLICY_FILE")"
for ((i=0; i<dir_count; i++)); do
  raw_path="$(jq -r ".governedDirs[$i].path" "$POLICY_FILE")"
  wrapper_required="$(jq -r ".governedDirs[$i].wrapperRequired // true" "$POLICY_FILE")"
  dir="$(expand_path "$raw_path")"

  if [[ ! -d "$dir" ]]; then
    echo "WARN: governed dir does not exist: $dir" >&2
    continue
  fi

  mapfile -t ignore_patterns < <(jq -r ".governedDirs[$i].ignore[]?" "$POLICY_FILE")

  while IFS= read -r -d '' file; do
    rel="${file#$dir/}"
    if matches_ignore "$rel" "${ignore_patterns[@]:-}"; then
      continue
    fi
    if is_exempt_script "$file"; then
      continue
    fi
    checked=$((checked + 1))
    if [[ "$wrapper_required" == "true" ]] && ! has_wrapper_guard "$file"; then
      echo "VIOLATION: missing wrapper guard: $file"
      violations=$((violations + 1))
    fi
  done < <(find "$dir" -type f \( -name '*.sh' -o -name '*.py' \) -print0)
done

echo "---"
echo "scripts-policy-lint: checked=$checked violations=$violations"
if (( violations > 0 )); then
  cat >&2 <<EOF

To fix:
  1. Scaffold new scripts with: ~/.openclaw/scripts/new-script.sh <name>.{sh,py}
  2. For existing scripts, prepend one of:
       sh:  source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
       py:  import sys; sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib"); from require_wrapper import require_wrapper; require_wrapper()
  3. If a script is legitimately exempt, add it to exemptScripts[] in $POLICY_FILE with a reason.
EOF
  exit 1
fi
exit 0
