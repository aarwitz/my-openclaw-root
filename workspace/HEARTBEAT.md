# HEARTBEAT.md

Each heartbeat is one autonomous work session. Goal: highest-ROI action, ship evidence, update Task Manager.

## 0. Preflight (skip if obviously fresh)

```bash
~/.openclaw/scripts/credential-preflight.sh
```
Before a Drive/Gmail/Calendar write, run a matching cheap probe first. If auth fails, refresh once, retry once, then escalate.

## 1. Pick

- Reconcile: `~/.openclaw/scripts/reconcile-task-manager-with-git.py --apply`
- Highest-ROI Jerry item in the active sprint (impact ÷ effort)
- Check Gmail for actionable client/build emails; reconcile any scheduling against existing calendar entries
- Only create new stories that pass the 5 gates in `autotap-visual-qa`

## 2. Execute

- Pull latest before coding
- Edit on Linux → push to GitHub → `/autotap_build build <branch>` on Mac node
- Run targeted tests via `/autotap_test`; fall back to `test-all` only after a green targeted run
- Up to 3 retries on test failure, then report blocker
- **Serialize all Mac node ops** — never two simulator commands at once (see `AutoTap_INFRA.md`)

## 3. Verify

- `/autotap_capture <view>` (or `all`) for any UI change
- Hand off to `autotap-visual-qa` to compare vs Drive history and judge
- Attach screenshots to the TM issue

## 4. Report

- Compare against the last 3 comments on the issue; comment ONLY if there is materially new info
- Dense: what changed, evidence, next step (≤3 bullets)
- If a PR was opened, add Aaron as reviewer
- Move status: in_progress → in_review → done

## 5. Learn

- Lessons / patterns / decisions → `memory/YYYY-MM-DD.md`
- Durable cross-sprint insight → `MEMORY.md`
- New product opportunity from work → backlog (apply 5 gates)

## Hard Rules

- Do not invent progress. Do not create churn for appearance.
- No outbound Telegram unless material (PR, delivery, blocker, new build).
- If nothing changed: reply `HEARTBEAT_OK` and stop.
- Never use "Linux has no swift" as a final blocker — route iOS validation to mac-build-node.
- Never `bash -c` on the Mac node. Never `systemctl restart` the gateway.
- Detailed iOS infra, Drive folder IDs, ios-agent specifics: `AutoTap_INFRA.md`.
