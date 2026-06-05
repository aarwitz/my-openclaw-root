#!/usr/bin/env python3
"""Bessent · audit_app_snapshot.py

Verify the deployed `data.json` consumed by the lidisolutions.ai dashboard:
  - exists, parses, recent (<24h)
  - top-level keys include retail_insights + system_health
  - agents includes executor + developer + overseer
  - regime block matches latest DB regime row
  - hypotheses count matches DB within tolerance

Emits JSON + audit row.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import audit, connect, emit, now_iso  # noqa: E402

DEFAULT_DATA_JSON = Path(
    "/home/aaron/repos/lidi-solutions/public/solutions/trader_intel/app/data.json"
)
FRESH_HOURS = 24


def _parse_iso(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def check(conn, path: Path) -> dict:
    issues: list[dict] = []
    if not path.exists():
        return {"checked_at": now_iso(), "color": "red",
                "issues": [{"severity": "red", "area": "snapshot",
                            "detail": f"missing {path}"}]}
    try:
        d = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return {"checked_at": now_iso(), "color": "red",
                "issues": [{"severity": "red", "area": "snapshot",
                            "detail": f"json parse error: {exc}"}]}

    gen = _parse_iso(d.get("generated_at"))
    if gen is None or (datetime.now(timezone.utc) - gen).total_seconds() / 3600.0 > FRESH_HOURS:
        issues.append({"severity": "yellow", "area": "freshness",
                       "detail": f"generated_at={d.get('generated_at')} (>{FRESH_HOURS}h)"})

    for key in ("retail_insights", "system_health", "agents", "regime", "counts"):
        if key not in d:
            issues.append({"severity": "red", "area": "shape",
                           "detail": f"missing top-level key: {key}"})

    agents = {a.get("id") for a in (d.get("agents") or [])}
    for required in ("executor", "developer", "overseer"):
        if required not in agents:
            issues.append({"severity": "yellow", "area": "topology",
                           "detail": f"agent missing in snapshot: {required}"})

    rg = conn.execute(
        "SELECT id, current, determined_at FROM regime "
        "ORDER BY determined_at DESC LIMIT 1"
    ).fetchone()
    snap_reg = d.get("regime") or {}
    if rg and snap_reg:
        if snap_reg.get("current") != rg["current"]:
            issues.append({"severity": "yellow", "area": "regime_drift",
                           "detail": f"snap={snap_reg.get('current')} db={rg['current']}"})
        if snap_reg.get("id") != rg["id"]:
            issues.append({"severity": "yellow", "area": "regime_drift",
                           "detail": f"regime id snap={snap_reg.get('id')} db={rg['id']}"})

    db_hypo = conn.execute("SELECT COUNT(*) AS n FROM hypotheses").fetchone()["n"]
    snap_hypo = (d.get("counts") or {}).get("hypotheses_total", 0)
    if abs(db_hypo - snap_hypo) > 0:
        issues.append({"severity": "yellow", "area": "counts_drift",
                       "detail": f"hypotheses snap={snap_hypo} db={db_hypo}"})

    color = ("red" if any(i["severity"] == "red" for i in issues)
             else ("yellow" if issues else "green"))
    return {"checked_at": now_iso(), "color": color, "issues": issues,
            "data_json_path": str(path), "generated_at": d.get("generated_at")}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--path", default=str(DEFAULT_DATA_JSON))
    p.add_argument("--no-write", action="store_true")
    args = p.parse_args(argv)
    conn = connect()
    payload = check(conn, Path(args.path))
    emit(payload)
    if not args.no_write:
        rid = "APP-SNAP-" + now_iso().replace(":", "").replace("-", "")
        audit(conn, actor="developer", entity_type="app_snapshot", entity_id=rid,
              action="audit",
              rationale=f"color={payload['color']} issues={len(payload['issues'])}")
        conn.commit()
    return 0 if payload["color"] != "red" else 1


if __name__ == "__main__":
    sys.exit(main())
