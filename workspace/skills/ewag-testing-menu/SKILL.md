---
name: ewag_testing_menu
description: Interactive EWAG Telegram menu for capture/record/test/build operations with deterministic callback dispatch.
user-invocable: true
metadata: {"openclaw":{"emoji":"🧪","os":["linux"],"requires":{"channels":["telegram"]}}}
---

# EWAG Testing Menu Router (Lean + Deterministic)

Purpose: provide a stable Telegram inline-button control surface for EWAG test operations.

## Deterministic Behavior

1. Render inline menu with callback IDs.
2. On callback, dispatch by prefix only.
3. Execute mapped script directly.
4. Return pass/fail + artifact links.

No freeform interpretation of callback intent.

## Callback Mapping Contract

- `capture_<view>` -> `~/.openclaw/scripts/ewag-capture.sh <view>`
- `record_<view>` -> `~/.openclaw/scripts/ewag-capture.sh <view> record`
- `test_<cmd>` -> `~/.openclaw/scripts/ewag-test.sh <cmd>`
- `build_<cmd>` -> `~/.openclaw/scripts/ewag-build.sh <cmd>`

If callback does not match known prefixes, return "unknown action" and re-show menu.

## Menu Safety Rules

- Keep callback data short and stable.
- Prefer stateless callback IDs.
- For visual runs, report Drive links produced by scripts.
- Avoid long prose; output operation result and next choices.

## Output Contract

For each button action:
1. Executed command
2. Result status (success/failure)
3. Artifacts (Drive links/log path)
4. Next menu choices

## On-Demand Deep Reference

For full inline-keyboard JSON examples and UX patterns:
- `workspace/skills/ewag-testing-menu/REFERENCE_FULL.md`
