#!/usr/bin/env python3
"""Render the active/watch theme context consumed by research passes."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import theme_model as tm

DEFAULT_OUTPUT = tm.ROOT / "state/theme-context.json"


def build_context(db_path=tm.DB_PATH, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    conn = tm.connect(db_path)
    try:
        themes = []
        for row in conn.execute(
            "SELECT * FROM themes WHERE status IN ('active','watch') "
            "ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, score DESC, updated_at DESC"
        ):
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            themes.append({
                "theme_id": row["id"], "statement": row["statement"],
                "beneficiaries": tm.parse_list(row["beneficiaries_json"]),
                "victims": tm.parse_list(row["victims_json"]),
                "status": row["status"], "source": row["source"],
                "age_days": max(0, (now - created).days),
                "score_pct": row["score"], "score_as_of": row["score_as_of"],
                "last_evidence_at": row["last_evidence_at"],
            })
        return {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "authority": "research_context_only_no_trade_authority", "themes": themes}
    finally:
        conn.close()


def write_atomic(payload: dict, path: Path = DEFAULT_OUTPUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--write", action="store_true")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = p.parse_args(argv)
    payload = build_context()
    if args.write:
        write_atomic(payload, Path(args.output))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
