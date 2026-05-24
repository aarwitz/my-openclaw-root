#!/usr/bin/env bash
set -euo pipefail

# ewag-sim.sh — Simulator interaction from Telegram (no LLM)
#
# Telegram usage:
#   /bash ~/.openclaw/scripts/ewag-sim.sh screenshot
#   /bash ~/.openclaw/scripts/ewag-sim.sh tap 660 1434
#   /bash ~/.openclaw/scripts/ewag-sim.sh swipe up
#   /bash ~/.openclaw/scripts/ewag-sim.sh input "hello world"
#   /bash ~/.openclaw/scripts/ewag-sim.sh home
#   /bash ~/.openclaw/scripts/ewag-sim.sh deviceinfo

NODE_NAME="ios-build-node"
# EWAG execution Mac: Taylor's Mac on Tailscale.
MAC_USER="${EWAG_MAC_USER:-taylorolsen-vogt}"
SSH_HOST="${EWAG_MAC_SSH_HOST:-${MAC_USER}@100.125.133.123}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=15"
IOS_AGENT_BIN="${EWAG_IOS_AGENT_BIN:-/Users/${MAC_USER}/ios-agent/ios-agent}"
TIMEOUT=60
MEDIA_DIR="/home/aaron/.openclaw/media/inbound"

# Run a command on the Mac node via SSH.
run_on_node() {
  ssh $SSH_OPTS -o ServerAliveInterval=15 -o ServerAliveCountMax=20 "$SSH_HOST" "$*" 2>&1
}

case "${1:-}" in
  ""|help|-h|--help)
    echo "ewag-sim.sh — Simulator interaction from Telegram"
    echo ""
    echo "Commands:"
    echo "  screenshot          Capture current simulator screen"
    echo "  tap <x> <y>        Tap at coordinates (1320x2868 grid)"
    echo "  swipe <dir>        Swipe up/down/left/right"
    echo "  input <text>       Type text into focused field"
    echo "  home               Press home button"
    echo "  deviceinfo         Show device resolution info"
    echo "  openurl <url>      Open URL in simulator"
    exit 0
    ;;

  screenshot)
    echo "Capturing simulator screenshot..."
    TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$MEDIA_DIR"

    # Take screenshot via simctl
    ssh $SSH_OPTS "$SSH_HOST" "xcrun simctl io booted screenshot /tmp/sim-screenshot.png" 2>&1
    scp $SSH_OPTS "$SSH_HOST:/tmp/sim-screenshot.png" "$MEDIA_DIR/sim-${TIMESTAMP}.png" 2>&1
    echo "Saved: $MEDIA_DIR/sim-${TIMESTAMP}.png ($(du -h "$MEDIA_DIR/sim-${TIMESTAMP}.png" | cut -f1))"
    ;;

  tap)
    X="${2:?'x coordinate required'}"
    Y="${3:?'y coordinate required'}"
    echo "Tapping ($X, $Y)..."
    run_on_node "$IOS_AGENT_BIN" tap "$X" "$Y" | tail -5
    ;;

  swipe)
    DIR="${2:?'direction required (up/down/left/right)'}"
    echo "Swiping $DIR..."
    run_on_node "$IOS_AGENT_BIN" swipe "$DIR" | tail -5
    ;;

  input)
    shift
    TEXT="$*"
    echo "Typing: $TEXT"
    run_on_node "$IOS_AGENT_BIN" input "$TEXT" | tail -5
    ;;

  home)
    echo "Pressing home..."
    run_on_node "$IOS_AGENT_BIN" home | tail -5
    ;;

  deviceinfo)
    echo "Device info:"
    run_on_node "$IOS_AGENT_BIN" deviceinfo | tail -15
    ;;

  openurl)
    URL="${2:?'URL required'}"
    echo "Opening: $URL"
    ssh $SSH_OPTS "$SSH_HOST" "xcrun simctl openurl booted '$URL'" 2>&1
    ;;

  *)
    echo "Unknown command: $1"
    echo "Run: ewag-sim.sh help"
    exit 1
    ;;
esac
