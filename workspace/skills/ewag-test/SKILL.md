---
name: ewag_test
description: "XCUITest runner. Usage: /ewag_test <TestClass/testMethod>, /ewag_test list, /ewag_test smoke, /ewag_test all."
user-invocable: true
metadata: {"openclaw":{"emoji":"🧪","os":["linux"],"requires":{"bins":["openclaw"]}}}
---

# EWAG Test Runner

When this skill is invoked, IMMEDIATELY run the following command with NO analysis, discussion, or deliberation:

```bash
~/.openclaw/scripts/ewag-test.sh {ARGS}
```

Replace `{ARGS}` with whatever the user typed after `/ewag_test`. If no args, run `~/.openclaw/scripts/ewag-test.sh list` to show available tests.

Do NOT add extra flags. Do NOT interpret the intent. Just run the script and return its output verbatim.

For truly deterministic (zero-LLM) execution: `/bash ~/.openclaw/scripts/ewag-test.sh [command] [args]`
