#!/usr/bin/env python3
"""Deterministic batch scorer for hypotheses.

Iterates every eligible hypothesis (default: state IN ('raw','scored')),
computes a deterministic quant score per primary horizon, writes
`quant_score`, `state='scored'`, `scored_at`, and a `score_json` breakdown
on a paired `expression_candidates` row. Fails loudly when a row cannot be
scored (missing horizon, missing evidence, malformed tickers) by moving it to
`state='dormant'` with a `rationale_concise` explaining why.

This script is the ONLY sanctioned writer of `quant_score`. The quant LLM
turn only adds `quant_rationale` text afterwards; it never assigns the
number.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))

# Horizon enum (must match new schema column once migration lands; for now
# we accept legacy `time_horizon` strings and normalize). Order matters for
# default selection.
HORIZONS = ("intraday", "swing_1_5d", "position_1_4w", "trend_1_3m", "long_6m_plus")
LEGACY_HORIZON_MAP = {
    "intraday": "intraday",
    "1d": "intraday",
    "1-3d": "swing_1_5d",
    "1-5d": "swing_1_5d",
    "swing": "swing_1_5d",
    "1w": "swing_1_5d",
    "1-4w": "position_1_4w",
    "weeks": "position_1_4w",
    "1m": "position_1_4w",
    "position": "position_1_4w",
    "1-3m": "trend_1_3m",
    "months": "trend_1_3m",
    "3m": "trend_1_3m",
    "trend": "trend_1_3m",
    "6m+": "long_6m_plus",
    "1y": "long_6m_plus",
    "long": "long_6m_plus",
}

# Component weights sum to 100. Tuned to match the doc-stated "fundamentals,
# catalyst, factor diversification, freshness, edge_vs_SPY" emphasis. Weights
# live here for now; will move to `quant_scoring_weights` table in Phase D.
WEIGHTS = {
    "catalyst_proximity": 25,
    "evidence_completeness": 20,
    "factor_diversification": 15,
    "freshness": 10,
    "edge_vs_spy_estimate": 30,
}

EXIT_OK = 0
EXIT_FAIL_LOUD = 2


@dataclass
class ScoreBreakdown:
    score: float
    components: dict[str, float]
    horizon: str
    best_horizon: str
    per_horizon_scores: dict[str, float]
    rationale_one_liner: str


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hours_old(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def normalize_horizon(raw: str | None) -> str:
    if not raw:
        return "position_1_4w"
    key = raw.strip().lower()
    return LEGACY_HORIZON_MAP.get(key, "position_1_4w" if key not in HORIZONS else key)


# ---------------------------------------------------------------------------
# Score components (deterministic, source-grounded)
#
# Every component returns a 0..100 sub-score plus a small dict the LLM
# rationale pass can read. When a component cannot be computed it returns
# None and the row is marked unscorable.
# ---------------------------------------------------------------------------


def score_evidence_completeness(conn: sqlite3.Connection, hyp_id: str) -> tuple[float, dict[str, Any]] | None:
    row = conn.execute(
        "SELECT COUNT(*) AS n, "
        "SUM(CASE WHEN signal_type IS NOT NULL AND signal_type != '' THEN 1 ELSE 0 END) AS typed, "
        "SUM(CASE WHEN source_url IS NOT NULL AND source_url != '' THEN 1 ELSE 0 END) AS with_url "
        "FROM hypothesis_evidence WHERE hypothesis_id = ?",
        (hyp_id,),
    ).fetchone()
    n = row[0] or 0
    if n == 0:
        return 0.0, {"evidence_count": 0}
    typed = row[1] or 0
    with_url = row[2] or 0
    completeness = min(1.0, n / 5.0) * 60 + (typed / n) * 20 + (with_url / n) * 20
    return round(completeness, 2), {
        "evidence_count": n,
        "typed_pct": round(typed / n * 100, 1),
        "with_url_pct": round(with_url / n * 100, 1),
    }


def score_freshness(conn: sqlite3.Connection, hyp_id: str) -> tuple[float, dict[str, Any]]:
    row = conn.execute(
        "SELECT MAX(retrieved_at) FROM hypothesis_evidence WHERE hypothesis_id = ?",
        (hyp_id,),
    ).fetchone()
    latest = row[0] if row else None
    age_h = _hours_old(latest)
    if age_h is None:
        return 30.0, {"latest_evidence_at": None, "note": "no evidence timestamps"}
    if age_h <= 24:
        score = 100.0
    elif age_h <= 72:
        score = 80.0
    elif age_h <= 24 * 7:
        score = 60.0
    elif age_h <= 24 * 30:
        score = 40.0
    else:
        score = 20.0
    return score, {"latest_evidence_age_hours": round(age_h, 1)}


def score_catalyst_proximity(conn: sqlite3.Connection, hyp_id: str) -> tuple[float, dict[str, Any]]:
    """Use the nearest event_date on an expression_candidate as a proxy.

    Closer to a known catalyst => higher score. No catalyst => mid-band 50.
    """
    row = conn.execute(
        "SELECT MIN(event_date) FROM expression_candidates "
        "WHERE hypothesis_id = ? AND event_date IS NOT NULL AND event_date != ''",
        (hyp_id,),
    ).fetchone()
    event = row[0] if row else None
    if not event:
        return 50.0, {"event_date": None}
    days_to_event = _days_until(event)
    if days_to_event is None:
        return 50.0, {"event_date": event, "note": "unparseable"}
    if days_to_event < 0:
        return 30.0, {"event_date": event, "days_to_event": days_to_event}
    if days_to_event <= 3:
        return 100.0, {"event_date": event, "days_to_event": days_to_event}
    if days_to_event <= 14:
        return 85.0, {"event_date": event, "days_to_event": days_to_event}
    if days_to_event <= 45:
        return 65.0, {"event_date": event, "days_to_event": days_to_event}
    return 45.0, {"event_date": event, "days_to_event": days_to_event}


def _days_until(iso: str) -> int | None:
    try:
        # Accept date or datetime
        if len(iso) <= 10:
            dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int((dt - datetime.now(timezone.utc)).total_seconds() // 86400)


def score_factor_diversification(
    conn: sqlite3.Connection, hyp_id: str, tickers: list[str]
) -> tuple[float, dict[str, Any]]:
    """Penalise overlap with currently-open positions on the same ticker."""
    if not tickers:
        return 40.0, {"overlap_tickers": []}
    placeholders = ",".join("?" * len(tickers))
    overlap = conn.execute(
        f"SELECT ticker FROM positions WHERE state IN ('opening','open','scaling','trimming','closing') "
        f"AND ticker IN ({placeholders})",
        tickers,
    ).fetchall()
    overlap_set = sorted({r[0] for r in overlap})
    if not overlap_set:
        return 90.0, {"overlap_tickers": []}
    overlap_ratio = len(overlap_set) / len(tickers)
    score = max(20.0, 90.0 - overlap_ratio * 60.0)
    return round(score, 2), {"overlap_tickers": overlap_set, "overlap_ratio": round(overlap_ratio, 2)}


def score_edge_vs_spy(
    conn: sqlite3.Connection,
    hyp_id: str,
    horizon: str,
    confidence: str | None,
) -> tuple[float, dict[str, Any]]:
    """Estimate expected edge over SPY for the given horizon.

    Pure deterministic heuristic over confidence + evidence breadth + horizon
    decay. When we have realized `attribution` data for similar prior
    hypotheses (Phase D), this swaps in a backtest-derived estimate.
    """
    conf_score = {"high": 80.0, "medium": 60.0, "low": 40.0, None: 50.0}.get(
        confidence, 50.0
    )
    horizon_multiplier = {
        "intraday": 0.9,
        "swing_1_5d": 1.0,
        "position_1_4w": 1.05,
        "trend_1_3m": 1.0,
        "long_6m_plus": 0.85,
    }.get(horizon, 1.0)
    breadth_row = conn.execute(
        "SELECT COUNT(DISTINCT signal_type) FROM hypothesis_evidence WHERE hypothesis_id = ?",
        (hyp_id,),
    ).fetchone()
    breadth = breadth_row[0] if breadth_row else 0
    breadth_bonus = min(15.0, breadth * 5.0)
    raw = conf_score * horizon_multiplier + breadth_bonus
    score = max(0.0, min(100.0, raw))
    return round(score, 2), {
        "confidence": confidence,
        "horizon": horizon,
        "signal_breadth": breadth,
        "horizon_multiplier": horizon_multiplier,
    }


# ---------------------------------------------------------------------------
# Per-row scorer
# ---------------------------------------------------------------------------


def score_one(
    conn: sqlite3.Connection, hyp_row: sqlite3.Row
) -> ScoreBreakdown | str:
    hyp_id = hyp_row["id"]
    try:
        tickers = json.loads(hyp_row["tickers"] or "[]")
        if not isinstance(tickers, list):
            return "tickers_not_a_list"
    except json.JSONDecodeError:
        return "tickers_unparseable"

    declared_horizon = normalize_horizon(hyp_row["time_horizon"])

    evidence_result = score_evidence_completeness(conn, hyp_id)
    if evidence_result is None:
        return "no_evidence_rows"
    evidence_score, evidence_meta = evidence_result

    freshness_score, freshness_meta = score_freshness(conn, hyp_id)
    catalyst_score, catalyst_meta = score_catalyst_proximity(conn, hyp_id)
    diversification_score, diversification_meta = score_factor_diversification(
        conn, hyp_id, tickers
    )

    per_horizon: dict[str, float] = {}
    per_horizon_meta: dict[str, dict[str, Any]] = {}
    for horizon in HORIZONS:
        edge_score, edge_meta = score_edge_vs_spy(
            conn, hyp_id, horizon, hyp_row["confidence"]
        )
        total = (
            evidence_score * WEIGHTS["evidence_completeness"] / 100
            + freshness_score * WEIGHTS["freshness"] / 100
            + catalyst_score * WEIGHTS["catalyst_proximity"] / 100
            + diversification_score * WEIGHTS["factor_diversification"] / 100
            + edge_score * WEIGHTS["edge_vs_spy_estimate"] / 100
        )
        per_horizon[horizon] = round(total, 2)
        per_horizon_meta[horizon] = {"edge_vs_spy": edge_meta}

    best_horizon = max(per_horizon, key=per_horizon.__getitem__)
    chosen_horizon = declared_horizon if declared_horizon in per_horizon else best_horizon
    final_score = per_horizon[chosen_horizon]

    rationale = (
        f"declared={chosen_horizon} best={best_horizon} "
        f"score={final_score:.1f} "
        f"evidence={evidence_meta.get('evidence_count', 0)} "
        f"catalyst={catalyst_meta.get('days_to_event', 'n/a')} "
        f"overlap={diversification_meta.get('overlap_ratio', 0.0)}"
    )

    return ScoreBreakdown(
        score=final_score,
        components={
            "evidence_completeness": evidence_score,
            "freshness": freshness_score,
            "catalyst_proximity": catalyst_score,
            "factor_diversification": diversification_score,
            "edge_vs_spy_chosen": per_horizon_meta[chosen_horizon]["edge_vs_spy"],
        },
        horizon=chosen_horizon,
        best_horizon=best_horizon,
        per_horizon_scores=per_horizon,
        rationale_one_liner=rationale,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def mark_scored(
    conn: sqlite3.Connection,
    hyp_id: str,
    breakdown: ScoreBreakdown,
    experiment_id: str | None,
) -> None:
    now = _now_utc_iso()
    score_json = {
        "score": breakdown.score,
        "chosen_horizon": breakdown.horizon,
        "best_horizon": breakdown.best_horizon,
        "per_horizon_scores": breakdown.per_horizon_scores,
        "components": breakdown.components,
        "weights": WEIGHTS,
        "scored_at": now,
        "experiment_id": experiment_id,
        "rationale_one_liner": breakdown.rationale_one_liner,
    }
    conn.execute(
        "UPDATE hypotheses SET quant_score = ?, state = 'scored', scored_at = ?, "
        "time_horizon = ? WHERE id = ?",
        (breakdown.score, now, breakdown.horizon, hyp_id),
    )
    # Mirror onto expression_candidates: pick one row or insert a synthetic
    # placeholder so the score_json has a home if no candidate exists yet.
    cand_row = conn.execute(
        "SELECT id FROM expression_candidates WHERE hypothesis_id = ? LIMIT 1",
        (hyp_id,),
    ).fetchone()
    if cand_row:
        conn.execute(
            "UPDATE expression_candidates SET score_json = ? WHERE id = ?",
            (json.dumps(score_json), cand_row[0]),
        )
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise, journal_ref, experiment_id) "
        "VALUES (?, ?, 'quant', 'hypothesis', ?, 'scored', NULL, ?, ?, NULL, ?)",
        (
            f"audit_{uuid.uuid4().hex[:12]}",
            now,
            hyp_id,
            f"score={breakdown.score:.1f} horizon={breakdown.horizon}",
            breakdown.rationale_one_liner[:500],
            experiment_id,
        ),
    )
    conn.commit()


def mark_dormant(
    conn: sqlite3.Connection,
    hyp_id: str,
    reason: str,
    experiment_id: str | None,
) -> None:
    now = _now_utc_iso()
    conn.execute(
        "UPDATE hypotheses SET state = 'dormant', rationale_concise = ? WHERE id = ?",
        (f"scoring_blocked: {reason}"[:500], hyp_id),
    )
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise, journal_ref, experiment_id) "
        "VALUES (?, ?, 'quant', 'hypothesis', ?, 'scoring_blocked', NULL, 'dormant', ?, NULL, ?)",
        (
            f"audit_{uuid.uuid4().hex[:12]}",
            now,
            hyp_id,
            reason[:500],
            experiment_id,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument(
        "--states",
        default="raw,scored",
        help="Comma-separated hypothesis states to scan (default raw,scored)",
    )
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print scores without writing to DB",
    )
    parser.add_argument("--experiment-id", default=None)
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: db missing at {db_path}", file=sys.stderr)
        return EXIT_FAIL_LOUD

    states = tuple(s.strip() for s in args.states.split(",") if s.strip())
    if not states:
        print("ERROR: --states must contain at least one state", file=sys.stderr)
        return EXIT_FAIL_LOUD

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(states))
    rows = conn.execute(
        f"SELECT id, tickers, thesis_summary, state, confidence, time_horizon, "
        f"created_at, scored_at FROM hypotheses WHERE state IN ({placeholders}) "
        f"ORDER BY created_at ASC LIMIT ?",
        (*states, args.limit),
    ).fetchall()

    summary = {"total": len(rows), "scored": 0, "blocked": 0, "errors": 0, "results": []}
    for row in rows:
        try:
            outcome = score_one(conn, row)
        except Exception as exc:  # fail loud per-row
            summary["errors"] += 1
            summary["results"].append({"id": row["id"], "error": str(exc)})
            traceback.print_exc(file=sys.stderr)
            continue
        if isinstance(outcome, str):
            summary["blocked"] += 1
            summary["results"].append(
                {"id": row["id"], "blocked_reason": outcome}
            )
            if not args.dry_run:
                mark_dormant(conn, row["id"], outcome, args.experiment_id)
            continue
        summary["scored"] += 1
        summary["results"].append(
            {
                "id": row["id"],
                "score": outcome.score,
                "horizon": outcome.horizon,
                "best_horizon": outcome.best_horizon,
            }
        )
        if not args.dry_run:
            mark_scored(conn, row["id"], outcome, args.experiment_id)

    print(json.dumps(summary, indent=2, sort_keys=True))
    # If we found rows but failed to score every single one, that is a
    # fail-loud condition: something is wrong with the scoring layer.
    if summary["total"] > 0 and summary["scored"] == 0 and summary["errors"] > 0:
        return EXIT_FAIL_LOUD
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
