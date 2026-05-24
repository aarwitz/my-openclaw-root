#!/usr/bin/env bash
set -euo pipefail

CFG="/home/aaron/.openclaw/openclaw.json"
TMP="/tmp/openclaw.enforce.github-ssh.json"

jq '
  def addskill: if index("github-ssh") then . else . + ["github-ssh"] end;

  .agents.defaults.skills |= addskill
  | .agents.list |= map(if (.skills|type) == "array" then .skills |= addskill else . end)
  | .channels.telegram.groups |= with_entries(
      .value |= (
        (if (.skills|type) == "array" then .skills |= addskill else . end)
        | (if (.topics|type) == "object" then
            .topics |= with_entries(
              .value |= (if (.skills|type) == "array" then .skills |= addskill else . end)
            )
           else . end)
      )
    )
' "$CFG" > "$TMP"

mv "$TMP" "$CFG"
jq . "$CFG" >/dev/null

echo "[enforce-github-ssh-skill] OK: github-ssh present in defaults, explicit agents, groups, and topics"
