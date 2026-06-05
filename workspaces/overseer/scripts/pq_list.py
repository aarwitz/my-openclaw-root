#!/usr/bin/env python3
"""List open priority-queue rows.

Sort: priority asc (1 = highest), then submitted_at desc (freshest first).

Usage:
  pq_list.py [--status open|claimed|done|rejected|all] [--limit N] [--json]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

QUEUE = Path(os.path.expanduser("~/.openclaw/state/priority-queue.jsonl"))


def load() -> list[dict]:
    if not QUEUE.exists():
        return []
    rows: list[dict] = []
    for ln, line in enumerate(QUEUE.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"WARN line {ln}: invalid JSON ({e})", file=sys.stderr)
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--status", default="open",
                   choices=["open", "claimed", "done", "rejected", "all"])
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    # Coalesce by id: latest occurrence wins (append-only log semantics).
    by_id: dict[str, dict] = {}
    for r in load():
        by_id[r.get("id", "")] = r
    rows = list(by_id.values())
    if a.status != "all":
        rows = [r for r in rows if r.get("status") == a.status]
    rows.sort(key=lambda r: (r.get("priority", 9), r.get("submitted_at", "")), reverse=False)
    # priority asc, but freshest first within priority: do a second sort
    rows.sort(key=lambda r: r.get("submitted_at", ""), reverse=True)
    rows.sort(key=lambda r: r.get("priority", 9))
    rows = rows[: a.limit]

    if a.json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print(f"(no rows with status={a.status})")
        return 0
    print(f"{'PRIO':<5}{'ID':<18}{'STATUS':<10}{'BY':<12}{'CAT':<13}TITLE")
    for r in rows:
        prio = r.get("priority", "?")
        rid = r.get("id", "")[:16]
        st = r.get("status", "?")[:9]
        by = (r.get("submitted_by") or "?")[:11]
        cat = (r.get("category") or "?")[:12]
        title = (r.get("title") or "")[:70]
        print(f"{prio:<5}{rid:<18}{st:<10}{by:<12}{cat:<13}{title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
