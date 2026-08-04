#!/usr/bin/env python3
"""Bessent · audit_app_snapshot.py

Verify the deployed `data.json` consumed by the lidisolutions.ai dashboard:
  - exists, parses, recent (<24h)
  - top-level keys include retail_insights + system_health
  - agents includes executor + developer + overseer
  - regime block matches latest DB regime row
  - topology and internal-paper broker labels match the current v2 contract
  - paper cash/equity/positions, inventory lineage, prediction replay,
    selection coverage, and deterministic pipeline health reconcile to state

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
MONEY_TOLERANCE = 0.02


def _parse_iso(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _health_color(issues: list[dict]) -> str:
    if any(issue.get("severity") == "red" for issue in issues):
        return "red"
    return "yellow" if issues else "green"


def check(conn, path: Path, health_checker=None, inventory_checker=None) -> dict:
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

    for key in (
        "retail_insights", "system_health", "agents", "topology", "regime", "counts",
        "broker", "brokerPositions", "selectionFunnel", "predictionReplay",
        "inventoryLineage", "capital_attribution",
    ):
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

    topology = d.get("topology")
    if not isinstance(topology, dict):
        issues.append({"severity": "red", "area": "topology",
                       "detail": f"snapshot topology must be the v5 contract object, got {type(topology).__name__}"})
    elif topology.get("topology_version") != "v5":
        issues.append({"severity": "red", "area": "topology",
                       "detail": f"snapshot topology version={topology.get('topology_version')!r}"})
    elif set(topology.get("agents_order") or []) != agents:
        issues.append({"severity": "red", "area": "topology",
                       "detail": "snapshot topology and agent roster disagree"})
    broker = d.get("broker") or {}
    if broker.get("source") != "sim":
        issues.append({"severity": "red", "area": "broker_source",
                       "detail": f"snapshot broker source={broker.get('source')!r}"})
    if broker.get("name") != "internal_paper":
        issues.append({"severity": "red", "area": "broker_label",
                       "detail": f"snapshot broker name={broker.get('name')!r}"})
    if broker.get("status") != "ACTIVE" or not broker.get("available"):
        issues.append({"severity": "red", "area": "broker_status",
                       "detail": f"snapshot broker unavailable/status={broker.get('status')!r}"})

    broker_positions = d.get("brokerPositions") or []
    account = conn.execute(
        "SELECT cash FROM sim_accounts WHERE book='desk'"
    ).fetchone()
    db_positions = conn.execute(
        "SELECT ticker,qty,current_value FROM sim_positions "
        "WHERE book='desk' AND state='open' ORDER BY ticker"
    ).fetchall()
    db_cash = float(account["cash"]) if account else 0.0
    db_equity = db_cash + sum(float(row["current_value"] or 0.0) for row in db_positions)
    if abs(float(broker.get("cash") or 0.0) - db_cash) > MONEY_TOLERANCE:
        issues.append({"severity": "red", "area": "broker_drift",
                       "detail": f"cash snap={broker.get('cash')} db={db_cash}"})
    if abs(float(broker.get("equity") or 0.0) - db_equity) > MONEY_TOLERANCE:
        issues.append({"severity": "red", "area": "broker_drift",
                       "detail": f"equity snap={broker.get('equity')} db={db_equity}"})
    snap_positions = {str(row.get("symbol")): row for row in broker_positions}
    if set(snap_positions) != {str(row["ticker"]) for row in db_positions}:
        issues.append({"severity": "red", "area": "broker_drift",
                       "detail": "snapshot and DB desk position symbols disagree"})
    else:
        for row in db_positions:
            snap = snap_positions[str(row["ticker"])]
            if abs(float(snap.get("qty") or 0.0) - float(row["qty"])) > 1e-6:
                issues.append({"severity": "red", "area": "broker_drift",
                               "detail": f"{row['ticker']} quantity drift"})
            if abs(float(snap.get("market_value") or 0.0) - float(row["current_value"] or 0.0)) > MONEY_TOLERANCE:
                issues.append({"severity": "red", "area": "broker_drift",
                               "detail": f"{row['ticker']} mark drift"})

    db_resolved = conn.execute(
        "SELECT COUNT(*) AS n FROM predictions WHERE brier_component IS NOT NULL"
    ).fetchone()["n"]
    snap_resolved = int((((d.get("predictionReplay") or {}).get("cohort") or {}).get("n")) or 0)
    if snap_resolved != db_resolved:
        issues.append({"severity": "yellow", "area": "prediction_replay",
                       "detail": f"resolved predictions snap={snap_resolved} db={db_resolved}"})

    selection_counts = {
        row[0]: row[1] for row in conn.execute(
            "SELECT outcome_status,COUNT(*) FROM selection_funnel_outcomes GROUP BY outcome_status"
        )
    }
    if ((d.get("selectionFunnel") or {}).get("coverage") or {}) != selection_counts:
        issues.append({"severity": "yellow", "area": "selection_funnel",
                       "detail": "selection-funnel coverage differs from canonical DB"})

    snapshot_inventory = d.get("inventoryLineage") or {}
    if snapshot_inventory.get("available") is not True:
        issues.append({"severity": "red", "area": "inventory_lineage",
                       "detail": "snapshot inventory-lineage report is unavailable"})
    try:
        if inventory_checker is None:
            from inventory_lineage import build_report as inventory_checker
        expected_inventory = inventory_checker(conn)
        comparable_keys = (
            "prediction_lineage_cutover", "cutover_present", "open_positions",
            "status_counts", "gross_value_by_status", "positions",
        )
        drift = [
            key for key in comparable_keys
            if key in expected_inventory
            and snapshot_inventory.get(key) != expected_inventory.get(key)
        ]
        if drift:
            issues.append({"severity": "red", "area": "inventory_lineage",
                           "detail": "snapshot inventory-lineage drift: " + ",".join(drift)})
    except Exception as exc:
        issues.append({"severity": "red", "area": "inventory_lineage",
                       "detail": f"canonical inventory-lineage audit failed: {type(exc).__name__}: {exc}"})

    daily_equity = float(
        (((d.get("capital_attribution") or {}).get("daily") or {}).get("equity")) or 0.0
    )
    if abs(daily_equity - db_equity) > MONEY_TOLERANCE:
        issues.append({"severity": "red", "area": "capital_attribution",
                       "detail": f"daily equity snap={daily_equity} db={db_equity}"})

    if health_checker is None:
        from audit_pipeline_health import run_checks
        health_checker = run_checks
    expected_health_issues = health_checker(conn)
    expected_health_color = _health_color(expected_health_issues)
    snapshot_health = d.get("system_health") or {}
    if snapshot_health.get("color") != expected_health_color:
        issues.append({"severity": "red", "area": "health_drift",
                       "detail": f"system_health snap={snapshot_health.get('color')} expected={expected_health_color}"})

    color = _health_color(issues)
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
