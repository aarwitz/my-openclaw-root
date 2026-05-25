---
name: ewag-visual-qa
description: Deterministic visual QA router for ResiLife screenshots (compare, judge, and issue creation only after capture artifacts exist).
metadata: {"clawdbot":{"emoji":"👁️","os":["linux"]}}
---

gog drive search "name contains '<view>-' and parents in '<SUBFOLDER_ID>' and mimeType='image/png'" \
# EWAG Visual QA Router (Lean + Deterministic)

This is the judgment layer. Capture/build/test execution remains in deterministic `ewag-*` scripts and skills.

## Operation Table

| Step | Deterministic Action | Required Output |
|---|---|---|
| Capture check | Ensure artifact exists from `ewag-capture` run | current screenshot link/path |
| Historical compare | Fetch latest prior screenshot for same view from Drive | prior screenshot link |
| Judgment | Produce explicit improved/regressed/unchanged statements | 3 concise comparison bullets |
| Materiality gate | Apply 5 gates before issue creation | create issue or log-only decision |
| Story action | If gated in, create/update Task Manager issue with evidence | issue id + evidence links |
| Fix verification | Re-capture and compare against issue baseline | verify/pass or remain open |

## Use Boundaries

Use this skill for review/evaluate/QA decisions after artifacts exist.

Do not use this skill for:
- raw capture (`/ewag_capture`)
- recording (`/ewag_capture <view> record`)
- build/test execution (`/ewag_build`, `/ewag_test`)

## Materiality Gates

Create story only when all pass:
1. material
2. reproducible
3. actionable
4. non-duplicate
5. worth effort vs backlog alternatives

If any fail, log observation and do not create busywork issue.

## Hard Rules

- No QA judgment if capture failed.
- No regression-shaped story without before/after evidence links.
- No "looks fine" result without explicit improved/regressed/unchanged summary.

## Output Contract

Return:
1. compared artifacts (current/prior)
2. improved/regressed/unchanged bullets
3. gate decision
4. Task Manager action taken (or reason skipped)
5. next action

## On-Demand Deep Reference

For full QA checklist, story template, and detailed verification workflow:
- `workspace/skills/ewag-visual-qa/REFERENCE_FULL.md`
