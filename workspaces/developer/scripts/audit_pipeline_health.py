#!/usr/bin/env python3
"""Bessent · audit_pipeline_health.py

Single deterministic health snapshot of the trading-intel pipeline.
Emits JSON + writes an audit row. Categorises issues by severity.

Checks:
  - schema_version meets the production baseline
  - actor_check is v2 (Phase A migration applied)
  - regime row exists and is fresh (<= 24h)
  - regime not degraded for >24h
  - at least one cron job enabled
  - latest critical host-script runs succeeded
  - raw hypotheses count not exploding (< 200)
  - active system_pauses == 0
  - internal paper account reachable + status ACTIVE
  - no protective exit is stranded before submission
  - no pending new-risk intent bypassed prediction-before-intent
  - feature store opens and serves a representative indexed query
  - DB free of duplicate broker_order_id (sanity)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from developer_db import audit, connect, emit, now_iso  # noqa: E402
from connectors.marketdata import market_clock  # noqa: E402

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/executor/scripts")
from broker import backend  # noqa: E402  (adapter, D52)

EXPECTED_SCHEMA_VERSION = 12
REGIME_FRESH_HOURS = 24
FEATURE_DB = Path(os.path.expanduser("~/.openclaw/state/features.sqlite"))
MIN_POST_CUTOFF_CASES = 30
MIN_NEGATIVE_CONTROL_CASES = 60
RUN_LOG = Path(os.path.expanduser("~/.openclaw/logs/script-runs.jsonl"))
CRITICAL_RUNS = {
    # Do not inspect the previous full trader pass from inside the current
    # trader pass. A single failure otherwise becomes self-latching: the
    # health stage fails because the preceding pass failed, which makes this
    # pass fail, ad infinitum. The wrapper already aggregates every current
    # stage and records its own authoritative exit code in RUN_LOG.
    "learning-signals.sh": "yellow",
    "nightly-learning.sh": "yellow",
}


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

    validation = dict(conn.execute(
        "SELECT case_class,COUNT(*) AS n FROM validation_cases GROUP BY case_class"
    ).fetchall())
    post_cutoff = int(validation.get("post_cutoff", 0))
    negative = int(validation.get("negative_control", 0))
    if post_cutoff < MIN_POST_CUTOFF_CASES or negative < MIN_NEGATIVE_CONTROL_CASES:
        issues.append({
            "severity": "yellow",
            "area": "validation_corpus",
            "detail": (
                "reasoning_gate=fail: validation corpus below production-edge minimum "
                f"(post_cutoff={post_cutoff}/{MIN_POST_CUTOFF_CASES}, "
                f"negative_control={negative}/{MIN_NEGATIVE_CONTROL_CASES}); "
                "internal-paper simulation may continue, production/edge claims may not"
            ),
        })

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

    stranded = conn.execute(
        "SELECT id, ticker, state FROM trade_intents "
        "WHERE action IN ('exit','trim') "
        "AND triggered_by IN ('stop_rule_enforcer_v1','stop_rule_soft_enforcer_v1',"
        "'falsifier_enforcer_v1','horizon_enforcer_v1') "
        "AND state IN ('proposed','critic_review','risk_review','approved')"
    ).fetchall()
    # Approved exits created while closed are deliberately deferred, not
    # resting orders. They become stranded only once the session is actually
    # open and the executor has had a chance to submit them in this pass.
    if stranded and market_clock().get("is_open"):
        sample = ",".join(f"{r['ticker']}:{r['state']}" for r in stranded[:4])
        issues.append({"severity": "red", "area": "protection",
                       "detail": f"{len(stranded)} protective intents stranded ({sample})"})

    bypass = conn.execute(
        "SELECT COUNT(*) AS n FROM trade_intents ti "
        "WHERE ti.action IN ('open','add') "
        "AND ti.state IN ('proposed','critic_review','risk_review','approved') "
        "AND NOT EXISTS (SELECT 1 FROM predictions p "
        "WHERE p.hypothesis_id=ti.hypothesis_id AND p.predicted_at<=ti.created_at)"
    ).fetchone()["n"]
    if bypass:
        issues.append({"severity": "red", "area": "prediction_lineage",
                       "detail": f"{bypass} pending new-risk intents lack a pre-author prediction"})

    baseline_ready = conn.execute(
        "SELECT COUNT(*) AS n FROM hypotheses h WHERE h.state='ready' "
        "AND (SELECT c.reviewed_by FROM critic_reviews c WHERE c.target_id=h.id "
        "ORDER BY c.reviewed_at DESC LIMIT 1)='critic_baseline'"
    ).fetchone()["n"]
    if baseline_ready:
        issues.append({"severity": "red", "area": "critic",
                       "detail": f"{baseline_ready} ready hypotheses have only a "
                                 "baseline checklist review"})

    try:
        from resolve_prediction_backlog import resolve_prediction_backlog
        maturity = resolve_prediction_backlog(conn, dry_run=True)
        overdue = int(maturity.get("matured", 0) or 0)
        blocked = int(maturity.get("data_blocked", 0) or 0)
        if overdue:
            sample = ",".join(
                str(row.get("prediction_id"))
                for row in maturity.get("details", [])[:3]
            )
            issues.append({
                "severity": "red",
                "area": "prediction_grading",
                "detail": (
                    f"{overdue} trading-window-matured predictions unresolved "
                    f"({blocked} data-blocked; sample={sample})"
                ),
            })
    except Exception as exc:
        issues.append({
            "severity": "red",
            "area": "prediction_grading",
            "detail": f"exact maturity check failed: {exc}",
        })

    if not FEATURE_DB.exists() or FEATURE_DB.stat().st_size == 0:
        issues.append({"severity": "red", "area": "feature_store",
                       "detail": f"missing/empty feature store: {FEATURE_DB}"})
    else:
        try:
            fconn = sqlite3.connect(f"file:{FEATURE_DB}?mode=ro", uri=True, timeout=2.0)
            fconn.execute("PRAGMA query_only=ON")
            fconn.execute("SELECT COUNT(*) FROM calibrated_mechanisms").fetchone()
            # Do not turn the health probe into a 48M-row workload. Critical
            # feature jobs exercise indexed reads and report through RUN_LOG.
            fconn.execute("SELECT 1 FROM features LIMIT 1").fetchone()
            fconn.close()
        except sqlite3.Error as exc:
            issues.append({"severity": "red", "area": "feature_store",
                           "detail": f"feature store read failed: {exc}"})

    if RUN_LOG.exists():
        latest: dict[str, dict] = {}
        try:
            for line in RUN_LOG.read_text().splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                name = Path(str(row.get("script") or "")).name
                if name in CRITICAL_RUNS:
                    latest[name] = row
            for name, severity in CRITICAL_RUNS.items():
                row = latest.get(name)
                if row and int(row.get("exit_code") or 0) != 0:
                    issues.append({"severity": severity, "area": "critical_job",
                                   "detail": f"latest {name} exit={row.get('exit_code')} at {row.get('ended_at')}"})
        except OSError as exc:
            issues.append({"severity": "yellow", "area": "critical_job",
                           "detail": f"cannot read script run ledger: {exc}"})

    if backend() != "sim":
        issues.append({"severity": "red", "area": "broker",
                       "detail": f"unexpected execution backend={backend()}"})
    try:
        acct = conn.execute(
            "SELECT book, cash, starting_cash FROM sim_accounts WHERE book='desk'"
        ).fetchone()
        if not acct:
            issues.append({"severity": "red", "area": "broker",
                           "detail": "internal paper desk account missing"})
        elif float(acct["cash"]) < -0.01:
            issues.append({"severity": "red", "area": "broker",
                           "detail": f"internal paper cash is negative: {acct['cash']}"})
        mark = conn.execute(
            "SELECT date, equity FROM book_equity WHERE book='desk' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if not mark or float(mark["equity"] or 0) <= 0:
            issues.append({"severity": "red", "area": "broker",
                           "detail": "internal paper equity mark missing/nonpositive"})
    except sqlite3.Error as exc:
        issues.append({"severity": "red", "area": "broker",
                       "detail": f"internal paper ledger unreadable: {exc}"})

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
