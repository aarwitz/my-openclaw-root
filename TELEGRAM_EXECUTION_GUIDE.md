# Telegram Execution Guide (No-Script Mode)

This guide answers one question clearly:

Do you need to run scripts yourself from Telegram?

Short answer: no, not for normal operation.

## How to think about OpenClaw

There are two ways to operate:

1. Telegram-first mode (recommended)
- You send intent in Telegram.
- Dwight/Jerry routes work and runs the needed commands internally.
- You review results and approve next steps.

2. Server-CLI mode (operator/debug mode)
- You run scripts directly on the Ubuntu host.
- Use this for maintenance, recovery, or advanced control.

If your goal is shipping work, stay in Telegram-first mode.

## What you should send in Telegram

For coding delivery, message Dwight with an explicit run command:

run issue <id> --repo <absolute-path> [--scope low|medium|high] [--expected-files N] [--risk low|medium|high] [--acp-available true|false] [--acp-agent copilot] [--agent-timeout 180]

Example:
run issue <your_tm_issue_id> --repo /home/aaron/repos/lidi-task-manager --scope high --expected-files 10 --risk medium --acp-available true --acp-agent copilot

Important format rules:
- Use a Task Manager issue id, not a GitHub issue number.
- Do not add a trailing colon at the end of the command.
- Keep the command on one line.

What happens:
1. Dwight resolves issue fields.
2. Lane is selected (inline, codex-subagent, or acp-external).
3. Work executes in the target repo.
4. You get task id, lane, metadata path, and result summary.

## Do I need to type shell scripts in Telegram?

Usually no.

You only need manual shell commands if:
- The gateway is unhealthy.
- You are doing maintenance.
- You are debugging a failed automation path.

## Best practice for "repo + scoped feature"

Use this checklist before execution:

1. Issue quality
- Clear title and objective.
- Acceptance criteria that can be tested.
- Repo path is absolute.
- Assigned owner is correct.

2. Execution quality
- Start with medium/high scope honestly.
- Include expected file count and risk.
- Use acp-available true only when harness is available.

3. Validation quality
- Require test/lint/build evidence in completion.
- Require changed-files summary.
- Require branch/commit reference.

## Operator commands (only when needed)

Run these on the server, not in Telegram:

- openclaw health
- openclaw gateway status
- ~/.openclaw/scripts/safe-restart.sh
- ~/.openclaw/scripts/coding-lane-preflight.sh
- ~/.openclaw/scripts/test-coding-lane-regression.sh

## Fast start flow

1. In Task Manager, create or refine issue.
2. In Telegram to Dwight, send run issue with repo and scope.
3. Review completion evidence.
4. Ask for fixes if acceptance is not fully met.
5. Merge only after proof is complete.

## Common mistake to avoid

Do not send vague asks like "build this feature" without:
- issue id
- absolute repo path
- acceptance criteria

Ambiguity lowers delivery quality.

## If Dwight says "Blocked on issue identity"

Use this recovery sequence:

1. Verify the Task Manager issue id is correct for that repo.
- Quick check on server: `curl -fsS http://127.0.0.1:8000/api/issues/<id> | python3 -m json.tool | head -80`

2. Re-send the Telegram command with a known-good Task Manager issue id and no trailing punctuation.

3. If Telegram still drifts into exploration, run the launcher directly from server CLI (deterministic path):
- `~/.openclaw/scripts/dwight-launch-from-issue.py --issue-id <id> --repo <abs-path> --scope high --expected-files 10 --risk medium --acp-available true --acp-agent copilot --execute`

4. If the issue id is wrong or missing, create/fix the Task Manager issue first, then re-run.

## Bottom line

You can absolutely send a mostly complete repo plus a clearly scoped missing feature and have it built through this system.

Use Telegram as the control plane.
Use server scripts only as operator tools.
