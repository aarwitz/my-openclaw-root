#!/usr/bin/env python3
"""Append a row to the priority queue.

Usage:
  pq_append.py --by <agent_id> --category <cat> --title <t> --details <d> \\
               --priority <1-5> [--id <pq-id>]

Categories: research | engineering | product | ops
Priority:   1 (highest) .. 5 (lowest)
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper

require_wrapper()

QUEUE = Path(os.path.expanduser("~/.openclaw/state/priority-queue.jsonl"))
VALID_BY = {"researcher", "quant", "critic", "trader", "executor",
            "archivist", "developer", "overseer", "dwight", "human"}
VALID_CAT = {"research", "engineering", "product", "ops"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--by", required=True)
    p.add_argument("--category", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--details", default="")
    p.add_argument("--priority", type=int, required=True)
    p.add_argument("--id", default=None)
    a = p.parse_args()

    if a.by not in VALID_BY:
        print(f"ERROR: --by must be one of {sorted(VALID_BY)}", file=sys.stderr)
        return 2
    if a.category not in VALID_CAT:
        print(f"ERROR: --category must be one of {sorted(VALID_CAT)}", file=sys.stderr)
        return 2
    if not 1 <= a.priority <= 5:
        print("ERROR: --priority must be 1..5", file=sys.stderr)
        return 2
    if len(a.title) > 200:
        print("ERROR: --title must be <= 200 chars", file=sys.stderr)
        return 2

    row = {
        "id": a.id or f"pq-{uuid.uuid4().hex[:12]}",
        "submitted_by": a.by,
        "submitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "category": a.category,
        "title": a.title,
        "details": a.details,
        "priority": a.priority,
        "status": "open",
        "claimed_by": None,
        "task_id": None,
    }
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
