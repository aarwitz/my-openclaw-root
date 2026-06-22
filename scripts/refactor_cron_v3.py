#!/usr/bin/env python3
"""Phase E cron refactor.

Re-targets the six executor cron jobs and adds a new dwight job that polls the
priority queue. Idempotent.

Changes:
  - Six `executor` jobs (pre-market / open / 11am / 1:30 / close / weekly):
      agentId            : executor -> overseer
      sessionKey         : agent:executor:... -> agent:overseer:...
      sessionTarget      : session:agent:executor:... -> session:agent:overseer:...
      delivery.accountId : druck  (unchanged; bot username still @druck_rsl_bot)
      payload.message    : keep the deterministic prefix; expand the narrate
                           clause to make overseer chain the pipeline agents
                           in order (researcher -> quant -> critic -> trader ->
                           executor) and run an archivist async pass.

  - New dwight job: every 6 hours, run pq_promote-poll script.

Backs up `cron/jobs.json` first.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper

require_wrapper()

import json
import shutil
import time
import uuid
from pathlib import Path

CRON = Path("/home/aaron/.openclaw/cron/jobs.json")
BACKUP = CRON.with_suffix(f".json.pre-phase-e.{int(time.time())}.bak")
RUN_WITH_TRACE = "~/.openclaw/scripts/run-with-trace.sh"
OVERSEER_SCRIPTS = "~/.openclaw/workspaces/overseer/scripts"
PQ_APPEND_CMD = (
    f"{RUN_WITH_TRACE} {OVERSEER_SCRIPTS}/pq_append.py --by overseer "
    "--category <cat> --title <t> --details <d> --priority <1-5>"
)

NEW_MESSAGE = (
    "[DETERMINISTIC-PASS-V1] You are AutoTrade (agent id overseer). Run the "
    "deterministic pipeline first, then narrate.\n"
    "\n"
    "Step 1 (deterministic): execute "
    "`~/.openclaw/scripts/run-with-trace.sh --tag cron ~/.openclaw/scripts/trader-pass-deterministic.sh` and capture stdout JSON. "
    "This runs classify_regime -> score_hypotheses -> gate_evaluator -> "
    "execute_intent -> reconcile -> snapshot -> pipeline_health -> app_snapshot.\n"
    "\n"
    "Step 2 (parse): note `regime.current` and `pipeline_health.color` from the "
    "snapshot JSON. Note any non-empty `pipeline_health.issues` or "
    "`app_snapshot.issues`.\n"
    "\n"
    "Step 3 (pipeline agents, in order, only if needed):\n"
    "  - If regime or hypothesis state is stale, spawn `researcher` for fresh "
    "  hypothesis sourcing.\n"
    "  - Then spawn `quant` to score any unscored hypotheses.\n"
    "  - Then spawn `critic` to challenge ready hypotheses.\n"
    "  - Then spawn `trader` to mint trade_intents for any green hypotheses.\n"
    "  - Then spawn `executor` to submit/cancel orders if intents exist.\n"
    "  - Finally spawn `archivist` for the async learning pass.\n"
    "\n"
    "Step 4 (narrate to Aaron via Telegram on account `druck`): one short "
    "message, no markdown tables, no fenced code. Cite regime, hypothesis "
    "count by state, any orders or executions, and any health issues. End "
    "with one concrete next action.\n"
    "\n"
    "Step 5 (queue): if you spotted anything that needs a follow-up issue, "
    "append a priority-queue row via "
    f"`{PQ_APPEND_CMD}`."
)


def main() -> int:
    d = json.loads(CRON.read_text())
    shutil.copy2(CRON, BACKUP)
    print(f"backup: {BACKUP}")
    jobs = d["jobs"]
    changed = 0
    for j in jobs:
        if j.get("agentId") != "executor":
            continue
        j["agentId"] = "overseer"
        j["sessionKey"] = j["sessionKey"].replace("agent:executor", "agent:overseer")
        j["sessionTarget"] = j["sessionTarget"].replace("agent:executor", "agent:overseer")
        # rewrite name + description for clarity
        if "name" in j:
            j["name"] = j["name"].replace("trader-", "overseer-")
        if "description" in j:
            j["description"] = j["description"].replace("for Druck.", "for AutoTrade.")
        # keep delivery.accountId == druck (bot username unchanged) but ensure
        # delivery.agentId is not pinned to executor anywhere
        # rewrite payload message
        if isinstance(j.get("payload"), dict) and j["payload"].get("kind") == "agentTurn":
            j["payload"]["message"] = NEW_MESSAGE
        changed += 1
    print(f"retargeted {changed} jobs to overseer")

    # Add dwight priority-queue poller (idempotent)
    POLL_NAME = "dwight-pq-poll-6h"
    if not any(j.get("name") == POLL_NAME for j in jobs):
        jobs.append({
            "id": str(uuid.uuid4()),
            "agentId": "dwight",
            "sessionTarget": "isolated",
            "name": POLL_NAME,
            "description": "Poll ~/.openclaw/state/priority-queue.jsonl every 6h; promote eligible rows into rsl-task-manager issues on sprint 5 via Dwight's queue rail.",
            "createdAtMs": int(time.time() * 1000),
            "wakeMode": "now",
            "enabled": True,
            "schedule": {"kind": "every", "everyMs": 21_600_000, "anchorMs": int(time.time() * 1000)},
            "payload": {
                "kind": "agentTurn",
                "message": (
                    "[DETERMINISTIC-DWIGHT-PQ] Execute exactly one command: "
                    "`~/.openclaw/scripts/run-with-trace.sh --tag cron ~/.openclaw/scripts/dwight-pq-rail.sh` "
                    "and report claimed/reconciled/failed counts. If anything failed, surface the error "
                    "messages and propose a fix path. Do not modify the queue manually."
                ),
            },
            "delivery": {
                "mode": "announce",
                "to": "telegram:6043080629",
                "channel": "telegram",
                "accountId": "dwight",
            },
            "state": {},
        })
        print(f"added cron job: {POLL_NAME}")
    else:
        print(f"cron job {POLL_NAME} already present; left alone")

    CRON.write_text(json.dumps(d, indent=2) + "\n")
    print(f"wrote {CRON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
