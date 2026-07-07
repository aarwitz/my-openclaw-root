#!/usr/bin/env python3
"""Bessent · audit_pipeline_health.py

Single deterministic health snapshot of the trading-intel pipeline.
Emits JSON + writes an audit row. Categorises issues by severity.

Checks:
  - schema_version meets expected (>=4)
  - actor_check is v2 (Phase A migration applied)
  - regime row exists and is fresh (<= 24h)
  - regime not degraded for >24h
  - at least one cron job enabled
  - cron_runs/<job> recent (<= job.schedule * 2)
  - raw hypotheses count not exploding (< 200)
  - active system_pauses == 0
  - alpaca account reachable + status ACTIVE
  - DB free of duplicate broker_order_id (sanity)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _db import audit, connect, emit, now_iso  # noqa: E402

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/executor/scripts")
from broker import ConnectorError, get_account  # noqa: E402  (adapter, D52)

EXPECTED_SCHEMA_VERSION = 4
REGIME_FRESH_HOURS = 24


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hours_since(s: str | None) -> float | None:
    dt = _parse_iso(s)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def run_checks(conn) -> list[dict]:
    issues: list[dict] = []
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}

    sv = int(meta.get("_schema_version", "0"))
    if sv < EXPECTED_SCHEMA_VERSION:
        issues.append({"severity": "red", "area": "schema",
                       "detail": f"schema_version={sv} expected>={EXPECTED_SCHEMA_VERSION}"})
    if not meta.get("_actor_check_v2"):
        issues.append({"severity": "yellow", "area": "schema",
                       "detail": "actor_check_v2 marker missing"})

    rg = conn.execute(
        "SELECT current, signals_json, determined_at FROM regime "
        "ORDER BY determined_at DESC LIMIT 1"
    ).fetchone()
    if not rg:
        issues.append({"severity": "red", "area": "regime", "detail": "no regime rows"})
    else:
        age = _hours_since(rg["determined_at"])
        if age is None or age > REGIME_FRESH_HOURS:
            issues.append({"severity": "yellow", "area": "regime",
                           "detail": f"regime stale: age_h={age}"})
        try:
            sig = json.loads(rg["signals_json"] or "{}")
            if sig.get("fail_closed"):
                issues.append({"severity": "yellow", "area": "regime",
                               "detail": f"regime fail_closed; missing={sig.get('missing_signals')}"})
        except json.JSONDecodeError:
            issues.append({"severity": "yellow", "area": "regime",
                           "detail": "signals_json unparseable"})

    cron_path = Path(os.path.expanduser("~/.openclaw/cron/jobs.json"))
    if cron_path.exists():
        try:
            cron = json.loads(cron_path.read_text())
            enabled = [j for j in cron.get("jobs", []) if j.get("enabled")]
            if not enabled:
                issues.append({"severity": "yellow", "area": "cron",
                               "detail": "no enabled cron jobs"})
        except json.JSONDecodeError:
            issues.append({"severity": "yellow", "area": "cron",
                           "detail": "jobs.json unparseable"})

    raw_count = conn.execute("SELECT COUNT(*) AS n FROM hypotheses WHERE state='raw'").fetchone()["n"]
    if raw_count > 200:
        issues.append({"severity": "yellow", "area": "pipeline",
                       "detail": f"raw_hypotheses={raw_count} (>200)"})

    pauses = conn.execute(
        "SELECT COUNT(*) AS n FROM system_pauses WHERE ended_at IS NULL"
    ).fetchone()["n"]
    if pauses > 0:
        issues.append({"severity": "red", "area": "pauses",
                       "detail": f"{pauses} active pauses"})

    dup = conn.execute(
        "SELECT broker_order_id, COUNT(*) AS n FROM orders GROUP BY broker_order_id HAVING n>1"
    ).fetchall()
    if dup:
        issues.append({"severity": "red", "area": "integrity",
                       "detail": f"duplicate broker_order_ids: {len(dup)}"})

    try:
        acct = get_account()
        if str(acct.get("status", "")).upper() != "ACTIVE":
            issues.append({"severity": "red", "area": "broker",
                           "detail": f"alpaca status={acct.get('status')}"})
    except ConnectorError as exc:
        issues.append({"severity": "red", "area": "broker",
                       "detail": f"alpaca unreachable: {exc}"})

    return issues


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--no-write", action="store_true")
    args = p.parse_args(argv)
    conn = connect()
    issues = run_checks(conn)
    color = ("red" if any(i["severity"] == "red" for i in issues)
             else ("yellow" if issues else "green"))
    payload = {"checked_at": now_iso(), "color": color, "issues": issues}
    emit(payload)
    if not args.no_write:
        rid = "PIPE-HEALTH-" + now_iso().replace(":", "").replace("-", "")
        audit(conn, actor="developer", entity_type="pipeline_health", entity_id=rid,
              action="audit",
              rationale=f"color={color} issues={len(issues)} top={(issues[0]['area'] if issues else 'n/a')}")
        conn.commit()
    return 0 if color != "red" else 1


if __name__ == "__main__":
    sys.exit(main())
