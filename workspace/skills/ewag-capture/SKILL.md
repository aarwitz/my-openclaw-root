---
name: ewag_capture
description: "Screenshot capture → Google Drive. Usage: /ewag_capture <view> [--build] [--branch name] [--no-upload] [--clean]. Views: home coaching nutrition community rewards all."
user-invocable: true
metadata: {"openclaw":{"emoji":"📸","os":["linux"],"requires":{"bins":["openclaw","gog"]}}}
---

# EWAG Screenshot Capture

When this skill is invoked, IMMEDIATELY run the following command with NO analysis, discussion, or deliberation:

```bash
~/.openclaw/scripts/ewag-capture.sh {ARGS}
```

Replace `{ARGS}` with whatever the user typed after `/ewag_capture`. If no args, show the help output by running `~/.openclaw/scripts/ewag-capture.sh --help`.

Do NOT add extra flags. Do NOT interpret the intent. Just run the script and return its output verbatim.

For truly deterministic (zero-LLM) execution: `/bash ~/.openclaw/scripts/ewag-capture.sh <view> [options]`
