#!/usr/bin/env python3
"""Append an explicit review disposition to priority-queue observations."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from pq_groom import QUEUE, eligible, load_latest

FINAL = {"done", "rejected", "superseded"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", action="append", required=True, dest="ids")
    parser.add_argument("--status", choices=sorted(FINAL), required=True)
    parser.add_argument("--note", required=True)
    parser.add_argument("--task-id")
    args = parser.parse_args()
    if len(args.note.strip()) < 12:
        parser.error("--note must contain review evidence, not a label")
    latest = load_latest()
    missing = [rid for rid in args.ids if rid not in latest]
    ineligible = [rid for rid in args.ids if rid in latest and not eligible(latest[rid])]
    if missing or ineligible:
        print(json.dumps({"error": "invalid disposition target", "missing": missing,
                          "not_open": ineligible}))
        return 2
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updates = []
    for rid in args.ids:
        row = {
            **latest[rid],
            "status": args.status,
            "claimed_by": "dwight",
            "reviewed_by": "dwight",
            "reviewed_at": now,
            "disposition_note": args.note.strip(),
        }
        if args.task_id:
            row["task_id"] = str(args.task_id)
        updates.append(row)
    with QUEUE.open("a", encoding="utf-8") as handle:
        for row in updates:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"updated": args.ids, "status": args.status}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
