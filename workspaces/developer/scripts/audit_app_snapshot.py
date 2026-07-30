#!/usr/bin/env python3
"""Bessent · audit_app_snapshot.py

Verify the deployed `data.json` consumed by the lidisolutions.ai dashboard:
  - exists, parses, recent (<24h)
  - top-level keys include retail_insights + system_health
  - agents includes executor + developer + overseer
  - regime block matches latest DB regime row
  - canonical counts, calibration, mechanisms and simulated risk reconcile

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
from developer_db import audit, connect, emit, now_iso  # noqa: E402

DEFAULT_DATA_JSON = Path(
    os.path.expanduser("~/.openclaw/state/trader-intel-snapshot/data.json")
)
FRESH_HOURS = 24
RISK_TOLERANCE_PCT = 0.2


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

    topology = d.get("topology") or {}
    if topology.get("broker") != "Internal paper":
        issues.append({"severity": "red", "area": "broker_label",
                       "detail": f"snapshot broker label={topology.get('broker')!r}"})
    broker = d.get("broker") or {}
    if broker.get("source") != "sim":
        issues.append({"severity": "red", "area": "broker_source",
                       "detail": f"snapshot broker source={broker.get('source')!r}"})

    broker_positions = d.get("brokerPositions") or []
    equity = float(broker.get("equity") or 0.0)
    gross = sum(abs(float(p.get("market_value") or 0.0)) for p in broker_positions)
    expected_gross_pct = round(100.0 * gross / equity, 1) if equity > 0 else None
    current = (d.get("risk_gate") or {}).get("current") or {}
    snap_gross_pct = current.get("gross_exposure_pct")
    if expected_gross_pct is not None and (
        snap_gross_pct is None
        or abs(float(snap_gross_pct) - expected_gross_pct) > RISK_TOLERANCE_PCT
    ):
        issues.append({"severity": "red", "area": "risk_drift",
                       "detail": f"gross snap={snap_gross_pct} expected={expected_gross_pct}"})
    snap_names = int(current.get("concurrent_names") or 0)
    if snap_names != len(broker_positions):
        issues.append({"severity": "red", "area": "risk_drift",
                       "detail": f"concurrent_names snap={snap_names} expected={len(broker_positions)}"})

    db_resolved = conn.execute(
        "SELECT COUNT(*) AS n FROM predictions WHERE brier_component IS NOT NULL"
    ).fetchone()["n"]
    snap_resolved = int((d.get("calibration") or {}).get("n_resolved") or 0)
    if snap_resolved != db_resolved:
        issues.append({"severity": "yellow", "area": "calibration_window",
                       "detail": f"resolved predictions snap={snap_resolved} db={db_resolved}"})

    db_mechanisms = conn.execute("SELECT COUNT(*) AS n FROM mechanisms").fetchone()["n"]
    snap_mechanisms = int(((d.get("world_model") or {}).get("summary") or {}).get("total") or 0)
    if snap_mechanisms != db_mechanisms:
        issues.append({"severity": "yellow", "area": "mechanism_window",
                       "detail": f"mechanisms snap={snap_mechanisms} db={db_mechanisms}"})

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
