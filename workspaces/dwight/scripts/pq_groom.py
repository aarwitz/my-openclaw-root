#!/usr/bin/env python3
"""Coalesce repeated priority-queue symptoms without deleting history.

The queue is an append-only observation rail, not a second issue tracker. A
recurring failure used to create a fresh id every pass, leaving hundreds of
apparently unreviewed rows. This tool groups only explicit, conservative title
families and marks all but the newest observation ``superseded``. The newest
row remains open for review/promotion. It never resolves a family by itself.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

QUEUE = Path(os.path.expanduser("~/.openclaw/state/priority-queue.jsonl"))
ELIGIBLE = {"open", "claimed"}

# Match titles only. Details often mention downstream components and caused
# unrelated incidents to collapse together in the legacy ad-hoc grooming.
FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.I))
    for name, pattern in (
        ("valuation_snapshot", r"valuation.*(latest|snapshot|selector|as[_ -]?of)|latest.*valuation|valuations query"),
        ("audit_id_collision", r"audit.*(collision|unique)|duplicate audit|unique constraint.*audit|write_postmortems.*collision|postmortem.*audit"),
        ("agent_spawn_hook", r"spawn_agent|subagent.*hook|native hook|delegation.*(stall|block|unavailable)|hook relay"),
        ("telegram_route", r"telegram.*(route|target|group)"),
        ("sqlite_reliability", r"sqlite.*(lock|malformed|contention)|database lock|writer.*(lock|contention)|db lock"),
        ("app_snapshot", r"(app )?snapshot.*(drift|missing|count|red)|snapshot count|red app snapshot"),
        ("broker_reconcile", r"reconcil|broker.*db|db.*broker|broker.*order|order.*broker|position.*drift|fill.*link|placeholder"),
        ("intent_capacity", r"intent.*cap|ready.*(queue|hypoth)|max.positions|position cap|sizing.headroom|risk cap|ready hypotheses not cleared"),
        ("macro_calendar", r"macro|cpi"),
        ("archivist_freshness", r"archiv.*fresh"),
        ("schema_docs", r"schema|architecture|authority docs|archived architecture"),
        ("short_path", r"short.*(ready|intent|author|express)|executor.*buy.only"),
        ("valuation_hygiene", r"value_scan|valuation.*rationale|valuation share|visa valuation|swbi valuation"),
        ("learning_outcomes", r"prediction|learning|calibrat|brier|debrief"),
        ("evidence_freshness", r"stale evidence|evidence.*fresh"),
        ("risk_gate_crash", r"risk.gate.*(crash|error|index)|hypothesis_id crash"),
    )
)


def load_latest(path: Path = QUEUE) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("id"):
            latest[str(row["id"])] = row
    return latest


def eligible(row: dict) -> bool:
    return (
        str(row.get("status") or "").lower() in ELIGIBLE
        and not str(row.get("task_id") or "").strip()
        and str(row.get("claimed_by") or "").lower() in {"", "dwight"}
    )


def family(title: object) -> str | None:
    text = str(title or "")
    for name, pattern in FAMILY_PATTERNS:
        if pattern.search(text):
            return name
    return None


def plan(rows: dict[str, dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows.values():
        name = family(row.get("title"))
        if name and eligible(row):
            grouped[name].append(row)
    changes: list[dict] = []
    for name, members in sorted(grouped.items()):
        if len(members) < 2:
            continue
        keeper = max(
            members,
            key=lambda r: (str(r.get("submitted_at") or ""), str(r.get("id") or "")),
        )
        for row in members:
            if row["id"] == keeper["id"]:
                continue
            changes.append({
                **row,
                "status": "superseded",
                "claimed_by": "dwight",
                "reviewed_by": "dwight",
                "reviewed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "family": name,
                "superseded_by": keeper["id"],
                "disposition_note": "repeated symptom coalesced; newest family observation remains reviewable",
            })
    return changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--queue", type=Path, default=QUEUE)
    args = parser.parse_args()
    changes = plan(load_latest(args.queue))
    if args.apply and changes:
        with args.queue.open("a", encoding="utf-8") as handle:
            for row in changes:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({
        "applied": bool(args.apply),
        "superseded": len(changes),
        "remaining_open": sum(1 for r in load_latest(args.queue).values() if eligible(r))
            if args.apply else None,
    }))
    return 1 if changes and not args.apply else 0


if __name__ == "__main__":
    raise SystemExit(main())
