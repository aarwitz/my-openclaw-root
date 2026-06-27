#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

REMOTE_HOST="taylorolsen-vogt@100.125.133.123"
REMOTE_ROOT="/Users/taylorolsen-vogt/repos/MONTRA"
LOCAL_MIRROR="/home/aaron/.openclaw/workspaces/montraassistant/mirror"
SSH_KEY_DEFAULT="/home/aaron/.openclaw/credentials/montra_forced_ed25519"
SSH_KEY="${MONTRA_SSH_KEY:-$SSH_KEY_DEFAULT}"

usage() {
  cat <<'EOF'
Usage:
  montra-mac-safe.sh run -- <command tokens...>
  montra-mac-safe.sh sync-pull
  montra-mac-safe.sh sync-push

Rules:
  - Always executes in /Users/taylorolsen-vogt/repos/MONTRA on the Mac node.
  - For run mode, only whitelisted command prefixes are accepted.
  - For run mode, shell metacharacters are blocked.
EOF
}

log() {
  printf '[montra-mac-safe] %s\n' "$*"
}

die() {
  printf '[montra-mac-safe] ERROR: %s\n' "$*" >&2
  exit 1
}

ssh_base=(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
if [[ -n "$SSH_KEY" && -f "$SSH_KEY" ]]; then
  ssh_base+=(-i "$SSH_KEY")
fi
ssh_base+=("$REMOTE_HOST")

rsync_ssh="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
if [[ -n "$SSH_KEY" && -f "$SSH_KEY" ]]; then
  rsync_ssh="$rsync_ssh -i $SSH_KEY"
fi

ensure_ssh() {
  "${ssh_base[@]}" "pwd" >/dev/null
}

sanitize_and_validate() {
  local cmd="$1"
  local first
  local first_base
  first="${cmd%% *}"
  first_base="$(basename "$first")"

  if [[ -z "$first" ]]; then
    die "empty command"
  fi

  if [[ "$cmd" =~ [\;\&\|\<\>\`\$\(\)\{\}] ]]; then
    die "blocked metacharacters in command"
  fi

  if [[ "$cmd" =~ (^|[[:space:]])(cd|sudo|ssh|scp|sftp|rsync)[[:space:]] ]]; then
    die "blocked control command"
  fi

  case "$first_base" in
    git|xcodebuild|xcrun|swift|swiftlint|npm|npx|yarn|pnpm|node|bundle|pod|fastlane|ruby|gem|python3|plutil|defaults|PlistBuddy|ls|pwd|find|rg|grep|sed|awk|cat|head|tail|bash|sh)
      ;;
    *)
      die "command prefix '$first_base' is not in allowlist"
      ;;
  esac
}

run_remote() {
  local cmd="$1"
  sanitize_and_validate "$cmd"
  log "remote run in MONTRA repo: $cmd"
  "${ssh_base[@]}" "$cmd"
}

sync_pull() {
  mkdir -p "$LOCAL_MIRROR"
  log "sync pull: $REMOTE_ROOT -> $LOCAL_MIRROR"
  rsync -az --delete -e "$rsync_ssh" \
    "$REMOTE_HOST:$REMOTE_ROOT/" "$LOCAL_MIRROR/"
}

sync_push() {
  mkdir -p "$LOCAL_MIRROR"
  log "sync push: $LOCAL_MIRROR -> $REMOTE_ROOT"
  rsync -az --delete -e "$rsync_ssh" \
    "$LOCAL_MIRROR/" "$REMOTE_HOST:$REMOTE_ROOT/"
}

main() {
  [[ $# -ge 1 ]] || {
    usage
    exit 2
  }

  ensure_ssh

  case "$1" in
    run)
      shift
      [[ "${1:-}" == "--" ]] || die "run mode requires '--' separator"
      shift
      [[ $# -gt 0 ]] || die "missing command after '--'"
      run_remote "$*"
      ;;
    sync-pull)
      sync_pull
      ;;
    sync-push)
      sync_push
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      die "unknown mode '$1'"
      ;;
  esac
}

main "$@"
