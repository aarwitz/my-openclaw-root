#!/usr/bin/env bash
set -euo pipefail

DEFAULT_TOKEN_FILE="/home/aaron/.openclaw/credentials/cloudflare/account-token"
WORKER_DEPLOY_TOKEN_FILE="/home/aaron/.openclaw/credentials/cloudflare/account-token.bak"
ACCOUNT_META_FILE="/home/aaron/.openclaw/credentials/cloudflare/account-meta.json"
DEFAULT_ACCOUNT_ID="6729a939101c819b5a656b06c3bb0d0b"

usage() {
  cat <<'EOF'
Usage:
  cloudflare-account-router.sh [--mode auto|default|worker-mutate] [--print-token-path]
  cloudflare-account-router.sh [--mode auto|default|worker-mutate] [--verify]
  cloudflare-account-router.sh [--mode auto|default|worker-mutate] [--] <command ...>

Routes Cloudflare token selection deterministically:
  - default       -> ~/.openclaw/credentials/cloudflare/account-token
  - worker-mutate -> ~/.openclaw/credentials/cloudflare/account-token.bak
  - auto          -> worker-mutate for known Wrangler worker mutation commands,
                     otherwise default

Notes:
  - Exports CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID for the child command.
  - Never prints the token itself.
EOF
}

mode="auto"
print_token_path=0
verify_only=0

while [[ $# -gt 0 ]]; do
  case "${1:-}" in
    --mode)
      [[ $# -ge 2 ]] || {
        usage
        exit 2
      }
      mode="$2"
      shift 2
      ;;
    --print-token-path)
      print_token_path=1
      shift
      ;;
    --verify)
      verify_only=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

if [[ "$print_token_path" -eq 0 && "$verify_only" -eq 0 && $# -eq 0 ]]; then
  usage
  exit 2
fi

if [[ ! -f "$ACCOUNT_META_FILE" ]]; then
  echo "[cloudflare-account-router] missing account metadata file: $ACCOUNT_META_FILE" >&2
  exit 1
fi

account_id="${CLOUDFLARE_ACCOUNT_ID:-$(jq -r '.account_id // empty' "$ACCOUNT_META_FILE")}"
if [[ -z "$account_id" || "$account_id" == "null" ]]; then
  account_id="$DEFAULT_ACCOUNT_ID"
fi

select_token_profile() {
  local requested_mode="$1"
  shift || true

  case "$requested_mode" in
    default)
      printf 'default\n'
      return 0
      ;;
    worker-mutate)
      printf 'worker-mutate\n'
      return 0
      ;;
    auto)
      ;;
    *)
      echo "[cloudflare-account-router] unknown mode '$requested_mode'" >&2
      exit 2
      ;;
  esac

  if [[ $# -ge 2 && "$1" == "wrangler" ]]; then
    case "$2" in
      deploy)
        printf 'worker-mutate\n'
        return 0
        ;;
      versions)
        if [[ $# -ge 3 && "$3" == "deploy" ]]; then
          printf 'worker-mutate\n'
          return 0
        fi
        ;;
      secret)
        if [[ $# -ge 3 ]]; then
          case "$3" in
            put|bulk|delete)
              printf 'worker-mutate\n'
              return 0
              ;;
          esac
        fi
        ;;
    esac
  fi

  printf 'default\n'
}

profile="$(select_token_profile "$mode" "$@")"
token_file="$DEFAULT_TOKEN_FILE"
if [[ "$profile" == "worker-mutate" ]]; then
  token_file="$WORKER_DEPLOY_TOKEN_FILE"
fi

if [[ ! -f "$token_file" ]]; then
  echo "[cloudflare-account-router] missing token file for profile '$profile': $token_file" >&2
  exit 1
fi

if [[ "$print_token_path" -eq 1 ]]; then
  printf '%s\n' "$token_file"
  exit 0
fi

token="$(<"$token_file")"
if [[ -z "$token" ]]; then
  echo "[cloudflare-account-router] empty token file for profile '$profile': $token_file" >&2
  exit 1
fi

verify_token() {
  if [[ "$profile" == "worker-mutate" ]]; then
    return 0
  fi

  local verify_output
  verify_output="$(curl -fsS -X GET "https://api.cloudflare.com/client/v4/user/tokens/verify" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json")" || {
      echo "[cloudflare-account-router] token verify request failed for profile '$profile'" >&2
      exit 1
    }

  if [[ "$(jq -r '.success // false' <<<"$verify_output")" != "true" ]]; then
    echo "[cloudflare-account-router] token verify returned unsuccessful response for profile '$profile'" >&2
    exit 1
  fi
}

verify_token

if [[ "$verify_only" -eq 1 ]]; then
  printf 'profile=%s account_id=%s token_path=%s\n' "$profile" "$account_id" "$token_file"
  exit 0
fi

export CLOUDFLARE_API_TOKEN="$token"
export CLOUDFLARE_ACCOUNT_ID="$account_id"

exec "$@"
