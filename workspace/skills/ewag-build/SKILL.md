---
name: ewag_build
description: "iOS build operations. Usage: /ewag_build [branch], /ewag_build clean, /ewag_build status."
user-invocable: true
metadata: {"openclaw":{"emoji":"🔨","os":["linux"],"requires":{"bins":["openclaw"]}}}
---

# EWAG iOS Build

When this skill is invoked, IMMEDIATELY run the following command with NO analysis, discussion, or deliberation:

```bash
~/.openclaw/scripts/ewag-build.sh {ARGS}
```

Replace `{ARGS}` with whatever the user typed after `/ewag_build`. If no args, run `~/.openclaw/scripts/ewag-build.sh build` (default: build current branch).

Do NOT add extra flags. Do NOT interpret the intent. Just run the script and return its output verbatim.

For truly deterministic (zero-LLM) execution: `/bash ~/.openclaw/scripts/ewag-build.sh [command] [args]`
