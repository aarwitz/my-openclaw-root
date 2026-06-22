#!/usr/bin/env python3
"""Claim a priority-queue row for Dwight's Task Manager rail.

This script never talks to Task Manager directly. It records that the row has
been claimed by Dwight so the Dwight-owned queue rail can pick it up on the
next poll, create or update the issue, and write back the resulting task_id.

Usage:
    pq_promote.py <pq-id> [--owner Dwight]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper

require_wrapper()

QUEUE = Path(os.path.expanduser("~/.openclaw/state/priority-queue.jsonl"))


def load_rows() -> list[dict]:
    if not QUEUE.exists():
        return []
    rows: list[dict] = []
    for line in QUEUE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def latest(rows: list[dict], pq_id: str) -> dict | None:
    out: dict | None = None
    for r in rows:
        if r.get("id") == pq_id:
            out = r
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("pq_id")
    p.add_argument("--owner", default="Dwight")
    a = p.parse_args()

    rows = load_rows()
    row = latest(rows, a.pq_id)
    if not row:
        print(f"ERROR: pq id {a.pq_id} not found in {QUEUE}", file=sys.stderr)
        return 2
    if row.get("status") != "open":
        print(f"ERROR: pq {a.pq_id} status is {row.get('status')!r}, not open",
              file=sys.stderr)
        return 2

    update = {
        **row,
        "status": "claimed",
        "claimed_by": a.owner.lower(),
        "task_id": row.get("task_id"),
        "promoted_at": row.get("promoted_at"),
    }
    with QUEUE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(update, ensure_ascii=False) + "\n")

    print(json.dumps({"pq_id": a.pq_id, "claimed_by": a.owner.lower(), "task_id": row.get("task_id")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
