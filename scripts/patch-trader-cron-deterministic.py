#!/usr/bin/env python3
"""Patch trader cron jobs to prefix each agent message with the deterministic
script invocation + retail_insights narrative contract.

Idempotent: skips jobs that already contain the marker.

Usage: python3 patch-trader-cron-deterministic.py [--dry-run]
"""

from __future__ import annotations
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper

require_wrapper()

import argparse
import json
import shutil
from pathlib import Path

JOBS_PATH = Path.home() / ".openclaw" / "cron" / "jobs.json"
MARKER = "[DETERMINISTIC-PASS-V1]"

PREFIX = """\
{marker} Run the deterministic prefix FIRST, then narrate.

Step 1: execute `~/.openclaw/scripts/run-with-trace.sh --tag cron ~/.openclaw/scripts/trader-pass-deterministic.sh` and capture stdout JSON.
Step 2: Parse the JSON. Note `pipeline_health.color` and `regime.current` from the snapshot.
Step 3: Read the freshly written snapshot at `snapshot_path` if present, otherwise use the path reported by `app_snapshot.data_json_path`.
Step 4: Compose a short Telegram update consistent with the deterministic JSON.

Telegram message contract (strict, no filler):
  Line 1: `DRUCK_PASS {{pass_name}} | regime={{current}}[{{fail_closed?\"*\":\"\"}}] | health={{color}}`
  Line 2: top scored hypotheses (max 3): `<ticker> {{score}} ({{horizon}})`
  Line 3: posture: `open_positions={{n}}; open_intents={{n}}; cash={{cash_pct}}%`
  Line 4 (only if non-empty): `Blockers: <issues from system_health.issues with severity>=yellow>`

After sending, append three retail-insight bullets back into the snapshot file reported by
`snapshot_path` (or `app_snapshot.data_json_path`) ONLY into the `retail_insights.three_takeaways`
array and `retail_insights.headline` field — preserve all other keys verbatim. If the repo path is
unavailable in this environment, keep the snapshot in the container-safe fallback path and do not
fail the pass.

If `pipeline_health.color` is `red`: send a single line `DRUCK_PASS_DEGRADED <pass_name> red — see audit_pipeline_health` and DO NOT submit any new intents.

Original instruction follows:
---
"""


def patch(jobs: list[dict]) -> tuple[int, int]:
    patched = skipped = 0
    for j in jobs:
        if j.get("agentId") != "executor":
            continue
        msg = j.get("payload", {}).get("message", "")
        if MARKER in msg:
            skipped += 1
            continue
        pass_name = j.get("name", j.get("id", "trader-pass"))
        new_msg = PREFIX.format(marker=MARKER).replace("{{pass_name}}", pass_name) + msg
        j["payload"]["message"] = new_msg
        patched += 1
    return patched, skipped


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    data = json.loads(JOBS_PATH.read_text())
    patched, skipped = patch(data["jobs"])
    print(f"patched={patched} skipped(already_marked)={skipped}")
    if args.dry_run:
        return 0
    backup = JOBS_PATH.with_suffix(".json.bak-pre-deterministic")
    shutil.copy(JOBS_PATH, backup)
    JOBS_PATH.write_text(json.dumps(data, indent=2))
    print(f"backup: {backup}")
    print(f"wrote: {JOBS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
