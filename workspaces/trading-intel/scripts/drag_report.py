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

DB_PATH = "/home/aaron/.openclaw/state/trading-intel.sqlite"

# Coin-flip Brier for a binary outcome is 0.25; the desk should beat it.
BRIER_COINFLIP = 0.25
BLOCK_LOOKBACK_DAYS = 14
BRIER_LOOKBACK_DAYS = 30
STALE_PREDICTION_DAYS = 21


def normalize_block_reason(reason: str) -> str:
    """Collapse per-intent detail so identical failure classes group together."""
    reason = (reason or "").strip()
    reason = re.sub(r"\[[^\]]*\]", "", reason)
    reason = re.sub(r"\d+(\.\d+)?", "N", reason)
    return reason[:90] or "(no reason recorded)"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "unknown"


def collect_signals(cur: sqlite3.Cursor) -> list[dict]:
    signals: list[dict] = []

    # --- Blocked-intent classes: each class is a concrete software/gate gap.
    try:
        rows = cur.execute(
            """SELECT blocked_reason, COUNT(*) FROM trade_intents
               WHERE state = 'blocked' AND created_at > datetime('now', ?)
               GROUP BY blocked_reason""",
            (f"-{BLOCK_LOOKBACK_DAYS} days",),
        ).fetchall()
        classes: dict[str, int] = {}
        for reason, count in rows:
            key = normalize_block_reason(reason)
            classes[key] = classes.get(key, 0) + count
        for key, count in sorted(classes.items(), key=lambda kv: -kv[1]):
            if count < 3:
                continue  # noise floor: a class must recur to be a signal
            signals.append({
                "id": f"blocked-{slugify(key)}"[:64],
                "severity": min(95, 40 + count * 4),
                "summary": f"{count} intents blocked in {BLOCK_LOOKBACK_DAYS}d by the same class: {key}",
                "evidence": [f"trade_intents state='blocked', class '{key}', count={count} over {BLOCK_LOOKBACK_DAYS}d"],
                "suggested_issue": {
                    "title": f"Eliminate recurring intent-block class: {key[:70]}",
                    "acceptance_criteria": (
                        f"- Root-cause the block class '{key}'\n"
                        "- Fix the responsible stage/gate or document why the block is correct-by-design\n"
                        f"- Fresh drag_report.py run shows this class below 3 occurrences/{BLOCK_LOOKBACK_DAYS}d"
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
            signals.append({
                "id": "calibration-brier-at-coinflip",
                "severity": min(90, int(55 + (brier - BRIER_COINFLIP) * 400)),
                "summary": f"Mean Brier {brier:.4f} over {count} resolved predictions ({BRIER_LOOKBACK_DAYS}d) — at/near coin-flip (0.25)",
                "evidence": [f"predictions resolved {BRIER_LOOKBACK_DAYS}d: n={count}, mean brier_component={brier:.4f}"],
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
        row = cur.execute(
            """SELECT COUNT(*) FROM predictions
               WHERE realized_outcome IS NULL AND resolved_at IS NULL
                 AND predicted_at < datetime('now', ?)""",
            (f"-{STALE_PREDICTION_DAYS} days",),
        ).fetchone()
        stale = int(row[0] or 0)
        if stale >= 10:
            signals.append({
                "id": "predictions-unresolved-backlog",
                "severity": min(80, 30 + stale),
                "summary": f"{stale} predictions older than {STALE_PREDICTION_DAYS}d never resolved — the fast learning loop is starving",
                "evidence": [f"predictions with NULL realized_outcome older than {STALE_PREDICTION_DAYS}d: {stale}"],
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
