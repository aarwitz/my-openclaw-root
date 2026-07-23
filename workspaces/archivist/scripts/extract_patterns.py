#!/usr/bin/env python3
"""Archivist · extract_patterns.py

Sweep resolved postmortems and produce `patterns` rows when a recurring
mechanism appears in >=2 postmortems with the same theme key.

Theme keys come from `thesis_analysis_json.theme` (free-text key the archivist
fills during resolution). When ≥2 postmortems share a theme, this script emits
a pattern row with confidence based on count and recency:
  count >= 5 OR (>=3 within 60d)  → high
  count >= 3 OR (>=2 within 30d)  → medium
  else                             → low

Usage:
  python3 extract_patterns.py
  python3 extract_patterns.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
import sqlite3

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _audit(conn, *, entity_id: str, action: str, rationale: str) -> None:
    ts = now_iso()
    # uuid suffix: two patterns written in the same second share the first 24
    # chars of their PATTERN- ids (all timestamp), which collided on audits.id.
    aid = "AUDIT-" + ts.replace(":", "").replace("-", "") + "-" + uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "rationale_concise) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (aid, ts, "archivist", "pattern", entity_id, action, rationale[:500]),
    )


def collect_themes(conn) -> dict[str, list[dict]]:
    rows = conn.execute(
        "SELECT id, hypothesis_id, resolved_at, grade, thesis_analysis_json "
        "FROM postmortems"
    ).fetchall()
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        try:
            th = json.loads(r["thesis_analysis_json"] or "{}")
        except json.JSONDecodeError:
            th = {}
        key = (th.get("theme") or "").strip().lower()
        if not key:
            continue
        buckets[key].append({
            "postmortem_id": r["id"], "hypothesis_id": r["hypothesis_id"],
            "resolved_at": r["resolved_at"], "grade": r["grade"],
            "theme": key,
        })
    return buckets


def _confidence(group: list[dict]) -> str:
    n = len(group)
    now = datetime.now(timezone.utc)
    recent_60 = sum(1 for g in group if _parse_iso(g["resolved_at"]) and
                    (now - _parse_iso(g["resolved_at"])) <= timedelta(days=60))
    recent_30 = sum(1 for g in group if _parse_iso(g["resolved_at"]) and
                    (now - _parse_iso(g["resolved_at"])) <= timedelta(days=30))
    if n >= 5 or recent_60 >= 3:
        return "high"
    if n >= 3 or recent_30 >= 2:
        return "medium"
    return "low"


def build(conn) -> list[dict]:
    buckets = collect_themes(conn)
    existing = {row["pattern"].lower(): row["id"] for row in
                conn.execute("SELECT id, pattern FROM patterns")}
    out: list[dict] = []
    for theme, group in buckets.items():
        if len(group) < 2:
            continue
        if theme in existing:
            continue
        conf = _confidence(group)
        out.append({
            "theme": theme, "confidence": conf,
            "count": len(group),
            "source_postmortem_id": group[0]["postmortem_id"],
            "applies_to": [g["hypothesis_id"] for g in group],
        })
    return out


def write(conn, rows: list[dict]) -> None:
    for r in rows:
        pid = "PATTERN-" + now_iso().replace(":", "").replace("-", "") + "-" + r["theme"].replace(" ", "_")[:24]
        conn.execute(
            "INSERT INTO patterns (id, created_at, pattern, confidence, applies_to_json, "
            "source_postmortem_id, external_validation_status) VALUES "
            "(?, ?, ?, ?, ?, ?, ?)",
            (pid, now_iso(), r["theme"], r["confidence"],
             json.dumps(r["applies_to"]), r["source_postmortem_id"], "unknown"),
        )
        _audit(conn, entity_id=pid, action="extract",
               rationale=f"theme={r['theme']!r} count={r['count']} conf={r['confidence']}")
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    conn = connect()
    rows = build(conn)
    if not args.dry_run and rows:
        write(conn, rows)
    print(json.dumps({"extracted": len(rows), "dry_run": bool(args.dry_run),
                      "rows": rows}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
