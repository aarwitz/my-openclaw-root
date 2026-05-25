#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${OPENCLAW_ROOT:-/home/aaron/.openclaw}"
CONFIG_FILE="${OPENCLAW_CONFIG:-${ROOT_DIR}/openclaw.json}"
SKILLS_DIR="${OPENCLAW_SKILLS_DIR:-${ROOT_DIR}/workspace/skills}"
WORKSPACES_DIR="${OPENCLAW_WORKSPACES_DIR:-${ROOT_DIR}/workspaces}"

PROMPT_WARN="${PROMPT_WARN:-1200}"
PROMPT_FAIL="${PROMPT_FAIL:-2200}"
DEFAULT_SKILLS_WARN="${DEFAULT_SKILLS_WARN:-8}"
STARTUP_FILE_WARN="${STARTUP_FILE_WARN:-120}"
SKILL_FILE_WARN="${SKILL_FILE_WARN:-220}"

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is required for this audit." >&2
  exit 2
fi

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "error: config file not found: ${CONFIG_FILE}" >&2
  exit 2
fi

echo "== OpenClaw Context Hardening Audit =="
echo "config: ${CONFIG_FILE}"
echo ""

echo "-- Prompt Size Hotspots (telegram group/topic systemPrompt) --"
PROMPT_TABLE="$(jq -r '
  [
    (.channels.telegram.groups // {} | to_entries[] | {
      where: ("group:" + .key),
      len: (.value.systemPrompt | length // 0)
    }),
    (.channels.telegram.groups // {} | to_entries[] | . as $g | ($g.value.topics // {}) | to_entries[] | {
      where: ("group:" + $g.key + "/topic:" + .key),
      len: (.value.systemPrompt | length // 0)
    })
  ]
  | flatten
  | map(select(.len > 0))
  | sort_by(.len)
  | reverse
  | .[]
  | "\(.len)\t\(.where)"
' "${CONFIG_FILE}")"

if [[ -n "${PROMPT_TABLE}" ]]; then
  echo "${PROMPT_TABLE}" | head -10
else
  echo "none"
fi
echo ""

MAX_PROMPT_LEN="$(printf '%s\n' "${PROMPT_TABLE}" | awk -F '\t' 'NF>0 {print $1; exit} END {if (NR==0) print 0}')"
PROMPT_OVER_WARN="$(printf '%s\n' "${PROMPT_TABLE}" | awk -v n="${PROMPT_WARN}" -F '\t' '$1+0>n {c++} END {print c+0}')"
PROMPT_OVER_FAIL="$(printf '%s\n' "${PROMPT_TABLE}" | awk -v n="${PROMPT_FAIL}" -F '\t' '$1+0>n {c++} END {print c+0}')"

echo "prompt.max_chars=${MAX_PROMPT_LEN}"
echo "prompt.over_warn(${PROMPT_WARN})=${PROMPT_OVER_WARN}"
echo "prompt.over_fail(${PROMPT_FAIL})=${PROMPT_OVER_FAIL}"
echo ""

echo "-- Skill Fanout --"
DEFAULT_SKILLS_COUNT="$(jq -r '.agents.defaults.skills | length' "${CONFIG_FILE}")"
echo "agents.defaults.skills=${DEFAULT_SKILLS_COUNT}"
jq -r '.agents.list[] | [.id, (.skills | length // 0)] | @tsv' "${CONFIG_FILE}" \
  | awk -F '\t' '{printf "agent.%s.skills=%s\n", $1, $2}'
echo ""

echo "-- Startup Markdown Sizes (workspaces/*/{AGENTS,SOUL,USER,MEMORY,HEARTBEAT}.md) --"
STARTUP_TABLE="$(find "${WORKSPACES_DIR}" -maxdepth 2 -type f \( \
  -name 'AGENTS.md' -o -name 'SOUL.md' -o -name 'USER.md' -o -name 'MEMORY.md' -o -name 'HEARTBEAT.md' \
\) -print0 | xargs -0 wc -l | sort -nr)"
if [[ -n "${STARTUP_TABLE}" ]]; then
  printf '%s\n' "${STARTUP_TABLE}" | head -12
fi
echo ""

STARTUP_MAX="$(printf '%s\n' "${STARTUP_TABLE}" | awk '$2 != "total" {if ($1+0>m) m=$1+0} END {print m+0}')"
STARTUP_OVER_WARN="$(printf '%s\n' "${STARTUP_TABLE}" | awk -v n="${STARTUP_FILE_WARN}" '$2 != "total" && $1+0>n {c++} END {print c+0}')"
echo "startup.max_lines=${STARTUP_MAX}"
echo "startup.over_warn(${STARTUP_FILE_WARN})=${STARTUP_OVER_WARN}"
echo ""

echo "-- Skill Doc Sizes (workspace/skills/*/SKILL.md) --"
SKILL_TABLE="$(find "${SKILLS_DIR}" -name 'SKILL.md' -type f -print0 | xargs -0 wc -l | sort -nr)"
if [[ -n "${SKILL_TABLE}" ]]; then
  printf '%s\n' "${SKILL_TABLE}" | head -12
fi
echo ""

SKILL_MAX="$(printf '%s\n' "${SKILL_TABLE}" | awk '$2 != "total" {if ($1+0>m) m=$1+0} END {print m+0}')"
SKILL_OVER_WARN="$(printf '%s\n' "${SKILL_TABLE}" | awk -v n="${SKILL_FILE_WARN}" '$2 != "total" && $1+0>n {c++} END {print c+0}')"
echo "skill.max_lines=${SKILL_MAX}"
echo "skill.over_warn(${SKILL_FILE_WARN})=${SKILL_OVER_WARN}"
echo ""

FAIL=0

if (( PROMPT_OVER_FAIL > 0 )); then
  echo "FAIL: one or more inlined system prompts exceed ${PROMPT_FAIL} chars."
  FAIL=1
fi

if (( DEFAULT_SKILLS_COUNT > DEFAULT_SKILLS_WARN )); then
  echo "WARN: agents.defaults.skills is ${DEFAULT_SKILLS_COUNT} (target <= ${DEFAULT_SKILLS_WARN})."
fi

if (( STARTUP_OVER_WARN > 0 )); then
  echo "WARN: one or more startup markdown files exceed ${STARTUP_FILE_WARN} lines."
fi

if (( SKILL_OVER_WARN > 0 )); then
  echo "WARN: one or more SKILL.md files exceed ${SKILL_FILE_WARN} lines."
fi

echo ""
echo "-- Lean Hardening Actions --"
echo "1. Move giant per-group systemPrompt policy text into compact routing rules + skill docs."
echo "2. Keep agents.defaults.skills minimal (core only), add specialist skills per agent/topic."
echo "3. Keep AGENTS/SOUL/USER startup files short; move procedural depth into on-demand skills."
echo "4. Split large SKILL docs into thin router SKILL + deep reference docs read only when needed."
echo ""

if (( FAIL > 0 )); then
  exit 1
fi

exit 0