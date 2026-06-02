#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# Deterministic policy router for coding lane selection.
# ACP is disabled by policy; only inline and codex-subagent lanes are emitted.
# Output is one JSON object with lane, reason, and fallback lane.

scope="medium"
expected_files="1"
risk="low"
acp_available="false"
heavy_tag="false"

usage() {
  cat <<'EOF'
Usage:
  select-coding-lane.sh [options]

Options:
  --scope <low|medium|high>
  --expected-files <int>
  --risk <low|medium|high>
  --acp-available <true|false>    Accepted for compatibility; ignored.
  --tag-heavy <true|false>
  --help

Examples:
  select-coding-lane.sh --scope high --expected-files 12 --risk high --acp-available false
  select-coding-lane.sh --scope low --expected-files 1 --risk low --acp-available false
EOF
}

is_true() {
  [[ "$1" == "true" ]]
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope)
      scope="${2:-}"
      shift 2
      ;;
    --expected-files)
      expected_files="${2:-}"
      shift 2
      ;;
    --risk)
      risk="${2:-}"
      shift 2
      ;;
    --acp-available)
      acp_available="${2:-}"
      shift 2
      ;;
    --tag-heavy)
      heavy_tag="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "$scope" =~ ^(low|medium|high)$ ]]; then
  echo "Invalid --scope: $scope" >&2
  exit 2
fi

if ! [[ "$risk" =~ ^(low|medium|high)$ ]]; then
  echo "Invalid --risk: $risk" >&2
  exit 2
fi

if ! [[ "$expected_files" =~ ^[0-9]+$ ]]; then
  echo "Invalid --expected-files: $expected_files" >&2
  exit 2
fi

if ! [[ "$acp_available" =~ ^(true|false)$ ]]; then
  echo "Invalid --acp-available: $acp_available" >&2
  exit 2
fi

if ! [[ "$heavy_tag" =~ ^(true|false)$ ]]; then
  echo "Invalid --tag-heavy: $heavy_tag" >&2
  exit 2
fi

lane="inline"
reason="default-inline"
fallback_lane="inline"

# ACP lanes are intentionally disabled. Keep the compatibility arg parsed so
# callers do not break, but route deterministically to Codex lanes only.
if [[ "$scope" == "high" || "$risk" == "high" || "$expected_files" -ge 2 || "$scope" == "medium" || "$heavy_tag" == "true" ]]; then
  lane="codex-subagent"
  reason="sdk-codex-subagent"
elif [[ "$scope" == "medium" || "$risk" == "medium" || "$expected_files" -ge 2 ]]; then
  lane="codex-subagent"
  reason="sdk-codex-subagent"
else
  lane="inline"
  reason="bounded-scope"
fi

case "$lane" in
  codex-subagent)
    fallback_lane="inline"
    ;;
  inline)
    fallback_lane="inline"
    ;;
  *)
    echo "Invalid lane computed: $lane" >&2
    exit 2
    ;;
esac

printf '{"lane":"%s","reason":"%s","fallbackLane":"%s"}\n' "$lane" "$reason" "$fallback_lane"
