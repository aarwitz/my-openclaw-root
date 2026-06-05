#!/usr/bin/env bash
# Shared repo/owner boundary policy for coding launch scripts.
# Fail closed on policy violations.

set -euo pipefail

readonly DEFAULT_EWAG_ALLOWED_REPOS="/home/aaron/repos/lidi-task-manager,/home/aaron/repos/EWAG-dev-iosApp"
readonly DEFAULT_EWAG_OWNER_AGENTS=""
readonly DEFAULT_RSL_OWNER_AGENTS="main,jerry,dwight,druck,resi,researcher,quant,critic,trader,executor,archivist,developer,overseer"
readonly DEFAULT_EWAG_CONTAINER_REPO_MAP="/home/aaron/repos/lidi-task-manager=/work/lidi-task-manager,/home/aaron/repos/EWAG-dev-iosApp=/work/ewagios-dev"

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

repo_is_ewag_allowed() {
  local repo="$1"
  local allowed_csv="${OPENCLAW_EWAG_ALLOWED_REPOS:-$DEFAULT_EWAG_ALLOWED_REPOS}"
  while IFS= read -r root; do
    if path_is_within "$repo" "$root"; then
      return 0
    fi
  done < <(split_csv_to_lines "$allowed_csv")
  return 1
}

is_ewag_owner_agent() {
  local owner_agent="$1"
  local owner="${owner_agent,,}"
  local ewag_owners="${OPENCLAW_EWAG_OWNER_AGENTS:-$DEFAULT_EWAG_OWNER_AGENTS}"
  owner_in_list "$owner" "$ewag_owners"
}

resolve_ewag_container_repo_path() {
  local repo_path="$1"
  local mapping_csv="${OPENCLAW_EWAG_CONTAINER_REPO_MAP:-$DEFAULT_EWAG_CONTAINER_REPO_MAP}"
  local repo_real
  repo_real="$(realpath -m "$repo_path")"

  while IFS= read -r entry; do
    local host_root container_root host_real suffix
    host_root="${entry%%=*}"
    container_root="${entry#*=}"

    if [[ -z "$host_root" || -z "$container_root" || "$host_root" == "$container_root" ]]; then
      continue
    fi

    host_real="$(realpath -m "$host_root")"
    if [[ "$repo_real" == "$host_real" ]]; then
      printf '%s' "$container_root"
      return 0
    fi
    if [[ "$repo_real" == "$host_real"/* ]]; then
      suffix="${repo_real#"$host_real"/}"
      printf '%s/%s' "$container_root" "$suffix"
      return 0
    fi
  done < <(split_csv_to_lines "$mapping_csv")

  return 1
}

enforce_repo_owner_policy() {
  local owner_agent="$1"
  local repo_path="$2"

  local owner="${owner_agent,,}"
  local ewag_owners="${OPENCLAW_EWAG_OWNER_AGENTS:-$DEFAULT_EWAG_OWNER_AGENTS}"
  local rsl_owners="${OPENCLAW_RSL_OWNER_AGENTS:-$DEFAULT_RSL_OWNER_AGENTS}"
  local allowed_repos="${OPENCLAW_EWAG_ALLOWED_REPOS:-$DEFAULT_EWAG_ALLOWED_REPOS}"

  if owner_in_list "$owner" "$ewag_owners"; then
    if ! repo_is_ewag_allowed "$repo_path"; then
      echo "Repo boundary violation: owner-agent '$owner_agent' is restricted to EWAG repos only." >&2
      echo "Allowed EWAG repo roots: $allowed_repos" >&2
      echo "Requested repo: $(realpath -m "$repo_path")" >&2
      return 1
    fi
  fi

  if owner_in_list "$owner" "$rsl_owners"; then
    if repo_is_ewag_allowed "$repo_path"; then
      echo "Repo boundary violation: owner-agent '$owner_agent' is an RSL bot and cannot target EWAG repos." >&2
      echo "EWAG repo roots: $allowed_repos" >&2
      echo "Requested repo: $(realpath -m "$repo_path")" >&2
      return 1
    fi
  fi

  return 0
}
