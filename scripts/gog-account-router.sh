#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: gog-account-router.sh [--agent <main|resi|dwight|druck>] [--print-account] <gog args...>

Routes gog account deterministically by agent and enforces non-interactive mode.

Account mapping (override via env vars):
  - main   -> GOG_ACCOUNT_MAIN   (default: aaronclawrsl@gmail.com)
  - resi   -> GOG_ACCOUNT_RESI   (default: aaronclawrsl@gmail.com)
  - dwight -> GOG_ACCOUNT_DWIGHT (default: aaronclawrsl@gmail.com)
  - druck  -> GOG_ACCOUNT_DRUCK  (default: aaronclawrsl@gmail.com)

You can also set OPENCLAW_AGENT_ID, OPENCLAW_AGENT, or AGENT_ID.
EOF
}

agent="${OPENCLAW_AGENT_ID:-${OPENCLAW_AGENT:-${AGENT_ID:-main}}}"
print_account=0

while [[ $# -gt 0 ]]; do
  case "${1:-}" in
    --agent)
      [[ $# -ge 3 ]] || {
        usage
        exit 2
      }
      agent="$2"
      shift 2
      ;;
    --print-account)
      print_account=1
      shift
      ;;
    *)
      break
      ;;
  esac
done

if [[ "$print_account" -eq 0 && $# -eq 0 ]]; then
  usage
  exit 2
fi

if ! command -v gog >/dev/null 2>&1; then
  echo "[gog-account-router] gog CLI not found in PATH" >&2
  exit 1
fi

case "$agent" in
  main) account="${GOG_ACCOUNT_MAIN:-aaronclawrsl@gmail.com}" ;;
  resi) account="${GOG_ACCOUNT_RESI:-aaronclawrsl@gmail.com}" ;;
  dwight) account="${GOG_ACCOUNT_DWIGHT:-aaronclawrsl@gmail.com}" ;;
  druck) account="${GOG_ACCOUNT_DRUCK:-aaronclawrsl@gmail.com}" ;;
  *)
    echo "[gog-account-router] unknown agent '$agent'" >&2
    exit 2
    ;;
esac

if [[ "$print_account" -eq 1 ]]; then
  printf '%s\n' "$account"
  exit 0
fi

# Global auth health check first.
if ! gog auth list --check --no-input >/dev/null 2>&1; then
  echo "[gog-account-router] gog auth unhealthy; run: gog auth manage --services drive,gmail,calendar,sheets,docs" >&2
  exit 1
fi

service="${1:-}"
subcommand="${2:-}"

# Cheap service probes to fail fast before mutating calls.
case "$service:$subcommand" in
  drive:*)
    gog drive search 'owner:me' --max 1 --account "$account" --no-input >/dev/null 2>&1 || {
      echo "[gog-account-router] drive probe failed for account '$account'" >&2
      exit 1
    }
    ;;
  gmail:*)
    gog gmail search 'newer_than:7d' --max 1 --account "$account" --no-input >/dev/null 2>&1 || {
      echo "[gog-account-router] gmail probe failed for account '$account'" >&2
      exit 1
    }
    ;;
  calendar:*)
    gog calendar calendars --max 1 --account "$account" --no-input >/dev/null 2>&1 || {
      echo "[gog-account-router] calendar probe failed for account '$account'" >&2
      exit 1
    }
    ;;
  sheets:*)
    # Auth check is usually enough; leave sheet-specific checks to caller context.
    ;;
  docs:*)
    ;;
  contacts:*)
    ;;
  *)
    ;;
esac

has_no_input=0
has_account=0
for arg in "$@"; do
  if [[ "$arg" == "--no-input" ]]; then
    has_no_input=1
  fi
  if [[ "$arg" == "--account" ]]; then
    has_account=1
  fi
done

cmd=(gog)
if [[ "$has_account" -eq 0 ]]; then
  cmd+=(--account "$account")
fi
cmd+=("$@")
if [[ "$has_no_input" -eq 0 ]]; then
  cmd+=(--no-input)
fi

exec "${cmd[@]}"
