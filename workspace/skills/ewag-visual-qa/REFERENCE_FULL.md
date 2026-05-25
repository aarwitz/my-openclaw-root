---
name: ewag-visual-qa
description: Evaluate ResiLife iOS UI quality. Compare new screenshots against the latest version in Google Drive, judge layout/typography/contrast/content, decide whether the change improved or regressed the screen, and create Task Manager stories for material issues. Use AFTER capture is done. For raw capture, recording, build, or test execution, use the deterministic ewag-* scripts/skills instead.
metadata: {"clawdbot":{"emoji":"👁️","os":["linux"]}}
---

# EWAG Visual QA — Evaluate, Compare, Decide

This skill is the **judgment layer**. The deterministic capture/build/test work is owned by `ewag-capture`, `ewag-build`, `ewag-test` (and the `~/.openclaw/scripts/ewag-*.sh` scripts they wrap). This skill takes the artifacts they produce and decides what to do about them.

For the operator surface, file paths, ios-agent details, Drive folder IDs, and test catalog, see `EWAG_INFRA.md` — the single source of truth. Do not duplicate that information here.

## When to use this skill

- Aaron asks to "review", "evaluate", "QA", "check", or "audit" a screen or set of screens.
- After a code change that touched UI: capture → compare to last version in Drive → judge.
- Before opening a PR or declaring a feature done.
- When verifying a fix actually fixed the visual issue.

## When NOT to use this skill

- Aaron just wants to *see* a screen → invoke `/ewag_capture <view>` directly. No evaluation needed.
- Aaron wants to record a video → `/ewag_capture <view> record`.
- Aaron wants to run a test or build → `/ewag_test` / `/ewag_build`.
- Aaron wants the menu → `/menu` (ewag-testing-menu skill).

## The QA Loop

```
ewag-capture.sh <view>      ← deterministic; do not duplicate it here
        ↓
this skill kicks in:
   1. Pull latest <view>-*.png from Drive subfolder for that view
   2. Compare new vs previous (what improved? what regressed? what's unchanged?)
   3. Judge against the checklist below
   4. If material issue found → create TM story with both screenshots attached
   5. If fix verified → comment on the TM issue with evidence and move status
```

## Capture (delegate, don't reimplement)

For a single view:
```bash
/ewag_capture <view>            # or
/bash ~/.openclaw/scripts/ewag-capture.sh <view>
```

For all 5 main resident tabs after a build:
```bash
/ewag_capture all --build
```

The script handles SSH to the Mac, xcresulttool extraction, Drive upload, and Telegram delivery. Do not reimplement these steps. The script returns the final Drive link and local file path.

## Compare to Drive History

Fetch the most recent prior screenshot for the same view from its subfolder (IDs in `EWAG_INFRA.md`):

```bash
gog drive search "name contains '<view>-' and parents in '<SUBFOLDER_ID>' and mimeType='image/png'" \
  --max 5 --no-input
```

Sort by date, take the most recent that predates today's capture. View both. Form three explicit observations:

- **Improved:** what specifically looks better
- **Regressed:** what specifically looks worse
- **Unchanged:** what stayed the same (often the most important — confirms the fix didn't break neighbors)

If you cannot articulate at least one of these, the comparison is not done.

## Evaluation Checklist

Run this against every screenshot. Flag only **material** issues (something that would hurt an EWAG demo or a property-owner pitch). Cosmetic micro-issues do not warrant a story.

**Layout & spacing** — consistent padding/margins, no overlap, proper alignment, nothing cut off by safe area / notch / home indicator.

**Typography** — readable contrast, no truncated text that hides meaning, appropriate font sizes for mobile, clear hierarchy.

**Color & contrast** — WCAG AA (4.5:1 normal, 3:1 large), brand colors consistent, interactive elements visually distinguishable, dark + light mode both correct.

**Content quality** — no lorem/placeholder, demo data realistic and professional, images render, numbers/currency formatted.

**Interaction clarity** — buttons look tappable (size + affordance), navigation intuitive (tab bar, back, menus).

## Story Creation — 5 Gates

Before creating a TM story, all 5 must be true:

1. **Material** — would hurt a demo or a real customer
2. **Reproducible** — happens consistently, not a one-off render glitch
3. **Actionable** — there's a clear fix path Jerry can take
4. **Distinct** — not already covered by an open issue
5. **Worth the effort** — fixing it is higher-ROI than the next item in the backlog

If any gate fails, log the observation in `memory/YYYY-MM-DD.md` and move on. Do not create busywork stories.

## Story Template

When all 5 gates pass:

```
Title: <View>: <one-line problem>
Body:
  ## Problem
  <what's wrong, where, when it appears>

  ## Evidence
  - Current: <link to today's Drive screenshot>
  - Previous: <link to prior version that didn't have this>

  ## Acceptance Criteria
  - [ ] <specific visual outcome>
  - [ ] Verified via re-capture and comparison
  - [ ] Screenshot attached to this issue
```

Attach both screenshots to the TM issue (`task-manager` skill).

## Verifying a Fix

1. Run `/ewag_capture <view> --build --branch <fix-branch>`
2. Pull the screenshot referenced in the issue's "Problem" section from Drive
3. Compare new vs that specific historical version
4. If the named issue is gone AND no neighbor regressed → comment "verified via screenshot, see attached" with the new image, move status to in_review/done
5. If still present or a regression appeared → keep the issue open, comment with the new evidence and the next hypothesis

## Hard Rules

- Always upload to the correct Drive **subfolder** for the view, never the root. (IDs in `EWAG_INFRA.md`.)
- Naming: `<view>-YYYY-MM-DD.<ext>`. Recordings always go in `Recordings`.
- No story without both before/after screenshots when an issue is regression-shaped.
- No "looks fine" comment without an explicit improved/regressed/unchanged statement.
- If the capture itself failed (script error), do not write a QA judgment — fix the capture first.
