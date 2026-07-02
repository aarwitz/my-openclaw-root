#!/usr/bin/env bash
# Shared repo/owner boundary policy for coding launch scripts.
# Keep this library as a thin compatibility layer for existing launch scripts.

set -euo pipefail

readonly DEFAULT_RSL_OWNER_AGENTS="main,jerry,dwight,druck,resi,researcher,quant,critic,trader,executor,archivist,developer,overseer"

split_csv_to_lines() {
  local csv="$1"
  echo "$csv" | tr ',' '\n' | sed 's/^ *//;s/ *$//' | sed '/^$/d'
}

path_is_within() {
  local path="$1"
  local root="$2"
  local path_real root_real
  path_real="$(realpath -m "$path")"
  root_real="$(realpath -m "$root")"
  [[ "$path_real" == "$root_real" || "$path_real" == "$root_real"/* ]]
}

owner_in_list() {
  local owner="$1"
  local list_csv="$2"
  while IFS= read -r entry; do
    [[ "${owner,,}" == "${entry,,}" ]] && return 0
  done < <(split_csv_to_lines "$list_csv")
  return 1
}

enforce_repo_owner_policy() {
  local owner_agent="$1"
  local repo_path="$2"

  local owner="${owner_agent,,}"
  local rsl_owners="${OPENCLAW_RSL_OWNER_AGENTS:-$DEFAULT_RSL_OWNER_AGENTS}"

  if ! owner_in_list "$owner" "$rsl_owners"; then
    echo "Repo boundary violation: owner-agent '$owner_agent' is not in the allowed owner list." >&2
    echo "Allowed owner agents: $rsl_owners" >&2
    return 1
  fi

  return 0
}
