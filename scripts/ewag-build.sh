#!/usr/bin/env bash
set -euo pipefail

# ewag-build.sh — Build/clean/branch operations on Mac node (no LLM)
#
# Telegram usage:
#   /bash ~/.openclaw/scripts/ewag-build.sh build
#   /bash ~/.openclaw/scripts/ewag-build.sh clean
#   /bash ~/.openclaw/scripts/ewag-build.sh branch
#   /bash ~/.openclaw/scripts/ewag-build.sh status

NODE_NAME="ios-build-node"
# EWAG execution Mac: Taylor's Mac on Tailscale.
MAC_USER="${EWAG_MAC_USER:-taylorolsen-vogt}"
SSH_HOST="${EWAG_MAC_SSH_HOST:-${MAC_USER}@100.125.133.123}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=15"
IOS_AGENT_BIN="${EWAG_IOS_AGENT_BIN:-/Users/${MAC_USER}/ios-agent/ios-agent}"
EWAG_REPO_DIR="${EWAG_IOS_REPO_DIR:-/Users/${MAC_USER}/iosApp}"
TIMEOUT=300

# Run a command on the Mac node via SSH.
# Usage: run_on_node <command> [args...]
# Hang detection via SSH keepalives (ServerAliveInterval=15, CountMax=20 = ~5min).
run_on_node() {
  ssh $SSH_OPTS -o ServerAliveInterval=15 -o ServerAliveCountMax=20 "$SSH_HOST" "$*" 2>&1
}

case "${1:-}" in
  ""|help|-h|--help)
    echo "ewag-build.sh — iOS build operations from Telegram"
    echo ""
    echo "Commands:"
    echo "  build              Build the app"
    echo "  build <branch>     Switch branch and build"
    echo "  clean              Clean DerivedData and uninstall"
    echo "  branch             Show current branch info"
    echo "  status             Quick node health check"
    exit 0
    ;;

  build)
    BRANCH="${2:-}"
    COMMIT="${EWAG_GIT_COMMIT:-}"
    if [[ -n "$BRANCH" && -n "$COMMIT" ]]; then
      echo "Building branch: $BRANCH @ $COMMIT"
      run_on_node "$IOS_AGENT_BIN" build --branch "$BRANCH" --commit "$COMMIT" \
        | grep -E "REPO_SYNC|BUILD|error:|warning:|SUCCEEDED|FAILED" | tail -30
    elif [[ -n "$BRANCH" ]]; then
      echo "Building branch: $BRANCH"
      run_on_node "$IOS_AGENT_BIN" build --branch "$BRANCH" \
        | grep -E "REPO_SYNC|BUILD|error:|warning:|SUCCEEDED|FAILED" | tail -30
    elif [[ -n "$COMMIT" ]]; then
      echo "Building commit: $COMMIT"
      run_on_node "$IOS_AGENT_BIN" build --commit "$COMMIT" \
        | grep -E "REPO_SYNC|BUILD|error:|warning:|SUCCEEDED|FAILED" | tail -30
    else
      echo "Building current branch..."
      run_on_node "$IOS_AGENT_BIN" build \
        | grep -E "REPO_SYNC|BUILD|error:|warning:|SUCCEEDED|FAILED" | tail -30
    fi
    ;;

  clean)
    echo "Cleaning build..."
    run_on_node "$IOS_AGENT_BIN" clean | tail -10
    ;;

  branch)
    echo "Branch info:"
    run_on_node "$IOS_AGENT_BIN" branch | tail -20
    ;;

  status)
    echo "Node status check..."
    ssh $SSH_OPTS "$SSH_HOST" "set -e; echo 'SSH: OK' && \
      xcrun simctl list devices booted 2>/dev/null | head -5 && \
      echo '' && \
      cd '$EWAG_REPO_DIR' && \
      echo \"Branch: \$(git branch --show-current)\" && \
      echo \"Commit: \$(git log --oneline -1)\" && \
      echo \"DerivedData: \$(du -sh ~/Library/Developer/Xcode/DerivedData 2>/dev/null | cut -f1 || echo 'none')\"" 2>&1
    ;;

  *)
    echo "Unknown command: $1"
    echo "Run: ewag-build.sh help"
    exit 1
    ;;
esac
