#!/usr/bin/env python3
"""AutoTrade telemetry plugin for the improvement kernel (AGENTIC_SYSTEM.md).

Reads the desk store READ-ONLY and prints ranked deficiency signals as JSON on
stdout, following the kernel's telemetry contract. The PM pass files the top
unaddressed signal as a TM issue tagged drag:<id> and later verifies, against a
fresh run of this report, that merged fixes actually shrank the signal.

Deliberately small: a signal belongs here only if it is (a) measured from the
store, never inferred, and (b) actionable as a code change. Judgment-quality
problems belong to the desk's fast loop (mechanism updates), not this report.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import feature_store
import gate_evaluator
import worldmodel
import brier_contributors

DB_PATH = "/home/aaron/.openclaw/state/trading-intel.sqlite"
RISK_GATE_PATH = Path("/home/aaron/.openclaw/workspaces/risk/scripts/gate_risk_intents.py")

# Coin-flip Brier for a binary outcome is 0.25; the desk should beat it.
BRIER_COINFLIP = 0.25
BLOCK_LOOKBACK_DAYS = 14
BRIER_LOOKBACK_DAYS = 30
STALE_PREDICTION_DAYS = 21
RISK_REDUCING_ACTIONS = {"exit", "trim"}
OPEN_POSITION_STATES = ("opening", "open", "scaling", "trimming", "closing")
PENDING_INTENT_STATES = ("approved", "submitted", "partial")
EXITING_INTENT_STATES = ("proposed", "critic_review", "risk_review", "approved", "submitted", "partial")


def normalize_block_reason(reason: str) -> str:
    """Collapse per-intent detail so identical failure classes group together."""
    reason = (reason or "").strip()
    reason = re.sub(r"\[[^\]]*\]", "", reason)
    reason = re.sub(r"\d+(\.\d+)?", "N", reason)
    return reason[:90] or "(no reason recorded)"


def parse_failed_gates(reason: str) -> list[str]:
    reason = (reason or "").strip()
    if not reason.startswith("gates_failed:"):
        return []
    core = reason.split("|", 1)[0]
    core = re.sub(r"\[[^\]]*\]", "", core)
    gates = core.removeprefix("gates_failed:").split(",")
    return [gate.strip() for gate in gates if gate.strip()]


def classify_gate_block(row: sqlite3.Row, reevaluated: dict | None) -> dict:
    reason = (row["blocked_reason"] or "").strip()
    action = (row["action"] or "").strip()
    original_failed = parse_failed_gates(reason)
    original_key = "gates_failed:" + ",".join(original_failed) if original_failed else normalize_block_reason(reason)

    if action in RISK_REDUCING_ACTIONS and reevaluated and reevaluated.get("all_pass"):
        return {
            "active": False,
            "class_key": f"legacy_false_positive:{original_key}",
            "summary_key": original_key,
            "evidence": (
                f"intent {row['id']} action={action} was blocked as {original_key} "
                "but current gate stack now passes it (legacy pre-D47 risk-reducing false positive)"
            ),
        }

    failed_now = (
        reevaluated.get("failed_gates", [])
        if reevaluated and reevaluated.get("failed_gates") is not None
        else original_failed
    )
    class_key = "gates_failed:" + ",".join(failed_now) if failed_now else original_key
    gate_details = {
        gate["name"]: gate.get("detail", "")
        for gate in (reevaluated or {}).get("gates", [])
        if not gate.get("pass")
    }
    detail_bits = []
    for name in failed_now:
        detail = gate_details.get(name)
        if detail:
            detail_bits.append(f"{name} -> {detail}")
    evidence = (
        f"intent {row['id']} action={action} blocked as {class_key}; "
        + ("; ".join(detail_bits) if detail_bits else f"recorded_reason={reason}")
    )
    return {
        "active": True,
        "class_key": class_key,
        "summary_key": class_key,
        "evidence": evidence,
    }


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "unknown"


@lru_cache(maxsize=1)
def current_max_positions() -> int | None:
    try:
        text = RISK_GATE_PATH.read_text()
    except OSError:
        return None
    match = re.search(r"^MAX_POSITIONS\s*=\s*(\d+)", text, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


def concurrent_name_snapshot(conn: sqlite3.Connection) -> dict:
    contributors: dict[str, set[str]] = {}
    try:
        for row in conn.execute(
            "SELECT UPPER(ticker) AS ticker, state FROM positions "
            f"WHERE state IN ({','.join('?' * len(OPEN_POSITION_STATES))})",
            OPEN_POSITION_STATES,
        ):
            ticker = row["ticker"]
            if not ticker:
                continue
            contributors.setdefault(ticker, set()).add(f"position:{row['state']}")
    except sqlite3.Error:
        pass
    try:
        for row in conn.execute(
            "SELECT UPPER(ticker) AS ticker, state, action FROM trade_intents "
            f"WHERE state IN ({','.join('?' * len(PENDING_INTENT_STATES))})",
            PENDING_INTENT_STATES,
        ):
            ticker = row["ticker"]
            if not ticker:
                continue
            contributors.setdefault(ticker, set()).add(f"intent:{row['state']}:{(row['action'] or '').lower()}")
    except sqlite3.Error:
        pass
    try:
        exiting = {
            row["ticker"]
            for row in conn.execute(
                "SELECT DISTINCT UPPER(ticker) AS ticker FROM trade_intents "
                "WHERE action IN ('exit','trim') "
                f"AND state IN ({','.join('?' * len(EXITING_INTENT_STATES))})",
                EXITING_INTENT_STATES,
            )
            if row["ticker"]
        }
    except sqlite3.Error:
        exiting = set()
    active_slots = [
        {"ticker": ticker, "sources": sorted(sources)}
        for ticker, sources in sorted(contributors.items())
        if ticker not in exiting
    ]
    return {"count": len(active_slots), "active_slots": active_slots, "exiting_tickers": sorted(exiting)}


def summarize_slots(slots: list[dict], limit: int = 4) -> str:
    if not slots:
        return "none"
    shown = []
    for slot in slots[:limit]:
        shown.append(f"{slot['ticker']}[{','.join(slot.get('sources', []))}]")
    remaining = len(slots) - len(shown)
    if remaining > 0:
        shown.append(f"+{remaining} more")
    return ", ".join(shown)


def parse_concurrent_name_reason(reason: str) -> tuple[int | None, int | None]:
    match = re.search(r"concurrent_names=(\d+)\s*>=\s*cap=(\d+)", reason or "")
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _first_ticker(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not data or not isinstance(data, list):
        return None
    ticker = data[0]
    return str(ticker).upper() if ticker else None


def _window_available(prices: list[dict], entry_iso: str, horizon_days: int) -> bool:
    dates = [bar["t"] for bar in prices]
    index = 0
    while index < len(dates) and dates[index] < entry_iso:
        index += 1
    return index < len(dates) and index + horizon_days < len(dates)


def count_stale_predictions(cur: sqlite3.Cursor) -> int:
    rows = cur.execute(
        "SELECT p.id, p.predicted_at, p.horizon, h.tickers "
        "FROM predictions p JOIN hypotheses h ON h.id = p.hypothesis_id "
        "WHERE p.realized_outcome IS NULL AND p.resolved_at IS NULL "
        "AND p.predicted_at < datetime('now', ?) "
        "ORDER BY p.predicted_at ASC",
        (f"-{STALE_PREDICTION_DAYS} days",),
    ).fetchall()
    if not rows:
        return 0
    spy_prices = feature_store._prices("SPY", 4000)
    ticker_cache: dict[str, list[dict]] = {}
    stale = 0
    for row in rows:
        horizon_days = worldmodel.HORIZON_DAYS.get(row["horizon"], 15)
        if not _window_available(spy_prices, row["predicted_at"][:10], horizon_days):
            continue
        ticker = _first_ticker(row["tickers"])
        if not ticker:
            stale += 1
            continue
        if ticker not in ticker_cache:
            try:
                ticker_cache[ticker] = feature_store._prices(ticker, 4000)
            except Exception:
                ticker_cache[ticker] = []
        if _window_available(ticker_cache[ticker], row["predicted_at"][:10], horizon_days):
            stale += 1
        else:
            stale += 1
    return stale


def classify_risk_block(row: sqlite3.Row) -> dict:
    reason = (row["blocked_reason"] or "").strip()
    historical_count, historical_cap = parse_concurrent_name_reason(reason)
    if not reason.startswith("risk:concurrent_names="):
        return {
            "active": True,
            "class_key": normalize_block_reason(reason),
            "summary_key": normalize_block_reason(reason),
            "evidence": f"intent {row['id']} action={row['action']} blocked as {reason}",
        }

    try:
        snapshot = concurrent_name_snapshot(row["conn"])
    except sqlite3.Error as exc:
        return {
            "active": True,
            "class_key": normalize_block_reason(reason),
            "summary_key": normalize_block_reason(reason),
            "evidence": f"intent {row['id']} action={row['action']} blocked as {reason}; attribution_unavailable={exc}",
        }

    current_cap = current_max_positions()
    slot_summary = summarize_slots(snapshot["active_slots"])
    if current_cap is not None and snapshot["count"] < current_cap:
        return {
            "active": False,
            "class_key": f"legacy_false_positive:{normalize_block_reason(reason)}",
            "summary_key": normalize_block_reason(reason),
            "evidence": (
                f"intent {row['id']} action={row['action']} blocked as {reason} "
                f"but live concurrent_names={snapshot['count']}/{current_cap}; slots={slot_summary}"
            ),
        }

    current_label = (
        f"live concurrent_names={snapshot['count']}/{current_cap}"
        if current_cap is not None
        else f"live concurrent_names={snapshot['count']}"
    )
    return {
        "active": True,
        "class_key": normalize_block_reason(reason),
        "summary_key": normalize_block_reason(reason),
        "evidence": (
            f"intent {row['id']} action={row['action']} blocked as {reason}; "
            f"{current_label}; slots={slot_summary}; historical={historical_count}/{historical_cap}"
        ),
    }


def collect_signals(cur: sqlite3.Cursor) -> list[dict]:
    signals: list[dict] = []

    # --- Blocked-intent classes: each class is a concrete software/gate gap.
    try:
        rows = cur.execute(
            """SELECT id, action, blocked_reason FROM trade_intents
               WHERE state = 'blocked' AND created_at > datetime('now', ?)
               ORDER BY created_at DESC""",
            (f"-{BLOCK_LOOKBACK_DAYS} days",),
        ).fetchall()
        active_classes: dict[str, dict] = {}
        legacy_false_positives: dict[str, dict] = {}
        for row in rows:
            reason = (row["blocked_reason"] or "").strip()
            reevaluated = None
            if reason.startswith("risk:"):
                classified = classify_risk_block({"id": row["id"], "action": row["action"], "blocked_reason": reason, "conn": cur.connection})
            elif reason.startswith("gates_failed:"):
                reevaluated = gate_evaluator.evaluate(cur.connection, row["id"])
                classified = classify_gate_block(row, reevaluated)
            else:
                classified = classify_gate_block(row, reevaluated)
            bucket_set = active_classes if classified["active"] else legacy_false_positives
            bucket = bucket_set.setdefault(
                classified["class_key"],
                {"count": 0, "summary_key": classified["summary_key"], "evidence": []},
            )
            bucket["count"] += 1
            if len(bucket["evidence"]) < 3:
                bucket["evidence"].append(classified["evidence"])

        for key, meta in sorted(active_classes.items(), key=lambda kv: -kv[1]["count"]):
            count = meta["count"]
            if count < 3:
                continue  # noise floor: a class must recur to be a signal
            signals.append({
                "id": f"blocked-{slugify(meta['summary_key'])}"[:64],
                "severity": min(95, 40 + count * 4),
                "summary": f"{count} intents blocked in {BLOCK_LOOKBACK_DAYS}d by the same class: {meta['summary_key']}",
                "evidence": [
                    f"trade_intents state='blocked', class '{meta['summary_key']}', count={count} over {BLOCK_LOOKBACK_DAYS}d",
                    *meta["evidence"],
                ],
                "suggested_issue": {
                    "title": f"Eliminate recurring intent-block class: {meta['summary_key'][:70]}",
                    "acceptance_criteria": (
                        f"- Root-cause the block class '{meta['summary_key']}'\n"
                        "- Fix the responsible stage/gate or document why the block is correct-by-design\n"
                        f"- Fresh drag_report.py run shows this class below 3 occurrences/{BLOCK_LOOKBACK_DAYS}d"
                    ),
                    "assignee": "Developer",
                },
            })
        for key, meta in sorted(legacy_false_positives.items(), key=lambda kv: -kv[1]["count"]):
            count = meta["count"]
            if count < 3:
                continue
            signals.append({
                "id": f"legacy-blocked-{slugify(meta['summary_key'])}"[:64],
                "severity": min(75, 25 + count * 2),
                "summary": (
                    f"{count} blocked intents in {BLOCK_LOOKBACK_DAYS}d were legacy false positives, "
                    f"not an active recurrence: {meta['summary_key']}"
                ),
                "evidence": [
                    (
                        f"{count} blocked rows still in trade_intents over {BLOCK_LOOKBACK_DAYS}d re-evaluate "
                        "to pass under the current gate stack"
                    ),
                    *meta["evidence"],
                ],
                "suggested_issue": {
                    "title": f"Clean up legacy blocked intents: {meta['summary_key'][:70]}",
                    "acceptance_criteria": (
                        "- Confirm the current gate stack would pass these rows\n"
                        "- Decide whether to requeue, cancel, or leave them as historical artifacts\n"
                        "- Keep drag_report attribution explicit so legacy false positives do not masquerade as an active gate recurrence"
                    ),
                    "assignee": "Developer",
                },
            })
    except sqlite3.Error as exc:
        print(f"WARN: blocked-intent signal skipped: {exc}", file=sys.stderr)

    # --- Calibration: resolved-prediction Brier vs coin-flip.
    try:
        row = cur.execute(
            """SELECT COUNT(*), AVG(brier_component) FROM predictions
               WHERE resolved_at > datetime('now', ?) AND brier_component IS NOT NULL""",
            (f"-{BRIER_LOOKBACK_DAYS} days",),
        ).fetchone()
        count, brier = int(row[0] or 0), row[1]
        if count >= 20 and brier is not None and brier >= BRIER_COINFLIP - 0.01:
            evidence = [f"predictions resolved {BRIER_LOOKBACK_DAYS}d: n={count}, mean brier_component={brier:.4f}"]
            try:
                report = brier_contributors.build_report(cur.connection, BRIER_LOOKBACK_DAYS)
                selected = report.get("selected_contributor") or {}
                delta = report.get("replay", {}).get("delta_mean_brier")
                if selected:
                    evidence.append(
                        "worst contributor: "
                        f"{selected.get('mechanism')} / {selected.get('regime')} / {selected.get('horizon')} "
                        f"count={selected.get('count')} total_brier={selected.get('total_brier')} "
                        f"mean_brier={selected.get('mean_brier')}"
                    )
                if delta is not None:
                    baseline = report["replay"].get("baseline_class_family_most_observed", {}).get("mean_brier")
                    fixed = report["replay"].get("fixed_root_family_horizon_preferred", {}).get("mean_brier")
                    evidence.append(
                        "post-fix replay delta: "
                        f"before={baseline} after={fixed} delta={delta}"
                    )
                selected_delta = report.get("replay", {}).get("selected_contributor_delta")
                if selected and selected_delta is not None:
                    base_means = report["replay"].get("baseline_class_family_most_observed", {}).get("mechanism_means", {})
                    fix_means = report["replay"].get("fixed_root_family_horizon_preferred", {}).get("mechanism_means", {})
                    mech = selected.get("mechanism")
                    evidence.append(
                        "selected contributor replay delta: "
                        f"mechanism={mech} before={base_means.get(mech)} "
                        f"after={fix_means.get(mech)} delta={selected_delta}"
                    )
                reason = report.get("selection_reason")
                if reason:
                    evidence.append(f"selection_reason: {reason}")
            except Exception as exc:
                evidence.append(f"brier_contributor_breakdown_unavailable: {exc}")
            signals.append({
                "id": "calibration-brier-at-coinflip",
                "severity": min(90, int(55 + (brier - BRIER_COINFLIP) * 400)),
                "summary": f"Mean Brier {brier:.4f} over {count} resolved predictions ({BRIER_LOOKBACK_DAYS}d) — at/near coin-flip (0.25)",
                "evidence": evidence,
                "suggested_issue": {
                    "title": "Calibration: identify and fix the largest Brier contributor",
                    "acceptance_criteria": (
                        "- Deterministic breakdown of Brier by mechanism/regime/horizon (script, not prose)\n"
                        "- One concrete fix targeting the worst contributor (feature, data source, or scoring change)\n"
                        "- Change flows as rule_proposal if it alters trading parameters\n"
                        f"- Fresh drag_report.py shows mean Brier below {BRIER_COINFLIP}"
                    ),
                    "assignee": "Quant",
                },
            })
    except sqlite3.Error as exc:
        print(f"WARN: calibration signal skipped: {exc}", file=sys.stderr)

    # --- Unresolved predictions past horizon: the learning loop starving.
    try:
        stale = count_stale_predictions(cur)
        if stale >= 10:
            signals.append({
                "id": "predictions-unresolved-backlog",
                "severity": min(80, 30 + stale),
                "summary": (
                    f"{stale} predictions older than {STALE_PREDICTION_DAYS}d are past horizon and still unresolved "
                    "— the fast learning loop is starving"
                ),
                "evidence": [
                    f"predictions with NULL realized_outcome older than {STALE_PREDICTION_DAYS}d and past horizon: {stale}"
                ],
                "suggested_issue": {
                    "title": "Resolve or expire the stale-prediction backlog",
                    "acceptance_criteria": (
                        "- Resolver handles all past-horizon predictions (resolve, or expire with an audit row)\n"
                        f"- Fresh drag_report.py shows <10 unresolved predictions older than {STALE_PREDICTION_DAYS}d"
                    ),
                    "assignee": "Developer",
                },
            })
    except sqlite3.Error as exc:
        print(f"WARN: stale-prediction signal skipped: {exc}", file=sys.stderr)

    signals.sort(key=lambda s: -s["severity"])
    return signals


def main() -> int:
    try:
        db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        print(json.dumps({"project": "AutoTrade", "error": f"store unavailable: {exc}", "signals": []}))
        return 1
    try:
        signals = collect_signals(db.cursor())
    finally:
        db.close()
    print(json.dumps({
        "project": "AutoTrade",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "signals": signals,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
